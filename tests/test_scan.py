from __future__ import annotations

import json
import textwrap

import pytest

from wirecheck import cli
from wirecheck.scan import scan, scan_python_text, scan_shell_text


def rules_of(findings):
    return [(f.rule_id, f.line) for f in findings]


def py(src):
    return scan_python_text(textwrap.dedent(src), "x.py")


# -- WCS001 MessageParam ------------------------------------------------------------

def test_messageparam_call_and_subclass():
    found = py("""
        from anthropic.types import MessageParam
        from anthropic.types.beta import BetaMessageParam as BMP
        import anthropic
        m = MessageParam(role="user", content="hi")
        b = BMP(role="user", content="hi")
        c = anthropic.types.MessageParam(role="user", content="x")
        class Mine(MessageParam):
            pass
        ok: MessageParam = {"role": "user", "content": "hi"}
    """)
    assert rules_of(found) == [("WCS001", 5), ("WCS001", 6), ("WCS001", 7), ("WCS001", 8)]


def test_messageparam_from_other_library_is_ignored():
    assert py("from mylib import MessageParam\nMessageParam(x=1)\n") == []


# -- WCS002 X-Api-Key --------------------------------------------------------------

def test_x_api_key_in_extra_or_default_headers():
    found = py("""
        import anthropic
        c = anthropic.Anthropic(default_headers={"X-Api-Key": key})
        c.messages.create(model="m", max_tokens=1, messages=[], extra_headers={"x-api-key": other})
        c.messages.create(model="m", max_tokens=1, messages=[], extra_headers={"Authorization": f"Bearer {other}"})
    """)
    assert rules_of(found) == [("WCS002", 3), ("WCS002", 4)]


# -- WCS003 removed CLI flags -------------------------------------------------------

@pytest.mark.parametrize("line,hit", [
    ('ant --api-key "$KEY" models list', True),
    ("ant models list --api-key=$KEY", True),
    ("  run: ant --auth-token $TOK messages create", True),
    ("ant --api-key-stdin models list", False),
    ("ant --auth-token-stdin models list", False),
    ("mytool --api-key $KEY", False),
    ("important --api-key x", False),
    ("# ant --api-key $KEY (commented out)", False),
])
def test_ant_cli_flags(line, hit):
    found = scan_shell_text(line + "\n", "ci.yml")
    assert bool(found) is hit
    if hit:
        assert found[0].rule_id == "WCS003"


def test_ant_flags_in_python_subprocess_list():
    found = py('import subprocess\nsubprocess.run(["ant", "--api-key", key, "models", "list"])\n')
    assert ("WCS003", 2) in rules_of(found)


# -- WCS004 Text Completions -------------------------------------------------------

def test_text_completions_calls():
    found = py("""
        import anthropic
        client = anthropic.Anthropic()
        client.completions.create(model="claude-2.1", prompt="Human: hi", max_tokens_to_sample=5)
        openai_client.chat.completions.create(model="gpt", messages=[])
        url = "https://api.anthropic.com/v1/complete"
        other = "https://api.openai.com/v1/completions"
    """)
    assert rules_of(found) == [("WCS004", 4), ("WCS004", 6)]


def test_openai_only_file_is_not_flagged():
    assert py("import openai\nclient.completions.create(model='x', prompt='y')\n") == []


# -- WCS005 sampling params --------------------------------------------------------

def test_sampling_params_on_messages_calls():
    found = py("""
        import anthropic
        client.messages.create(model="m", max_tokens=1, messages=[], temperature=0)
        client.beta.messages.stream(model="m", max_tokens=1, messages=[], top_p=0.9)
        client.messages.create(model="m", max_tokens=1, messages=[], extra_body={"temperature": 0.2})
        client.messages.create(model="m", max_tokens=1, messages=[], extra_body=dict(top_k=3))
        client.messages.create(model="m", max_tokens=1, messages=[])
        other.create(temperature=0)
    """)
    assert rules_of(found) == [("WCS005", 3), ("WCS005", 4), ("WCS005", 5), ("WCS005", 6)]


def test_bad_syntax_is_skipped():
    assert py("def (:\n") == []


# -- walking and CLI ---------------------------------------------------------------

@pytest.fixture
def tree(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "llm.py").write_text(
        "import anthropic\nclient.messages.create(model='m', max_tokens=1, messages=[], temperature=0.2)\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "list.sh").write_text('#!/bin/sh\nant --api-key "$KEY" models list\n')
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "skip.py").write_text("from anthropic.types import MessageParam\nMessageParam()\n")
    (tmp_path / "README.md").write_text("ant --api-key x\n")
    return tmp_path


def test_scan_walks_and_skips(tree):
    data = scan([str(tree)], root=str(tree))
    got = sorted((f["path"], f["rule_id"]) for f in data["findings"])
    assert got == [("app/llm.py", "WCS005"), ("scripts/list.sh", "WCS003")]
    assert data["files_scanned"] == 2


def test_cli_exit_codes_and_formats(tree, capsys, monkeypatch):
    monkeypatch.chdir(tree)
    assert cli.main(["scan", "."]) == 1  # WCS003 is an error
    assert "scripts/list.sh:2: WCS003" in capsys.readouterr().out
    assert cli.main(["scan", "app"]) == 0  # only a warning
    capsys.readouterr()
    assert cli.main(["scan", "app", "--fail-on", "warn"]) == 1
    capsys.readouterr()
    assert cli.main(["scan", ".", "--fail-on", "never", "--format", "json", "-o", "s.json"]) == 0
    data = json.loads((tree / "s.json").read_text())
    assert data["kind"] == "scan" and len(data["findings"]) == 2


def test_comment_merges_both_reports(tree, monkeypatch):
    monkeypatch.chdir(tree)
    cli.main(["scan", ".", "--format", "json", "-o", "s.json", "--fail-on", "never"])
    (tree / "p.json").write_text(json.dumps({
        "requests_checked": 3, "sdk_version": "2.3.0", "findings": [{
            "rule_id": "WC001", "severity": "error", "message": "m", "doc": "d", "where": "temperature",
            "api_version": "2026-10-21", "api_version_source": "header",
            "test": "tests/test_a.py::test_b", "call_site": "app/llm.py:2", "url": "u"}]}))
    cli.main(["comment", "--pytest", "p.json", "--scan", "s.json", "-o", "c.md"])
    md = (tree / "c.md").read_text()
    assert md.startswith("<!-- wirecheck-comment -->")
    assert "**2 errors, 1 warning.**" in md
    assert "| WC001 | error | 1 | `tests/test_a.py::test_b` | `app/llm.py:2` | temperature |" in md
    assert "`scripts/list.sh:2`" in md
    assert "3 requests captured in tests" in md


def test_comment_counts_and_pytest_exit(tmp_path, capsys):
    (tmp_path / "s.json").write_text(json.dumps({"files_scanned": 1, "findings": [
        {"rule_id": "WCS003", "severity": "error", "path": "a.sh", "line": 1, "message": "m", "doc": "d"},
        {"rule_id": "WCS005", "severity": "warn", "path": "a.py", "line": 2, "message": "m", "doc": "d"}]}))
    cli.main(["comment", "--scan", str(tmp_path / "s.json"), "-o", str(tmp_path / "c.md"),
              "--pytest-exit", "4", "--counts"])
    assert capsys.readouterr().out == "errors=1\nwarnings=1\n"
    assert "pytest exited with code 4" in (tmp_path / "c.md").read_text()


def test_comment_with_nothing_found(tmp_path):
    out = tmp_path / "c.md"
    (tmp_path / "s.json").write_text(json.dumps({"files_scanned": 4, "findings": []}))
    cli.main(["comment", "--scan", str(tmp_path / "s.json"), "-o", str(out)])
    assert "**No problems found.**" in out.read_text()

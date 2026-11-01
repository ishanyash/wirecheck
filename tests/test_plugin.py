"""End-to-end runs of `pytest --wirecheck` in a throwaway project (pytester)."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("httpx2")

APP = '''
import httpx2

URL = "https://api.anthropic.com/v1/messages"
VERSION = "{version}"

def ok(request):
    return httpx2.Response(200, json={{"ok": True}})

def client():
    return httpx2.Client(transport=httpx2.MockTransport(ok))

def ask(extra=None, headers=None):
    body = {{"model": "claude-sonnet-4-6", "max_tokens": 10, "messages": [{{"role": "user", "content": "hi"}}]}}
    body.update(extra or {{}})
    h = {{"anthropic-version": VERSION}}
    h.update(headers or {{}})
    return client().post(URL, json=body, headers=h)
'''


@pytest.fixture
def project(pytester):
    def make(version="2026-10-21", tests=""):
        pytester.makepyfile(app=APP.format(version=version))
        pytester.makepyfile(test_app="import app\n" + tests)
        return pytester
    return make


def test_temperature_fails_the_test_that_sent_it(project):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0.2})\n\ndef test_good():\n    app.ask()\n")
    result = p.runpytest("--wirecheck")
    result.assert_outcomes(passed=1, failed=1)
    out = result.stdout.str()
    assert "test_app.py::test_bad" in out and "WC001" in out
    assert "app.py:" in out  # call site printed
    assert "2 requests to the Claude API checked: 1 error, 0 warnings" in out


def test_short_summary_is_one_line(project):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0.2, 'top_p': 1})\n")
    result = p.runpytest("--wirecheck", "-rf")
    line = next(l for l in result.outlines if l.startswith("FAILED"))
    assert "WC001 temperature, WC001 top_p" in line
    assert "call site" not in line


def test_xdist_workers_report_to_the_controller(project):
    pytest.importorskip("xdist")
    tests = "".join(
        f"def test_bad_{i}():\n    app.ask({{'temperature': 0.{i}}})\n\ndef test_ok_{i}():\n    app.ask()\n\n" for i in range(4)
    )
    p = project(tests=tests)
    result = p.runpytest("--wirecheck", "-n", "2", "--wirecheck-report=out.json")
    result.assert_outcomes(passed=4, failed=4)
    data = json.loads((p.path / "out.json").read_text())
    assert data["requests_checked"] == 8
    assert len(data["findings"]) == 4
    assert "8 requests to the Claude API checked: 4 errors" in result.stdout.str()


def test_without_the_flag_nothing_happens(project):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0.2})\n")
    p.runpytest().assert_outcomes(passed=1)


def test_old_api_version_only_warns(project):
    p = project(version="2023-06-01", tests="def test_bad():\n    app.ask({'top_p': 0.9})\n")
    result = p.runpytest("--wirecheck")
    result.assert_outcomes(passed=1)
    assert "0 errors, 1 warning" in result.stdout.str()


def test_fail_on_warn_and_never(project):
    p = project(version="2023-06-01", tests="def test_bad():\n    app.ask({'top_p': 0.9})\n")
    p.runpytest("--wirecheck", "--wirecheck-fail-on=warn").assert_outcomes(failed=1)
    p.runpytest("--wirecheck", "--wirecheck-fail-on=never").assert_outcomes(passed=1)


def test_ini_and_env_turn_it_on(project, monkeypatch):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0})\n")
    p.makeini("[pytest]\nwirecheck = true\n")
    p.runpytest().assert_outcomes(failed=1)
    p.makeini("[pytest]\n")
    monkeypatch.setenv("WIRECHECK", "1")
    p.runpytest().assert_outcomes(failed=1)


def test_json_and_markdown_reports(project):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0.2, 'top_k': 5})\n")
    result = p.runpytest("--wirecheck", "--wirecheck-report=out.json", "--wirecheck-markdown=out.md")
    result.assert_outcomes(failed=1)
    data = json.loads((p.path / "out.json").read_text())
    assert data["requests_checked"] == 1
    assert {f["where"] for f in data["findings"]} == {"temperature", "top_k"}
    assert data["findings"][0]["test"] == "test_app.py::test_bad"
    md = (p.path / "out.md").read_text()
    assert "| WC001 | error | 2 |" in md


def test_wirecheck_stub_needs_no_network(project):
    tests = (
        "import httpx2\n"
        "def test_stubbed(wirecheck_stub):\n"
        "    wirecheck_stub['text'] = 'canned'\n"
        "    r = httpx2.Client().post(app.URL, json={'model': 'm', 'messages': []},\n"
        "                             headers={'anthropic-version': '2026-10-21'})\n"
        "    assert r.json()['content'][0]['text'] == 'canned'\n"
    )
    p = project(tests=tests)
    p.runpytest("--wirecheck").assert_outcomes(passed=1)
    p.runpytest().assert_outcomes(passed=1)  # the stub also works without --wirecheck


def test_wirecheck_requests_fixture(project):
    tests = (
        "def test_inspect(wirecheck_requests):\n"
        "    app.ask()\n"
        "    assert len(wirecheck_requests) == 1\n"
        "    assert wirecheck_requests[0].body['model'] == 'claude-sonnet-4-6'\n"
    )
    project(tests=tests).runpytest("--wirecheck").assert_outcomes(passed=1)


def test_skip_marker(project):
    tests = "import pytest\n@pytest.mark.wirecheck_skip\ndef test_bad():\n    app.ask({'temperature': 0.2})\n"
    project(tests=tests).runpytest("--wirecheck").assert_outcomes(passed=1)


def test_request_outside_a_test_still_fails_the_run(project):
    p = project(tests="app.ask({'temperature': 0.2})  # at import time\n\ndef test_fine():\n    pass\n")
    result = p.runpytest("--wirecheck")
    result.assert_outcomes(passed=1)
    assert result.ret == 1
    assert "(outside tests)" in result.stdout.str()


def test_already_failing_test_gets_a_wirecheck_section(project):
    p = project(tests="def test_bad():\n    app.ask({'temperature': 0.2})\n    assert False\n")
    result = p.runpytest("--wirecheck")
    result.assert_outcomes(failed=1)
    assert "wirecheck" in result.stdout.str()


def test_fast_mode_with_frozen_clock(project, monkeypatch):
    tests = "def test_fast():\n    app.ask({'model': 'claude-opus-4-8', 'speed': 'fast'})\n"
    p = project(tests=tests)
    monkeypatch.setenv("WIRECHECK_TODAY", "2026-11-29")
    result = p.runpytest("--wirecheck")
    result.assert_outcomes(passed=1)
    assert "WC005" in result.stdout.str() and "1 warning" in result.stdout.str()
    monkeypatch.setenv("WIRECHECK_TODAY", "2026-11-30")
    p.runpytest("--wirecheck").assert_outcomes(failed=1)


def test_no_requests_prints_a_hint(project):
    p = project(tests="def test_nothing():\n    pass\n")
    result = p.runpytest("--wirecheck")
    assert "No requests were seen" in result.stdout.str()


def test_report_header(project):
    result = project(tests="def test_x():\n    pass\n").runpytest("--wirecheck")
    assert "wirecheck 0.1.0: hooked httpx2" in result.stdout.str()


def test_hook_is_removed_after_the_run(project):
    import httpx2

    before = httpx2.Client.send
    project(tests="def test_x():\n    pass\n").runpytest("--wirecheck")
    assert httpx2.Client.send is before

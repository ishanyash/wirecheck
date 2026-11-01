"""Turn findings into a terminal table, JSON, or a Markdown PR comment."""

from __future__ import annotations

import json
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import __version__
from .rules import RULES

COMMENT_MARKER = "<!-- wirecheck-comment -->"
REPO_URL = "https://github.com/ishanyash/wirecheck"
SCAN_RULE_DOCS: dict[str, str] = {}  # filled by scan.py to avoid an import cycle


@dataclass
class Row:
    rule_id: str
    severity: str
    test: str
    call_site: str
    count: int = 0
    where: list[str] = field(default_factory=list)


def group_findings(findings: Iterable[dict[str, Any]]) -> list[Row]:
    rows: "OrderedDict[tuple, Row]" = OrderedDict()
    for f in findings:
        key = (f["rule_id"], f["severity"], f.get("test") or "(outside tests)", f.get("call_site") or "?")
        row = rows.get(key)
        if row is None:
            row = rows[key] = Row(*key)
        row.count += 1
        if f.get("where") and f["where"] not in row.where:
            row.where.append(f["where"])
    order = {"error": 0, "warn": 1}
    return sorted(rows.values(), key=lambda r: (order.get(r.severity, 2), r.rule_id, r.test, r.call_site))


def _counts(findings: Iterable[dict[str, Any]]) -> tuple[int, int]:
    c = Counter(f["severity"] for f in findings)
    return c.get("error", 0), c.get("warn", 0)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# -- terminal -------------------------------------------------------------------

def render_text(data: dict[str, Any]) -> str:
    findings = data["findings"]
    errors, warns = _counts(findings)
    lines = [
        f"{_plural(data['requests_checked'], 'request')} to the Claude API checked: "
        f"{_plural(errors, 'error')}, {_plural(warns, 'warning')}",
        f"anthropic SDK: {data.get('sdk_version') or 'not installed'}; "
        f"API versions seen: {', '.join(f'{k} x{v}' for k, v in data.get('api_versions', {}).items()) or 'none'}",
    ]
    internal = data.get("internal_errors") or []
    if internal:
        lines.append(
            f"WARNING: WireCheck hit {len(internal)} internal error(s), so some requests may be missing. "
            f"First: {internal[0]}. Please report it at {REPO_URL}/issues"
        )
    if data["requests_checked"] == 0:
        lines.append(
            "No requests were seen. If your tests replace client.messages.create with a mock, no request is "
            "ever built. Mock the HTTP response instead (see the wirecheck_stub fixture)."
        )
    rows = group_findings(findings)
    if rows:
        table = [("RULE", "LEVEL", "COUNT", "TEST", "CALL SITE", "WHAT")]
        for r in rows:
            table.append((r.rule_id, r.severity, str(r.count), r.test, r.call_site, ", ".join(r.where)))
        widths = [max(len(row[i]) for row in table) for i in range(len(table[0]) - 1)]
        lines.append("")
        for row in table:
            cells = [row[i].ljust(widths[i]) for i in range(len(widths))] + [row[-1]]
            lines.append("  ".join(cells).rstrip())
        lines.append("")
        for rule_id in sorted({r.rule_id for r in rows}):
            rule = RULES[rule_id]
            lines.append(f"{rule_id} {rule.name}: {rule.summary}")
            lines.append(f"      {rule.doc}")
    return "\n".join(lines)


def render_test_failure(findings: list[dict[str, Any]]) -> str:
    out = ["WireCheck: this test sent a request that breaks under Claude API 2026-10-21", ""]
    for f in findings:
        out.append(f"  {f['rule_id']} [{f['severity']}] {f['where']}  (api {f['api_version']}, from {f['api_version_source']})")
        out.append(f"      call site: {f.get('call_site') or '?'}")
        out.append(f"      {f['message']}")
        out.append(f"      {f['doc']}")
    return "\n".join(out)


# -- markdown (PR comment and step summary) -------------------------------------

def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(
    pytest_data: dict[str, Any] | None,
    scan_data: dict[str, Any] | None,
    pytest_exit: int | None = None,
) -> str:
    runtime = (pytest_data or {}).get("findings", [])
    static = (scan_data or {}).get("findings", [])
    errors, warns = _counts(list(runtime) + list(static))
    if errors:
        headline = f"**{_plural(errors, 'error')}, {_plural(warns, 'warning')}.** These requests return 400 under API version 2026-10-21."
    elif warns:
        headline = f"**No errors, {_plural(warns, 'warning')}.**"
    else:
        headline = "**No problems found.**"

    parts = [COMMENT_MARKER, "### WireCheck: Claude API 2026-10-21", "", headline, ""]
    facts = []
    if pytest_data is not None:
        facts.append(f"{_plural(pytest_data.get('requests_checked', 0), 'request')} captured in tests")
        if pytest_data.get("sdk_version"):
            facts.append(f"anthropic {pytest_data['sdk_version']}")
    if scan_data is not None:
        facts.append(f"{_plural(scan_data.get('files_scanned', 0), 'file')} scanned")
    if facts:
        parts += ["<sub>" + " · ".join(facts) + "</sub>", ""]
    internal = (pytest_data or {}).get("internal_errors") or []
    if internal:
        parts += [f"> WireCheck hit {len(internal)} internal error(s), so some requests may be missing. "
                  f"First: `{_md(internal[0])}`", ""]
    if pytest_exit not in (None, 0, 1, 5):
        parts += [f"> pytest exited with code {pytest_exit}, so some tests may not have run. Check the job log.", ""]
    elif pytest_data is not None and pytest_data.get("requests_checked", 0) == 0:
        parts += [
            "> Your tests sent no requests to the Claude API. If they replace `client.messages.create` with a mock, "
            "no request is ever built. Mock the HTTP response instead (see the `wirecheck_stub` fixture).",
            "",
        ]

    rows = group_findings(runtime)
    if rows:
        parts += [
            "#### Requests your tests sent",
            "",
            "| Rule | Level | Count | Test | Call site | What |",
            "|---|---|---:|---|---|---|",
        ]
        for r in rows:
            parts.append(
                f"| {r.rule_id} | {r.severity} | {r.count} | `{_md(r.test)}` | `{_md(r.call_site)}` | {_md(', '.join(r.where))} |"
            )
        parts.append("")

    if static:
        parts += ["#### Static scan", "", "| Rule | Level | File | What |", "|---|---|---|---|"]
        for f in sorted(static, key=lambda x: ({"error": 0, "warn": 1}.get(x["severity"], 2), x["path"], x["line"])):
            parts.append(f"| {f['rule_id']} | {f['severity']} | `{_md(f['path'])}:{f['line']}` | {_md(f['message'])} |")
        parts.append("")

    used = sorted({f["rule_id"] for f in list(runtime) + list(static)})
    if used:
        parts += ["<details><summary>What the rules mean</summary>", ""]
        for rule_id in used:
            if rule_id in RULES:
                rule = RULES[rule_id]
                parts.append(f"- **{rule_id}** {_md(rule.summary)} [Docs]({rule.doc})")
            elif rule_id in SCAN_RULE_DOCS:
                parts.append(f"- **{rule_id}** {SCAN_RULE_DOCS[rule_id]}")
        parts += ["", "</details>", ""]

    parts.append(f"<sub>[WireCheck]({REPO_URL}) v{__version__}</sub>")
    return "\n".join(parts) + "\n"


def dump_json(data: dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")

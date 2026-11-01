"""Runtime rules for Claude API version 2026-10-21.

Every rule here is taken from the official migration guide or release notes:
https://platform.claude.com/docs/en/api/migration-2026-10-21
https://platform.claude.com/docs/en/release-notes/overview
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from . import clock, versions

MIGRATION_DOC = "https://platform.claude.com/docs/en/api/migration-2026-10-21"
RELEASE_NOTES = "https://platform.claude.com/docs/en/release-notes/overview"

FAST_MODE_MODEL = "claude-opus-4-8"
FAST_MODE_ERROR_FROM = _dt.date(2026, 11, 30)

ERROR = "error"
WARN = "warn"


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    summary: str
    doc: str


RULES: dict[str, Rule] = {
    r.id: r
    for r in (
        Rule(
            "WC001",
            "sampling-params",
            "temperature, top_p and top_k return 400 under 2026-10-21, even at their default values.",
            MIGRATION_DOC + "#messages-api-changes",
        ),
        Rule(
            "WC002",
            "document-in-assistant",
            "A document block placed directly in an assistant message returns 400 under 2026-10-21. "
            "Send documents in user messages.",
            MIGRATION_DOC + "#messages-api-changes",
        ),
        Rule(
            "WC003",
            "citations-in-user",
            "A text block with a non-empty citations array in a user message (including inside "
            "search_result blocks) returns 400 under 2026-10-21.",
            MIGRATION_DOC + "#messages-api-changes",
        ),
        Rule(
            "WC004",
            "x-api-key-header",
            "Your code sets X-Api-Key. SDK 2.x sends the key as Authorization: Bearer, so a custom "
            "X-Api-Key no longer replaces the client key (both are sent) and X-Api-Key: omit no "
            "longer removes it. The API still accepts either header.",
            MIGRATION_DOC + "#sdk-authorization-bearer",
        ),
        Rule(
            "WC005",
            "opus-4-8-fast-mode",
            'Fast mode for claude-opus-4-8 is deprecated. From 2026-11-30, requests with speed "fast" '
            "return an error. Move to Claude Opus 5.5.",
            RELEASE_NOTES,
        ),
    )
}


@dataclass
class Captured:
    """One outgoing request, as it left the HTTP client."""

    method: str
    url: str
    path: str
    headers: dict[str, str]  # lower-case names, secret values redacted
    body: Any = None
    test: str | None = None
    call_site: str | None = None
    library: str = "httpx2"


@dataclass
class Finding:
    rule_id: str
    severity: str
    message: str
    doc: str
    where: str
    api_version: str
    api_version_source: str
    test: str | None = None
    call_site: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Context:
    sdk_version: str | None = None
    today: _dt.date = field(default_factory=clock.today)


SAMPLING_PARAMS = ("temperature", "top_p", "top_k")
MESSAGE_PATH_SUFFIXES = ("/v1/messages", "/v1/messages/count_tokens")
BATCH_PATH_SUFFIX = "/v1/messages/batches"


def _short(value: Any) -> str:
    try:
        text = json.dumps(value)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def _iter_params(path: str, method: str, body: Any) -> Iterator[tuple[str, dict]]:
    """Yield (prefix, params) for each Messages request inside this HTTP request."""
    if method.upper() != "POST" or not isinstance(body, dict):
        return
    clean = path.rstrip("/")
    if clean.endswith(MESSAGE_PATH_SUFFIXES):
        yield "", body
    elif clean.endswith(BATCH_PATH_SUFFIX):
        for i, item in enumerate(body.get("requests") or []):
            if isinstance(item, dict) and isinstance(item.get("params"), dict):
                yield f"requests[{i}].params.", item["params"]


def _iter_user_text_blocks(content: Any, prefix: str) -> Iterator[tuple[str, dict]]:
    """Text blocks in a user message that the guide covers: top level and inside search_result."""
    if not isinstance(content, list):
        return
    for j, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        here = f"{prefix}[{j}]"
        kind = block.get("type")
        if kind == "text":
            yield here, block
        elif kind == "search_result":
            yield from _iter_nested_text(block.get("content"), here + ".content")
        elif kind == "tool_result":
            # Only search_result blocks inside a tool result; the guide does not cover bare text there.
            inner = block.get("content")
            if isinstance(inner, list):
                for k, sub in enumerate(inner):
                    if isinstance(sub, dict) and sub.get("type") == "search_result":
                        yield from _iter_nested_text(sub.get("content"), f"{here}.content[{k}].content")


def _iter_nested_text(content: Any, prefix: str) -> Iterator[tuple[str, dict]]:
    if not isinstance(content, list):
        return
    for k, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "text":
            yield f"{prefix}[{k}]", block


def check(captured: Captured, ctx: Context | None = None) -> list[Finding]:
    ctx = ctx or Context()
    res = versions.resolve(captured.headers, ctx.sdk_version)
    findings: list[Finding] = []

    def add(rule_id: str, severity: str, where: str, detail: str) -> None:
        rule = RULES[rule_id]
        findings.append(
            Finding(
                rule_id=rule_id,
                severity=severity,
                message=f"{detail} {rule.summary}".strip(),
                doc=rule.doc,
                where=where,
                api_version=res.api_version,
                api_version_source=res.source,
                test=captured.test,
                call_site=captured.call_site,
                url=captured.url,
            )
        )

    # Rules that return 400 under 2026-10-21. Errors when the request uses that version,
    # warnings when it still uses 2023-06-01 (it works today, it breaks on upgrade).
    breaking = ERROR if res.is_new else WARN
    later = "" if res.is_new else "(Works today on 2023-06-01; breaks when you move to anthropic>=2.0.0.)"

    for prefix, params in _iter_params(captured.path, captured.method, captured.body):
        for name in SAMPLING_PARAMS:
            if name in params:
                add("WC001", breaking, prefix + name, f"Sends {name}={_short(params[name])}. {later}")

        messages = params.get("messages")
        if isinstance(messages, list):
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role")
                content = msg.get("content")
                base = f"{prefix}messages[{i}].content"
                if role == "assistant" and isinstance(content, list):
                    for j, block in enumerate(content):
                        if isinstance(block, dict) and block.get("type") == "document":
                            add("WC002", breaking, f"{base}[{j}]", f"Assistant message has a document block. {later}")
                elif role == "user":
                    for where, block in _iter_user_text_blocks(content, base):
                        cites = block.get("citations")
                        if isinstance(cites, list) and cites:
                            add("WC003", breaking, where + ".citations",
                                f"User text block has {len(cites)} citation(s). {later}")

        model = params.get("model")
        if isinstance(model, str) and model.startswith(FAST_MODE_MODEL) and params.get("speed") == "fast":
            if ctx.today >= FAST_MODE_ERROR_FROM:
                add("WC005", ERROR, prefix + "speed", f'model={model} with speed="fast" now returns an error.')
            else:
                days = (FAST_MODE_ERROR_FROM - ctx.today).days
                add("WC005", WARN, prefix + "speed",
                    f'model={model} with speed="fast" will return an error in {days} day(s).')

    # X-Api-Key: SDK 2.x never sets it on api.anthropic.com, so under 2026-10-21 it came from your code.
    if res.is_new and "x-api-key" in captured.headers:
        detail = "Request carries X-Api-Key"
        if "authorization" in captured.headers:
            detail += " and Authorization, so two credentials are sent."
        else:
            detail += "."
        add("WC004", WARN, "headers.x-api-key", detail)

    return findings

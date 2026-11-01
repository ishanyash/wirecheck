"""``wirecheck scan``: things you can see in the source without running it.

The runtime plugin is the real check. This scanner catches what never reaches the
wire in tests (CLI scripts, code paths with no test) and SDK 2.x changes that fail
before a request is built.
"""

from __future__ import annotations

import ast
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Iterator

from . import __version__, report
from .rules import MIGRATION_DOC

SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".env", "node_modules", "__pycache__", ".tox", ".nox",
    "build", "dist", "site-packages", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".eggs",
}
SHELL_SUFFIXES = (".sh", ".bash", ".zsh", ".yml", ".yaml", ".mk", ".ps1")
SHELL_NAMES = ("Makefile", "makefile", "GNUmakefile", "Justfile", "justfile", "Procfile")
MAX_BYTES = 2_000_000


@dataclass(frozen=True)
class ScanRule:
    id: str
    severity: str
    summary: str
    doc: str


SCAN_RULES: dict[str, ScanRule] = {
    r.id: r
    for r in (
        ScanRule(
            "WCS001", "error",
            "Calling or subclassing MessageParam / BetaMessageParam raises TypeError in anthropic 2.x. Use a dict literal.",
            MIGRATION_DOC + "#sdk-message-param",
        ),
        ScanRule(
            "WCS002", "warn",
            "extra_headers / default_headers set X-Api-Key. In anthropic 2.x this no longer replaces or removes "
            "the client key; use Authorization instead.",
            MIGRATION_DOC + "#sdk-authorization-bearer",
        ),
        ScanRule(
            "WCS003", "error",
            "The ant CLI 2.0.0 removed --api-key and --auth-token. Use ANTHROPIC_API_KEY, --api-key-stdin, "
            "ANTHROPIC_AUTH_TOKEN or --auth-token-stdin.",
            MIGRATION_DOC + "#sdk-single-language",
        ),
        ScanRule(
            "WCS004", "error",
            "Text Completions is removed from the SDKs (client.completions is gone in anthropic 2.x). Use the Messages API.",
            MIGRATION_DOC + "#sdk-text-completions",
        ),
        ScanRule(
            "WCS005", "warn",
            "temperature / top_p / top_k passed to a Messages call. Returns 400 under 2026-10-21. "
            "Static hint only: run pytest --wirecheck to see what is really sent.",
            MIGRATION_DOC + "#messages-api-changes",
        ),
    )
}
report.SCAN_RULE_DOCS.update({k: f"{v.summary} [Docs]({v.doc})" for k, v in SCAN_RULES.items()})


@dataclass
class ScanFinding:
    rule_id: str
    severity: str
    path: str
    line: int
    message: str
    doc: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finding(rule_id: str, path: str, line: int, detail: str) -> ScanFinding:
    rule = SCAN_RULES[rule_id]
    return ScanFinding(rule_id, rule.severity, path, line, f"{detail} {rule.summary}".strip(), rule.doc)


# -- shell-like files -------------------------------------------------------------

ANT_FLAG_RE = re.compile(
    r"""(?:^|[\s;&|(`'"])ant(?:\s+[^\n#]*?)?\s--(api-key|auth-token)(?=[\s=]|$)""",
    re.MULTILINE,
)
COMPLETE_PATH_RE = re.compile(r"/v1/complete(?![\w/-])")


def scan_shell_text(text: str, path: str) -> list[ScanFinding]:
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        for m in ANT_FLAG_RE.finditer(line):
            out.append(_finding("WCS003", path, lineno, f"`ant ... --{m.group(1)}`."))
        if COMPLETE_PATH_RE.search(line):
            out.append(_finding("WCS004", path, lineno, "Calls the /v1/complete endpoint."))
    return out


# -- python files -----------------------------------------------------------------

MESSAGE_METHODS = {"create", "stream", "parse", "count_tokens"}
SAMPLING = ("temperature", "top_p", "top_k")


def _attr_chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        parts.append("()")
    return list(reversed(parts))


def _dict_keys(node: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    if isinstance(node, ast.Dict):
        for k, v in zip(node.keys, node.values):
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                yield k.value, v
    elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        for kw in node.keywords:
            if kw.arg:
                yield kw.arg, kw.value


class _PyVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.findings: list[ScanFinding] = []
        self.imports_anthropic = False
        self.param_names: set[str] = set()  # local names bound to MessageParam / BetaMessageParam

    def add(self, rule_id: str, node: ast.AST, detail: str) -> None:
        self.findings.append(_finding(rule_id, self.path, getattr(node, "lineno", 0), detail))

    def visit_Import(self, node: ast.Import) -> None:
        if any(a.name == "anthropic" or a.name.startswith("anthropic.") for a in node.names):
            self.imports_anthropic = True
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if mod == "anthropic" or mod.startswith("anthropic."):
            self.imports_anthropic = True
            for a in node.names:
                if a.name in ("MessageParam", "BetaMessageParam"):
                    self.param_names.add(a.asname or a.name)
        self.generic_visit(node)

    def _is_param_ref(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.param_names
        if isinstance(node, ast.Attribute) and node.attr in ("MessageParam", "BetaMessageParam"):
            return "anthropic" in _attr_chain(node) or self.imports_anthropic
        return False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for base in node.bases:
            if self._is_param_ref(base):
                self.add("WCS001", node, f"class {node.name} subclasses MessageParam.")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        chain = _attr_chain(func)

        if self._is_param_ref(func):
            self.add("WCS001", node, f"`{'.'.join(chain) or 'MessageParam'}(...)` call.")

        # Text Completions: <client>.completions.create(...) but not OpenAI's chat.completions
        if (
            self.imports_anthropic
            and len(chain) >= 3
            and chain[-1] in ("create", "stream")
            and chain[-2] == "completions"
            and chain[-3] != "chat"
        ):
            self.add("WCS004", node, f"`{'.'.join(chain)}(...)`.")

        for kw in node.keywords:
            # X-Api-Key in extra_headers / default_headers
            if kw.arg in ("extra_headers", "default_headers"):
                for key, _ in _dict_keys(kw.value):
                    if key.lower() == "x-api-key":
                        self.add("WCS002", kw.value, f"`{kw.arg}` sets {key!r}.")

        # sampling params on Messages calls, as kwargs or inside extra_body
        if len(chain) >= 2 and chain[-1] in MESSAGE_METHODS and "messages" in chain[:-1]:
            for kw in node.keywords:
                if kw.arg in SAMPLING:
                    self.add("WCS005", kw, f"`{kw.arg}=` passed to `{'.'.join(chain)}`.")
                elif kw.arg == "extra_body":
                    for key, _ in _dict_keys(kw.value):
                        if key in SAMPLING:
                            self.add("WCS005", kw.value, f"`{key}` inside extra_body of `{'.'.join(chain)}`.")

        # ant CLI flags in subprocess argument lists
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                strs = [e.value for e in arg.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
                if strs and os.path.basename(strs[0]) == "ant":
                    for flag in ("--api-key", "--auth-token"):
                        if flag in strs:
                            self.add("WCS003", arg, f"`{flag}` in an ant command.")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and ("ant" in node.value or "/v1/complete" in node.value):
            for f in scan_shell_text(node.value, self.path):
                f.line = node.lineno + f.line - 1
                self.findings.append(f)


def scan_python_text(text: str, path: str) -> list[ScanFinding]:
    try:
        tree = ast.parse(text, filename=path)
    except (SyntaxError, ValueError):
        return []
    visitor = _PyVisitor(path)
    # first pass for imports so order in the file does not matter
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            visitor.visit(node)
    visitor.findings.clear()
    visitor.visit(tree)
    # de-duplicate (imports were visited twice)
    seen, unique = set(), []
    for f in visitor.findings:
        key = (f.rule_id, f.path, f.line, f.message)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


# -- walking ----------------------------------------------------------------------

def iter_files(paths: Iterable[str]) -> Iterator[str]:
    for root in paths:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info"))
            for name in sorted(filenames):
                yield os.path.join(dirpath, name)


def _kind(path: str) -> str | None:
    name = os.path.basename(path)
    if name.endswith((".py", ".pyi")):
        return "python"
    if name.endswith(SHELL_SUFFIXES) or name in SHELL_NAMES or name.startswith("Dockerfile"):
        return "shell"
    return None


def scan(paths: Iterable[str], root: str | None = None) -> dict[str, Any]:
    root = root or os.getcwd()
    findings: list[ScanFinding] = []
    files = 0
    for path in iter_files(paths):
        kind = _kind(path)
        if kind is None:
            continue
        try:
            if os.path.getsize(path) > MAX_BYTES:
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        files += 1
        shown = os.path.relpath(path, root).replace(os.sep, "/")
        if shown.startswith(".."):
            shown = path
        if kind == "python":
            findings.extend(scan_python_text(text, shown))
        else:
            findings.extend(scan_shell_text(text, shown))
    return {
        "tool": "wirecheck",
        "version": __version__,
        "kind": "scan",
        "files_scanned": files,
        "findings": [f.to_dict() for f in findings],
    }


def render_text(data: dict[str, Any]) -> str:
    findings = data["findings"]
    errors = sum(1 for f in findings if f["severity"] == "error")
    warns = sum(1 for f in findings if f["severity"] == "warn")
    def n(count: int, word: str) -> str:
        return f"{count} {word}{'' if count == 1 else 's'}"

    lines = [f"wirecheck scan: {n(data['files_scanned'], 'file')}, {n(errors, 'error')}, {n(warns, 'warning')}"]
    for f in sorted(findings, key=lambda x: (x["path"], x["line"], x["rule_id"])):
        lines.append(f"{f['path']}:{f['line']}: {f['rule_id']} [{f['severity']}] {f['message']}")
    used = sorted({f["rule_id"] for f in findings})
    if used:
        lines.append("")
        lines.extend(f"{r}: {SCAN_RULES[r].doc}" for r in used)
    return "\n".join(lines)

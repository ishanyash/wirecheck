"""pytest plugin: ``pytest --wirecheck``."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from collections import Counter
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

import pytest

from . import __version__, clock, report, rules, versions
from .capture import Recorder
from .rules import Captured

STATE_KEY = pytest.StashKey["WireCheck"]()
FAIL_ON = ("error", "warn", "never")


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("wirecheck", "Claude API request checks")
    group.addoption(
        "--wirecheck",
        action="store_true",
        default=None,
        help="Capture requests to the Claude API and check them against API version 2026-10-21.",
    )
    group.addoption(
        "--wirecheck-base-url",
        action="append",
        default=[],
        metavar="URL",
        help="Extra base URL to capture (for gateways and proxies). Repeatable. "
        "api.anthropic.com and $ANTHROPIC_BASE_URL are always captured.",
    )
    group.addoption(
        "--wirecheck-fail-on",
        choices=FAIL_ON,
        default=None,
        help="Fail tests on 'error' findings (default), on any finding ('warn'), or 'never'.",
    )
    group.addoption("--wirecheck-report", metavar="PATH", default=None, help="Write findings as JSON.")
    group.addoption("--wirecheck-markdown", metavar="PATH", default=None, help="Write findings as Markdown.")
    parser.addini("wirecheck", type="bool", default=False, help="Turn WireCheck on for every run.")
    parser.addini("wirecheck_base_urls", type="linelist", default=[], help="Extra base URLs to capture.")
    parser.addini("wirecheck_fail_on", default="error", help="error, warn or never.")


def _enabled(config: pytest.Config) -> bool:
    flag = config.getoption("wirecheck")
    if flag is not None:
        return bool(flag)
    if os.environ.get("WIRECHECK", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(config.getini("wirecheck"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "wirecheck_skip: do not check Claude API requests made by this test")
    if not _enabled(config):
        return
    state = WireCheck(config)
    config.stash[STATE_KEY] = state
    config.pluginmanager.register(state, "wirecheck-state")
    state.start()


def pytest_unconfigure(config: pytest.Config) -> None:
    state = config.stash.get(STATE_KEY, None)
    if state is not None:
        state.stop()


class CapturedView(Sequence):
    """Live, read-only list of the requests one test has sent so far."""

    def __init__(self, state: "WireCheck | None", nodeid: str) -> None:
        self._state = state
        self._nodeid = nodeid

    def _items(self) -> list[Captured]:
        if self._state is None:
            return []
        return [c for c in self._state.captured if c.test == self._nodeid]

    def __getitem__(self, index: Any) -> Any:
        return self._items()[index]

    def __len__(self) -> int:
        return len(self._items())

    def __repr__(self) -> str:
        return f"CapturedView({self._items()!r})"


@pytest.fixture
def wirecheck_requests(request: pytest.FixtureRequest) -> CapturedView:
    """Requests to the Claude API captured during this test (always empty if WireCheck is off)."""
    return CapturedView(request.config.stash.get(STATE_KEY, None), request.node.nodeid)


@pytest.fixture
def wirecheck_stub(request: pytest.FixtureRequest) -> Any:
    """Answer every Claude API request in this test with a canned 200 reply, without the network.

    This mocks the response, not the client, so WireCheck still sees what your code sends.
    Works with or without --wirecheck. Non-streaming calls only.
    """
    state = request.config.stash.get(STATE_KEY, None)
    own: Recorder | None = None
    if state is None:
        own = Recorder(on_capture=lambda c: None)
        own.install()
        recorder = own
    else:
        recorder = state.recorder
    stub = {"text": "stub reply"}
    recorder.stub = stub
    try:
        yield stub
    finally:
        recorder.stub = None
        if own is not None:
            own.uninstall()


class FailureRepr:
    """Test failure text with a one-line summary for pytest's short test summary."""

    def __init__(self, text: str, summary: str) -> None:
        self.text = text
        self.reprcrash = SimpleNamespace(message=summary, path="", lineno=0)

    def toterminal(self, tw: Any) -> None:
        for line in self.text.splitlines():
            tw.line(line)

    def __str__(self) -> str:
        return self.text


def _one_line(findings: list[rules.Finding]) -> str:
    parts = []
    for f in findings:
        item = f"{f.rule_id} {f.where}"
        if item not in parts:
            parts.append(item)
    shown = ", ".join(parts[:3]) + (f" and {len(parts) - 3} more" if len(parts) > 3 else "")
    return f"WireCheck: {shown} (breaks under Claude API 2026-10-21)"


class WireCheck:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        workerinput = getattr(config, "workerinput", None)
        self.worker_id: str | None = workerinput.get("workerid") if workerinput else None
        if workerinput:
            self.share_dir: str | None = workerinput.get("wirecheck_dir")
            self.own_share_dir = False
        else:
            self.share_dir = tempfile.mkdtemp(prefix="wirecheck-")
            self.own_share_dir = True
        self.merged: dict[str, Any] | None = None
        base_urls = list(config.getoption("wirecheck_base_url") or []) + list(config.getini("wirecheck_base_urls"))
        env_base = os.environ.get("ANTHROPIC_BASE_URL")
        if env_base:
            base_urls.append(env_base)
        fail_on = config.getoption("wirecheck_fail_on") or config.getini("wirecheck_fail_on") or "error"
        if fail_on not in FAIL_ON:
            raise pytest.UsageError(f"wirecheck_fail_on must be one of {', '.join(FAIL_ON)}, not {fail_on!r}")
        self.fail_on = fail_on
        self.sdk_version = versions.installed_sdk_version()
        self.recorder = Recorder(self._on_capture, base_urls=base_urls, root=str(config.rootpath))
        self.captured: list[Captured] = []
        self.findings: list[rules.Finding] = []
        self.pending: dict[str, list[rules.Finding]] = {}
        self.skip_tests: set[str] = set()

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> None:
        self.recorder.install()

    def stop(self) -> None:
        self.recorder.uninstall()
        if self.own_share_dir and self.share_dir:
            shutil.rmtree(self.share_dir, ignore_errors=True)

    @pytest.hookimpl(optionalhook=True)
    def pytest_configure_node(self, node: Any) -> None:
        """pytest-xdist: tell each worker where to leave its findings."""
        if self.share_dir:
            node.workerinput["wirecheck_dir"] = self.share_dir

    def _on_capture(self, captured: Captured) -> None:
        if captured.test in self.skip_tests:
            return
        self.captured.append(captured)
        ctx = rules.Context(sdk_version=self.sdk_version, today=clock.today())
        found = rules.check(captured, ctx)
        self.findings.extend(found)
        if captured.test:
            self.pending.setdefault(captured.test, []).extend(found)

    def _fails(self, finding: rules.Finding) -> bool:
        if self.fail_on == "never":
            return False
        if self.fail_on == "warn":
            return True
        return finding.severity == rules.ERROR

    # -- pytest hooks ----------------------------------------------------------
    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item, nextitem: Any) -> Any:
        if item.get_closest_marker("wirecheck_skip"):
            self.skip_tests.add(item.nodeid)
        self.recorder.current_test = item.nodeid
        try:
            return (yield)
        finally:
            self.recorder.current_test = None

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_makereport(self, item: pytest.Item, call: pytest.CallInfo) -> Any:
        rep = yield
        new = self.pending.pop(item.nodeid, [])
        failing = [f for f in new if self._fails(f)]
        if failing:
            text = report.render_test_failure([f.to_dict() for f in failing])
            if rep.passed:
                rep.outcome = "failed"
                rep.longrepr = FailureRepr(text, _one_line(failing))
            else:
                rep.sections.append(("wirecheck", text))
        return rep

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        if self.worker_id:
            # pytest-xdist worker: hand the findings to the controller, which reports them.
            if self.share_dir and os.path.isdir(self.share_dir):
                report.dump_json(self.data(), os.path.join(self.share_dir, f"worker-{self.worker_id}.json"))
            return
        data = self.merged = self._merge_workers(self.data())
        outside = [f for f in data["findings"] if not f.get("test") and self._fails_dict(f)]
        if outside and session.exitstatus == 0:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
        path = self.config.getoption("wirecheck_report")
        if path:
            report.dump_json(data, path)
        md = self.config.getoption("wirecheck_markdown")
        if md:
            with open(md, "w", encoding="utf-8") as fh:
                fh.write(report.render_markdown(data, None))

    def pytest_report_header(self, config: pytest.Config) -> str:
        libs = ", ".join(self.recorder.libraries) or "none (install httpx2 or httpx)"
        sdk = self.sdk_version or "not installed"
        return f"wirecheck {__version__}: hooked {libs}; anthropic SDK {sdk}; fail on {self.fail_on}"

    def pytest_terminal_summary(self, terminalreporter: Any) -> None:
        if self.worker_id:
            return
        terminalreporter.write_sep("=", "wirecheck")
        terminalreporter.write_line(report.render_text(self.merged or self.data()))

    def _fails_dict(self, finding: dict[str, Any]) -> bool:
        if self.fail_on == "never":
            return False
        return self.fail_on == "warn" or finding.get("severity") == rules.ERROR

    def _merge_workers(self, own: dict[str, Any]) -> dict[str, Any]:
        if not self.share_dir or not os.path.isdir(self.share_dir):
            return own
        merged = dict(own, findings=list(own["findings"]), api_versions=Counter(own["api_versions"]))
        for name in sorted(os.listdir(self.share_dir)):
            if not (name.startswith("worker-") and name.endswith(".json")):
                continue
            try:
                with open(os.path.join(self.share_dir, name), encoding="utf-8") as fh:
                    part = json.load(fh)
            except (OSError, ValueError):
                continue
            merged["requests_checked"] += part.get("requests_checked", 0)
            merged["api_versions"].update(part.get("api_versions", {}))
            merged["findings"].extend(part.get("findings", []))
            merged["sdk_version"] = merged.get("sdk_version") or part.get("sdk_version")
        merged["api_versions"] = dict(merged["api_versions"])
        return merged

    def data(self) -> dict[str, Any]:
        versions_seen = Counter(
            versions.resolve(c.headers, self.sdk_version).api_version for c in self.captured
        )
        return {
            "tool": "wirecheck",
            "version": __version__,
            "kind": "pytest",
            "requests_checked": len(self.captured),
            "sdk_version": self.sdk_version,
            "api_versions": dict(versions_seen),
            "fail_on": self.fail_on,
            "findings": [f.to_dict() for f in self.findings],
        }

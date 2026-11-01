"""Capture every request that leaves httpx / httpx2 for the Claude API.

The hook wraps ``Client.send`` and ``AsyncClient.send`` at class level, so it sees
the final request after the SDK has merged ``extra_body``, ``extra_headers`` and
client defaults, and it sees it even when the test swaps in a mock transport.

anthropic>=1.x uses httpx2; older SDKs use httpx. Both are hooked when importable.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import sys
import sysconfig
import threading
from typing import Any, Callable

from .rules import Captured

DEFAULT_HOSTS = ("api.anthropic.com",)
LIBRARIES = ("httpx2", "httpx")
REDACT = {"x-api-key", "authorization", "proxy-authorization", "cookie"}

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_DIRS = tuple(
    os.path.normcase(os.path.abspath(p))
    for p in {
        sysconfig.get_paths().get("stdlib"),
        sysconfig.get_paths().get("platstdlib"),
        sysconfig.get_paths().get("purelib"),
        sysconfig.get_paths().get("platlib"),
    }
    if p
)


def _is_library_file(filename: str) -> bool:
    if not filename or filename.startswith("<"):
        return True
    path = os.path.normcase(os.path.abspath(filename))
    if path.startswith(os.path.normcase(_THIS_DIR) + os.sep):
        return True
    if os.sep + "site-packages" + os.sep in path or os.sep + "dist-packages" + os.sep in path:
        return True
    return path.startswith(_LIB_DIRS)


def find_call_site(root: str | None = None, skip: int = 2) -> str | None:
    """File and line of the innermost frame that belongs to the user's code."""
    try:
        frame = sys._getframe(skip)
    except ValueError:
        return None
    while frame is not None:
        filename = frame.f_code.co_filename
        if not _is_library_file(filename):
            shown = filename
            if root:
                try:
                    rel = os.path.relpath(filename, root)
                    if not rel.startswith(".."):
                        shown = rel
                except ValueError:
                    pass
            return f"{shown.replace(os.sep, '/')}:{frame.f_lineno}"
        frame = frame.f_back
    return None


def _normalise_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = "https://" + url
    return url


class Recorder:
    """Holds the hook state. One active recorder at a time."""

    def __init__(
        self,
        on_capture: Callable[[Captured], None],
        base_urls: list[str] | None = None,
        root: str | None = None,
    ) -> None:
        self.on_capture = on_capture
        self.base_urls = [_normalise_base(u) for u in (base_urls or []) if u and u.strip()]
        self.hosts = set(DEFAULT_HOSTS)
        self.root = root
        self.current_test: str | None = None
        self.stub: dict[str, Any] | None = None  # set by the wirecheck_stub fixture
        self._originals: list[tuple[type, str, Any]] = []
        self._lock = threading.Lock()
        self.libraries: list[str] = []

    # -- matching -----------------------------------------------------------
    def matches(self, url: Any) -> bool:
        host = getattr(url, "host", None)
        if host in self.hosts:
            return True
        text = str(url)
        return any(text == base or text.startswith(base + "/") for base in self.base_urls)

    # -- observing ----------------------------------------------------------
    def observe(self, request: Any, library: str) -> None:
        if not self.matches(request.url):
            return
        headers: dict[str, str] = {}
        for name, value in request.headers.items():
            key = name.lower()
            headers[key] = "<redacted>" if key in REDACT else value
        body: Any = None
        try:
            raw = request.content  # raises if the body is an unread stream
        except Exception:
            raw = None
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                body = None
        captured = Captured(
            method=request.method,
            url=str(request.url),
            path=request.url.path,
            headers=headers,
            body=body,
            test=self.current_test,
            call_site=find_call_site(self.root, skip=3),
            library=library,
        )
        with self._lock:
            self.on_capture(captured)

    def stub_response(self, request: Any, module: Any) -> Any | None:
        if self.stub is None or not self.matches(request.url):
            return None
        path = request.url.path.rstrip("/")
        if path.endswith("/v1/messages/count_tokens"):
            payload: Any = {"input_tokens": 1}
        elif path.endswith("/v1/messages"):
            model = "stub"
            try:
                model = json.loads(request.content).get("model", model)
            except Exception:
                pass
            payload = {
                "id": "msg_wirecheck_stub",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": self.stub.get("text", "stub reply")}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        else:
            payload = {}
        return module.Response(200, json=payload, request=request)

    # -- install / uninstall -------------------------------------------------
    def install(self) -> None:
        for name in LIBRARIES:
            try:
                module = importlib.import_module(name)
            except Exception:
                continue
            self._patch(module, name)
            self.libraries.append(name)

    def _patch(self, module: Any, name: str) -> None:
        recorder = self
        sync_orig = module.Client.send
        async_orig = module.AsyncClient.send

        @functools.wraps(sync_orig)
        def send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                recorder.observe(request, name)
                stubbed = recorder.stub_response(request, module)
            except Exception:  # never break the user's test because of us
                stubbed = None
            if stubbed is not None:
                return stubbed
            return sync_orig(client, request, *args, **kwargs)

        @functools.wraps(async_orig)
        async def asend(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                recorder.observe(request, name)
                stubbed = recorder.stub_response(request, module)
            except Exception:
                stubbed = None
            if stubbed is not None:
                return stubbed
            return await async_orig(client, request, *args, **kwargs)

        send.__wirecheck__ = True  # type: ignore[attr-defined]
        asend.__wirecheck__ = True  # type: ignore[attr-defined]
        self._originals.append((module.Client, "send", sync_orig))
        self._originals.append((module.AsyncClient, "send", async_orig))
        module.Client.send = send
        module.AsyncClient.send = asend

    def uninstall(self) -> None:
        while self._originals:
            cls, attr, orig = self._originals.pop()
            setattr(cls, attr, orig)
        self.libraries.clear()

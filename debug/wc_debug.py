import sys

import httpx2
import pytest

from wirecheck import capture


def _log(msg):
    print(f"[wc-debug] {msg}", file=sys.stderr, flush=True)


_orig_observe = capture.Recorder.observe


def observe(self, request, library):
    _log(f"observe {library} {request.method} {request.url} host={request.url.host!r} hosts={self.hosts} bases={self.base_urls} match={self.matches(request.url)}")
    try:
        return _orig_observe(self, request, library)
    except Exception as exc:  # show what the hook swallows
        _log(f"observe raised {exc!r}")
        raise


capture.Recorder.observe = observe


def pytest_configure(config):
    _log(f"configure: AsyncClient.send={httpx2.AsyncClient.send!r} wirecheck={getattr(httpx2.AsyncClient.send, '__wirecheck__', False)} file={httpx2.__file__}")


@pytest.fixture(autouse=True)
def _wc_debug(request):
    s = httpx2.AsyncClient.send
    _log(f"test {request.node.nodeid}: AsyncClient.send wirecheck={getattr(s, '__wirecheck__', False)} {s!r}")
    yield
    s = httpx2.AsyncClient.send
    _log(f"after {request.node.nodeid}: AsyncClient.send wirecheck={getattr(s, '__wirecheck__', False)} {s!r}")

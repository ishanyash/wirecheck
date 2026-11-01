"""The hook itself, driven with plain httpx2 / httpx clients (no SDK, no network)."""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from wirecheck.capture import Recorder

httpx2 = pytest.importorskip("httpx2")

URL = "https://api.anthropic.com/v1/messages"


def handler(request):
    return httpx2.Response(200, json={"ok": True})


def no_network(request):  # a transport that proves nothing left the process
    raise AssertionError("network must not be used")


@pytest.fixture
def recorder():
    seen = []
    rec = Recorder(seen.append, base_urls=["https://gateway.example.com/anthropic"])
    rec.install()
    rec.seen = seen
    yield rec
    rec.uninstall()


class TinyWrapper:
    """Looks like the Nojoin bug: a shared helper that merges extra_body into the JSON body."""

    def __init__(self, client):
        self.client = client

    def _sampling_kwargs(self):
        return {"extra_body": {"temperature": 0.2}}

    def ask(self, prompt):
        body = {"model": "claude-sonnet-4-6", "max_tokens": 64, "messages": [{"role": "user", "content": prompt}]}
        body.update(self._sampling_kwargs()["extra_body"])  # what the SDK does with extra_body
        headers = {"anthropic-version": "2026-10-21", "authorization": "Bearer sk-secret"}
        return self.client.post(URL, json=body, headers=headers)  # <- call site


def test_sees_temperature_injected_by_a_wrapper_with_a_mocked_response(recorder):
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    TinyWrapper(client).ask("hello")
    assert len(recorder.seen) == 1
    cap = recorder.seen[0]
    assert cap.body["temperature"] == 0.2
    assert cap.path == "/v1/messages"
    assert cap.headers["anthropic-version"] == "2026-10-21"
    # call site = the wrapper line that sent it, not the test and not library code
    lines, start = inspect.getsourcelines(TinyWrapper.ask)
    expected = start + next(i for i, line in enumerate(lines) if "# <- call site" in line)
    assert cap.call_site.endswith(f"test_capture.py:{expected}")


def test_secrets_are_redacted(recorder):
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    client.post(URL, json={}, headers={"x-api-key": "sk-real", "authorization": "Bearer sk-real"})
    headers = recorder.seen[0].headers
    assert headers["x-api-key"] == "<redacted>" and headers["authorization"] == "<redacted>"
    assert "sk-real" not in json.dumps(headers)


def test_only_claude_hosts_and_configured_base_urls(recorder):
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    client.post("https://example.com/v1/messages", json={})
    client.post("https://gateway.example.com/anthropic/v1/messages", json={"x": 1})
    client.post("https://gateway.example.com/other/v1/messages", json={})
    assert [c.url for c in recorder.seen] == ["https://gateway.example.com/anthropic/v1/messages"]


def test_async_client_is_hooked(recorder):
    async def go():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
            await client.post(URL, json={"top_k": 3})

    asyncio.run(go())
    assert recorder.seen[0].body == {"top_k": 3}
    assert "test_capture.py" in recorder.seen[0].call_site


def test_stub_answers_without_network(recorder):
    recorder.stub = {"text": "hi there"}
    client = httpx2.Client(transport=httpx2.MockTransport(no_network))
    resp = client.post(URL, json={"model": "claude-sonnet-4-6", "messages": []})
    assert resp.json()["content"][0]["text"] == "hi there"
    assert resp.json()["model"] == "claude-sonnet-4-6"
    assert len(recorder.seen) == 1


def test_captured_even_when_the_request_then_fails(recorder):
    client = httpx2.Client(transport=httpx2.MockTransport(no_network))
    with pytest.raises(AssertionError):
        client.post(URL, json={"temperature": 1})
    assert recorder.seen[0].body == {"temperature": 1}


def test_uninstall_restores_the_original_send():
    original = httpx2.Client.send
    rec = Recorder(lambda c: None)
    rec.install()
    assert httpx2.Client.send is not original
    rec.uninstall()
    assert httpx2.Client.send is original


def test_classic_httpx_is_hooked_too():
    httpx = pytest.importorskip("httpx")
    seen = []
    rec = Recorder(seen.append)
    rec.install()
    try:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
        client.post(URL, json={"top_p": 0.5})
    finally:
        rec.uninstall()
    assert seen[0].library == "httpx" and seen[0].body == {"top_p": 0.5}

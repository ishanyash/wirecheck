"""Against the real anthropic SDK (2.x in CI). Skipped when the SDK is not installed.

No test here touches the network: every client uses a mock transport or the stub.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect

import pytest

from wirecheck import rules, versions
from wirecheck.capture import Recorder

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

SDK = versions.installed_sdk_version()
SDK2 = (versions.sdk_major(SDK) or 0) >= 2
REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


def boom(request):
    raise AssertionError("no network allowed in this test")


def ok(request):
    if request.url.path.endswith("/batches"):
        return httpx2.Response(200, json={"id": "b", "type": "message_batch", "processing_status": "in_progress",
                                          "request_counts": {"processing": 1, "succeeded": 0, "errored": 0,
                                                             "canceled": 0, "expired": 0},
                                          "created_at": "2026-11-01T00:00:00Z", "expires_at": "2026-11-02T00:00:00Z",
                                          "ended_at": None, "cancel_initiated_at": None, "archived_at": None,
                                          "results_url": None})
    return httpx2.Response(200, json=REPLY)


@pytest.fixture
def seen():
    found = []
    rec = Recorder(found.append)
    rec.install()
    yield found
    rec.uninstall()


def client(handler=ok):
    return anthropic.Anthropic(api_key="sk-test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
                               max_retries=0)


class NojoinLikeBackend:
    def __init__(self, c):
        self.client = c

    def _sampling_kwargs(self):
        return {"extra_body": {"temperature": 0.2}}

    def generate(self, prompt):
        return self.client.messages.create(  # <- call site
            model="claude-sonnet-4-6", max_tokens=64,
            messages=[{"role": "user", "content": prompt}], **self._sampling_kwargs())


def check(c):
    return rules.check(c, rules.Context(sdk_version=SDK, today=dt.date(2026, 11, 1)))


def test_extra_body_temperature_from_a_wrapper_is_on_the_wire(seen):
    reply = NojoinLikeBackend(client()).generate("notes")
    assert reply.content[0].text == "ok"
    (cap,) = seen
    assert cap.body["temperature"] == 0.2
    lines, start = inspect.getsourcelines(NojoinLikeBackend.generate)
    line = start + next(i for i, src in enumerate(lines) if "# <- call site" in src)
    assert cap.call_site.endswith(f"test_sdk.py:{line}")
    found = check(cap)
    assert [(f.rule_id, f.where) for f in found] == [("WC001", "temperature")]
    assert found[0].severity == ("error" if SDK2 else "warn")


@pytest.mark.skipif(not SDK2, reason="needs anthropic>=2.0.0")
def test_sdk2_sends_new_version_and_bearer(seen):
    client().messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    h = seen[0].headers
    assert h["anthropic-version"] == "2026-10-21"
    assert "authorization" in h and "x-api-key" not in h
    assert check(seen[0]) == []


@pytest.mark.skipif(not SDK2, reason="needs anthropic>=2.0.0")
def test_custom_x_api_key_sends_two_credentials(seen):
    client().messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "hi"}],
                             extra_headers={"X-Api-Key": "sk-other"})
    found = check(seen[0])
    assert [(f.rule_id, f.severity) for f in found] == [("WC004", "warn")]
    assert "two credentials" in found[0].message


def test_batch_params_are_checked(seen):
    client().messages.batches.create(requests=[{
        "custom_id": "a",
        "params": {"model": "claude-sonnet-4-6", "max_tokens": 5, "top_k": 10,
                   "messages": [{"role": "user", "content": "hi"}]},
    }])
    assert [f.where for f in check(seen[0])] == ["requests[0].params.top_k"]


def test_fast_mode_for_opus_4_8(seen):
    client().messages.create(model="claude-opus-4-8", max_tokens=5, messages=[{"role": "user", "content": "hi"}],
                             extra_body={"speed": "fast"})
    before = rules.check(seen[0], rules.Context(sdk_version=SDK, today=dt.date(2026, 11, 29)))
    after = rules.check(seen[0], rules.Context(sdk_version=SDK, today=dt.date(2026, 11, 30)))
    assert [(f.rule_id, f.severity) for f in before] == [("WC005", "warn")]
    assert [(f.rule_id, f.severity) for f in after] == [("WC005", "error")]


def test_async_client(seen):
    async def go():
        c = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                     http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(ok)))
        await c.messages.create(model="claude-sonnet-4-6", max_tokens=5, extra_body={"top_p": 0.5},
                                messages=[{"role": "user", "content": "hi"}])

    asyncio.run(go())
    assert [f.where for f in check(seen[0])] == ["top_p"]


def test_captured_even_if_the_call_errors(seen):
    with pytest.raises(Exception):
        client(boom).messages.create(model="claude-sonnet-4-6", max_tokens=5, extra_body={"temperature": 1},
                                     messages=[{"role": "user", "content": "hi"}])
    assert seen and seen[0].body["temperature"] == 1


@pytest.mark.skipif((versions.sdk_major(SDK) or 0) < 1, reason="checks the 1.0+ signature")
def test_temperature_keyword_is_a_type_error_so_tests_already_catch_it(seen):
    # Since anthropic 1.0.0 the keyword is gone; only extra_body gets it to the wire.
    with pytest.raises(TypeError):
        client().messages.create(model="claude-sonnet-4-6", max_tokens=5, temperature=0.2,
                                 messages=[{"role": "user", "content": "hi"}])
    assert seen == []


def test_stub_gives_a_real_message_object_without_network():
    rec = Recorder(lambda c: None)
    rec.install()
    rec.stub = {"text": "canned"}
    try:
        c = anthropic.Anthropic(api_key="sk-test", max_retries=0)  # default transport, never reached
        msg = c.messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "hi"}])
    finally:
        rec.uninstall()
    assert msg.content[0].text == "canned"


def test_plugin_end_to_end_with_real_sdk(pytester):
    pytester.makepyfile(test_real='''
import anthropic, httpx2

def handler(request):
    return httpx2.Response(200, json=%r)

def test_send(wirecheck_requests):
    c = anthropic.Anthropic(api_key="k", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    c.messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[{"role": "user", "content": "hi"}],
                      extra_body={"temperature": 0.2})
    assert len(wirecheck_requests) == 1
''' % (REPLY,))
    result = pytester.runpytest("--wirecheck")
    if SDK2:
        result.assert_outcomes(failed=1)
        assert "WC001" in result.stdout.str()
    else:
        result.assert_outcomes(passed=1)

"""Shared test setup for the demo.

The tests mock the HTTP response the usual way: a real anthropic client with a
mock transport. Nothing goes over the network, so the API can never return the
400 that 2026-10-21 would return. That is the gap WireCheck closes.
"""

import anthropic
import httpx2  # anthropic>=1.x is built on httpx2
import pytest

REPLY = {
    "id": "msg_demo",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-4-6",
    "content": [{"type": "text", "text": "stub summary"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 3},
}


def fake_api(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json=REPLY)


@pytest.fixture
def mocked_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key="test-key",
        http_client=httpx2.Client(transport=httpx2.MockTransport(fake_api)),
    )

from __future__ import annotations

import pytest

from wirecheck.rules import Captured

NEW = {"anthropic-version": "2026-10-21", "authorization": "<redacted>", "content-type": "application/json"}
OLD = {"anthropic-version": "2023-06-01", "x-api-key": "<redacted>", "content-type": "application/json"}


def make(body, headers=None, path="/v1/messages", method="POST"):
    return Captured(
        method=method,
        url="https://api.anthropic.com" + path,
        path=path,
        headers=dict(NEW if headers is None else headers),
        body=body,
        test="tests/test_x.py::test_y",
        call_site="app/llm.py:10",
    )


@pytest.fixture
def captured():
    return make

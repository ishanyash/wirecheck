"""Same tests against the fixed code. These pass with `pytest --wirecheck`."""

from . import app_factory
from .app_direct import summarize
from .app_extra_body import AnthropicBackend


def test_direct_call(mocked_client):
    assert summarize(mocked_client, "a long report") == "stub summary"


def test_wrapper_factory(mocked_client, monkeypatch):
    monkeypatch.setattr(app_factory, "get_client", lambda: mocked_client)
    assert app_factory.make_llm().ask("hello") == "stub summary"


def test_extra_body(mocked_client):
    assert AnthropicBackend(mocked_client).generate("we agreed to ship") == "stub summary"

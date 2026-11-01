from __future__ import annotations

import datetime as dt

import pytest

from wirecheck import clock, rules
from wirecheck.rules import Context, check

from .conftest import NEW, OLD, make

TODAY = dt.date(2026, 11, 1)


def ids(findings):
    return [(f.rule_id, f.severity, f.where) for f in findings]


def ctx(today=TODAY, sdk="2.3.0"):
    return Context(sdk_version=sdk, today=today)


def msg(**extra):
    body = {"model": "claude-sonnet-4-6", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}]}
    body.update(extra)
    return body


# -- WC001 sampling params -----------------------------------------------------------

@pytest.mark.parametrize("name,value", [("temperature", 0.2), ("temperature", 1.0), ("top_p", 0.9), ("top_k", 40),
                                        ("temperature", None)])
def test_wc001_each_sampling_param_is_an_error_on_new_version(name, value):
    found = check(make(msg(**{name: value})), ctx())
    assert ids(found) == [("WC001", "error", name)]
    assert "even at their default values" in found[0].message


def test_wc001_all_three_at_once():
    found = check(make(msg(temperature=0, top_p=1, top_k=5)), ctx())
    assert [f.where for f in found] == ["temperature", "top_p", "top_k"]


def test_wc001_is_a_warning_on_2023_06_01():
    found = check(make(msg(temperature=0.2), headers=OLD), ctx(sdk="1.17.0"))
    assert ids(found) == [("WC001", "warn", "temperature")]
    assert "Works today on 2023-06-01" in found[0].message


def test_wc001_in_message_batches():
    body = {"requests": [{"custom_id": "a", "params": msg()}, {"custom_id": "b", "params": msg(top_k=3)}]}
    found = check(make(body, path="/v1/messages/batches"), ctx())
    assert ids(found) == [("WC001", "error", "requests[1].params.top_k")]


def test_wc001_on_count_tokens_and_beta_query():
    assert ids(check(make(msg(temperature=1), path="/v1/messages/count_tokens"), ctx())) == [
        ("WC001", "error", "temperature")]


def test_clean_request_has_no_findings():
    assert check(make(msg()), ctx()) == []


def test_other_endpoints_and_methods_are_ignored():
    assert check(make(msg(temperature=1), path="/v1/models"), ctx()) == []
    assert check(make(msg(temperature=1), method="GET"), ctx()) == []
    assert check(make(None), ctx()) == []


def test_gateway_path_prefix_still_matches():
    assert ids(check(make(msg(temperature=1), path="/anthropic/v1/messages"), ctx())) == [
        ("WC001", "error", "temperature")]


# -- WC002 document in assistant ----------------------------------------------------

DOC = {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "x"}}


def test_wc002_document_in_assistant_message():
    body = msg(messages=[
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}, DOC]},
        {"role": "user", "content": "more"},
    ])
    assert ids(check(make(body), ctx())) == [("WC002", "error", "messages[1].content[1]")]


def test_wc002_document_in_user_message_is_fine():
    body = msg(messages=[{"role": "user", "content": [DOC, {"type": "text", "text": "summarise"}]}])
    assert check(make(body), ctx()) == []


def test_wc002_nested_document_in_tool_result_is_fine():
    nested = {"type": "web_fetch_tool_result", "tool_use_id": "t", "content": {"type": "web_fetch_result", "content": DOC}}
    body = msg(messages=[{"role": "user", "content": "go"}, {"role": "assistant", "content": [nested]}])
    assert check(make(body), ctx()) == []


# -- WC003 citations in user --------------------------------------------------------

CITE = [{"type": "char_location", "cited_text": "x", "document_index": 0, "start_char_index": 0, "end_char_index": 1}]


def test_wc003_user_text_with_citations():
    body = msg(messages=[{"role": "user", "content": [{"type": "text", "text": "q", "citations": CITE}]}])
    assert ids(check(make(body), ctx())) == [("WC003", "error", "messages[0].content[0].citations")]


def test_wc003_empty_citations_are_fine():
    body = msg(messages=[{"role": "user", "content": [{"type": "text", "text": "q", "citations": []}]}])
    assert check(make(body), ctx()) == []


def test_wc003_assistant_citations_are_fine():
    body = msg(messages=[{"role": "user", "content": "q"},
                         {"role": "assistant", "content": [{"type": "text", "text": "a", "citations": CITE}]}])
    assert check(make(body), ctx()) == []


def test_wc003_inside_search_result():
    sr = {"type": "search_result", "source": "s", "title": "t",
          "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b", "citations": CITE}]}
    body = msg(messages=[{"role": "user", "content": [sr]}])
    assert ids(check(make(body), ctx())) == [("WC003", "error", "messages[0].content[0].content[1].citations")]


def test_wc003_search_result_inside_tool_result():
    sr = {"type": "search_result", "source": "s", "title": "t", "content": [{"type": "text", "text": "b", "citations": CITE}]}
    tr = {"type": "tool_result", "tool_use_id": "t", "content": [sr]}
    body = msg(messages=[{"role": "user", "content": [tr]}])
    assert ids(check(make(body), ctx())) == [("WC003", "error", "messages[0].content[0].content[0].content[0].citations")]


def test_wc003_citations_enabled_on_documents_is_fine():
    doc = dict(DOC, citations={"enabled": True})
    body = msg(messages=[{"role": "user", "content": [doc, {"type": "text", "text": "q"}]}])
    assert check(make(body), ctx()) == []


# -- WC004 X-Api-Key ----------------------------------------------------------------

def test_wc004_x_api_key_on_new_version_warns():
    headers = {"anthropic-version": "2026-10-21", "x-api-key": "<redacted>"}
    found = check(make(msg(), headers=headers), ctx())
    assert ids(found) == [("WC004", "warn", "headers.x-api-key")]


def test_wc004_both_headers_says_two_credentials():
    headers = dict(NEW, **{"x-api-key": "<redacted>"})
    found = check(make(msg(), headers=headers), ctx())
    assert "two credentials are sent" in found[0].message


def test_wc004_not_on_old_version_where_the_sdk_sets_it():
    assert check(make(msg(), headers=OLD), ctx(sdk="1.17.0")) == []


# -- WC005 fast mode, frozen clock ---------------------------------------------------

FAST = dict(model="claude-opus-4-8", speed="fast")


@pytest.mark.parametrize("today,severity", [
    (dt.date(2026, 11, 1), "warn"),
    (dt.date(2026, 11, 29), "warn"),
    (dt.date(2026, 11, 30), "error"),
    (dt.date(2027, 1, 1), "error"),
])
def test_wc005_warns_before_and_errors_from_2026_11_30(today, severity):
    found = check(make(msg(**FAST)), ctx(today=today))
    assert ids(found) == [("WC005", severity, "speed")]


def test_wc005_counts_days_left():
    found = check(make(msg(**FAST)), ctx(today=dt.date(2026, 11, 20)))
    assert "in 10 day(s)" in found[0].message


def test_wc005_applies_on_old_api_version_too():
    found = check(make(msg(**FAST), headers=OLD), ctx(today=dt.date(2026, 12, 1), sdk="1.17.0"))
    assert ids(found) == [("WC005", "error", "speed")]


@pytest.mark.parametrize("extra", [dict(model="claude-opus-5-5", speed="fast"),
                                   dict(model="claude-opus-4-8", speed="standard"),
                                   dict(model="claude-opus-4-8")])
def test_wc005_not_for_other_models_or_speeds(extra):
    assert check(make(msg(**extra)), ctx(today=dt.date(2026, 12, 1))) == []


def test_wc005_uses_the_frozen_module_clock(monkeypatch):
    monkeypatch.setattr(clock, "today", lambda: dt.date(2026, 11, 30))
    found = check(make(msg(**FAST)), Context(sdk_version="2.3.0", today=clock.today()))
    assert found[0].severity == "error"


def test_clock_env_override(monkeypatch):
    monkeypatch.setenv("WIRECHECK_TODAY", "2026-11-29")
    assert clock.today() == dt.date(2026, 11, 29)


def test_every_rule_has_an_id_message_and_doc_link():
    for rule_id, rule in rules.RULES.items():
        assert rule.id == rule_id and rule.summary and rule.doc.startswith("https://platform.claude.com/docs/")

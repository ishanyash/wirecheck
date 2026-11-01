import pytest

from wirecheck import versions


@pytest.mark.parametrize("sdk,expected", [
    ("2.0.0", "2026-10-21"), ("2.3.0", "2026-10-21"), ("2.0.0rc1", "2026-10-21"), ("3.1", "2026-10-21"),
    ("1.17.0", "2023-06-01"), ("0.125.0", "2023-06-01"), (None, None), ("", None), ("weird", None),
])
def test_sdk_default_api_version(sdk, expected):
    assert versions.sdk_default_api_version(sdk) == expected


def test_header_wins_over_sdk():
    res = versions.resolve({"anthropic-version": "2023-06-01"}, "2.3.0")
    assert (res.api_version, res.source, res.is_new) == ("2023-06-01", "header", False)


def test_sdk_used_when_no_header():
    res = versions.resolve({}, "2.1.0")
    assert (res.api_version, res.source, res.is_new) == ("2026-10-21", "sdk", True)


def test_default_when_nothing_known():
    res = versions.resolve(None, None)
    assert (res.api_version, res.source, res.is_new) == ("2023-06-01", "default", False)


def test_future_versions_count_as_new():
    assert versions.is_at_least_new("2027-03-01")
    assert not versions.is_at_least_new("2023-06-01")

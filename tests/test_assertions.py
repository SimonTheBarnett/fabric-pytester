import datetime as dt

from fabric_pytester.core.assertions import AssertionCollector, assert_rows, value_matches
from fabric_pytester.core.sql_backend import Row


def test_legacy_assertion_matchers():
    assert value_matches(None, "{NULL}")
    assert value_matches("value", "{!NULL}")
    assert value_matches("2026-07-09T12:00:00", "{DATE}")
    assert value_matches("550e8400-e29b-41d4-a716-446655440000", "{UUID}")
    assert not value_matches(None, "{!NULL}")


def test_dict_contains_matcher_supports_count():
    assert value_matches("value,value", {"contains": "value", "count": 2})
    assert not value_matches("value,value", {"contains": "value", "count": 1})


def test_dict_count_matcher_supports_collection_length():
    assert value_matches(["a", "b"], {"count": 2})
    assert not value_matches(["a"], {"count": 2})


def test_dict_regex_and_equals_matchers():
    assert value_matches("ORD-123", {"regex": r"^ORD-\d+$"})
    assert value_matches("Submitted", {"equals": "Submitted"})
    assert not value_matches("Submitted", {"equals": "Failed"})


def test_date_values_match_iso_date_strings():
    assert value_matches(dt.date(2026, 7, 9), "2026-07-09")
    assert value_matches("2026-07-09", dt.date(2026, 7, 9))
    assert value_matches(dt.datetime(2026, 7, 9, 12, 30), "2026-07-09")
    assert value_matches("2026-07-09", dt.datetime(2026, 7, 9, 12, 30))
    assert value_matches(dt.date(2026, 7, 9), {"equals": "2026-07-09"})
    assert not value_matches(dt.date(2026, 7, 10), "2026-07-09")


def test_non_date_values_keep_existing_equality_behavior():
    assert not value_matches("2026-07-09T12:30:00", "2026-07-09")
    assert not value_matches("Submitted", "submitted")


def test_date_failure_message_uses_normalized_display_values():
    collector = AssertionCollector()

    assert_rows(
        scenario="orders",
        target="sql",
        rows=[Row({"CompletionDate": dt.date(2026, 7, 10)})],
        fields={"CompletionDate": "2026-07-09"},
        collector=collector,
    )

    assert collector.failures == [
        "[orders] sql: CompletionDate: expected 2026-07-09, got 2026-07-10"
    ]

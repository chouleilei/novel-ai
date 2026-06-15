from datetime import datetime, timedelta, timezone

from backend.api.datetime_utils import serialize_datetime


def test_serialize_datetime_treats_naive_datetime_as_utc():
    value = datetime(2026, 3, 30, 12, 34, 56)

    assert serialize_datetime(value) == "2026-03-30T12:34:56+00:00"


def test_serialize_datetime_normalizes_aware_datetime_to_utc():
    value = datetime(2026, 3, 30, 20, 34, 56, tzinfo=timezone(timedelta(hours=8)))

    assert serialize_datetime(value) == "2026-03-30T12:34:56+00:00"


def test_serialize_datetime_supports_none():
    assert serialize_datetime(None) is None

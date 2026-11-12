from datetime import datetime, timedelta, timezone

import pytest

from conftest import TZ
from hermes_hevy.dates import ArgError, iso_utc, parse_instant, parse_local_time

UTC = timezone.utc


@pytest.mark.parametrize("text, expected", [
    ("2024-08-14T12:00:00Z", datetime(2024, 8, 14, 12, 0, tzinfo=UTC)),
    ("2024-08-14T12:00:00+00:00", datetime(2024, 8, 14, 12, 0, tzinfo=UTC)),
    ("2024-08-14T14:00:00+02:00", datetime(2024, 8, 14, 12, 0, tzinfo=UTC)),
    ("2024-08-14T12:00:00.123Z", datetime(2024, 8, 14, 12, 0, 0, 123000, tzinfo=UTC)),
    ("2024-08-14T12:00:00", datetime(2024, 8, 14, 12, 0, tzinfo=UTC)),
])
def test_parse_instant_returns_aware_utc(text, expected):
    moment = parse_instant(text)
    assert moment == expected
    assert moment.utcoffset() == timedelta(0)


@pytest.mark.parametrize("value", ["", "   ", "yesterday", "2024-13-01T00:00:00Z", None, 1723636800])
def test_parse_instant_is_none_when_empty_or_invalid(value):
    assert parse_instant(value) is None


def test_iso_utc_writes_hevy_form():
    assert iso_utc(datetime(2024, 8, 14, 12, 0, 0)) == "2024-08-14T12:00:00Z"  # naive is UTC
    assert iso_utc(datetime(2024, 8, 14, 12, 0, 0, 999999, tzinfo=UTC)) == "2024-08-14T12:00:00Z"


def test_iso_utc_converts_aware_datetimes():
    madrid = datetime(2024, 8, 14, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    assert iso_utc(madrid) == "2024-08-14T12:00:00Z"
    assert iso_utc(parse_instant("2024-08-14T14:00:00+02:00")) == "2024-08-14T12:00:00Z"


def test_parse_local_time_reads_local_wall_clock_in_zone():
    expected = datetime(2026, 11, 9, 18, 0, tzinfo=TZ)
    for text in ("2026-11-09 18:00", "2026-11-09T18:00"):
        moment = parse_local_time(text, TZ)
        assert moment == expected
        assert moment.tzinfo is TZ
        assert moment.utcoffset() == timedelta(hours=1)  # CET in November


def test_parse_local_time_keeps_explicit_offsets_and_z():
    zulu = parse_local_time("2026-11-09T18:00:00Z", TZ)
    assert zulu.hour == 18 and zulu.utcoffset() == timedelta(0)

    plus_two = parse_local_time("2026-11-09T18:00:00+02:00", TZ)
    assert plus_two.utcoffset() == timedelta(hours=2)
    assert iso_utc(plus_two) == "2026-11-09T16:00:00Z"


@pytest.mark.parametrize("value", ["tomorrow 6pm", "", None, "2026-13-40 10:00"])
def test_parse_local_time_rejects_bad_input_with_a_fix(value):
    with pytest.raises(ArgError, match="YYYY-MM-DD HH:MM"):
        parse_local_time(value, TZ)

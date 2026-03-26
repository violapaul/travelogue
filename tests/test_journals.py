"""Tests for journal date parsing."""

from datetime import date

from travelogue.ingest.journals import _try_parse_date


def test_iso_date():
    d, conf = _try_parse_date("2026-03-18")
    assert d == date(2026, 3, 18)
    assert conf > 0.9


def test_long_month_day_year():
    d, conf = _try_parse_date("March 18, 2026")
    assert d == date(2026, 3, 18)
    assert conf > 0.8


def test_day_month_year():
    d, conf = _try_parse_date("18 March 2026")
    assert d == date(2026, 3, 18)


def test_no_date():
    d, conf = _try_parse_date("nothing here")
    assert d is None
    assert conf == 0.0


def test_month_day_no_year_with_context():
    d, conf = _try_parse_date("March 18", context_year=2026)
    assert d == date(2026, 3, 18)


def test_filename_date():
    d, conf = _try_parse_date("2026-03-18")
    assert d == date(2026, 3, 18)

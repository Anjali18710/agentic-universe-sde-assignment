from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from solution.normalize import (IST, normalise_header, normalise_phone, parse_date, parse_decimal,
                                parse_enum, parse_timestamp)


@pytest.mark.parametrize("raw", [
    "9876543210", "+91 98765 43210", "09876543210", "91-98765-43210", "(+91) 9876543210",
    "+919876543210", " 98765-43210 ",
])
def test_phone_all_formats_normalise_to_same_canonical(raw):
    assert normalise_phone(raw) == "+919876543210"


@pytest.mark.parametrize("raw", [
    "987654321",          # 9 digits
    "5876543210",         # must start 6-9
    "022-2345-6789",      # landline
    "N/A", "",
    "+1 415 555 0134",    # 11 digits not starting with 0
    "919876543210 1",     # 13 digits
    "91987654321",        # 11 digits starting with 9, not 0
])
def test_phone_invalid(raw):
    assert normalise_phone(raw) is None


def test_phone_12_digits_only_strips_91_prefix():
    assert normalise_phone("919176543210") == "+919176543210"      # 91 + 9176543210
    assert normalise_phone("929876543210") is None                 # 12 digits, not 91


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-21", date(2026, 11, 21)),
    ("21/11/2026", date(2026, 11, 21)),
    ("21-11-2026", date(2026, 11, 21)),
    ("01-Dec-2026", date(2026, 12, 1)),
    ("01-dec-2026", date(2026, 12, 1)),     # month name is case-insensitive
    ("05/03/2026", date(2026, 3, 5)),       # day-first, never month-first
])
def test_date_formats(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw", ["31/02/2026", "2026-13-01", "soon", "TBD", "29-02-2026",
                                 "01-Foo-2026", "2026/11/21"])
def test_date_invalid(raw):
    assert parse_date(raw) is None


def test_date_leap_day_valid():
    assert parse_date("29/02/2028") == date(2028, 2, 29)


@pytest.mark.parametrize("raw,expected", [
    ("27554", "27554.00"), ("₹27,554", "27554.00"), ("Rs. 27554", "27554.00"),
    ("Rs 27554", "27554.00"), ("rs.27554", "27554.00"), ("INR 25301.4", "25301.40"),
    ("inr25301.4", "25301.40"), ("1,23,456.789", "123456.79"), ("0", "0.00"),
    ("10.005", "10.01"),   # half-up rounding to 2 places
])
def test_decimal(raw, expected):
    assert parse_decimal(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["N/A", "-", "twelve thousand", "-500", "₹-5", "", "1.2.3"])
def test_decimal_invalid(raw):
    assert parse_decimal(raw) is None


def test_enum_case_insensitive_and_stored_lower():
    langs = ["en", "hi", "ta"]
    assert parse_enum("HI", langs) == "hi"
    assert parse_enum("Hindi", langs) is None


@pytest.mark.parametrize("raw", ["Mobile No", "mobile_no", "  MOBILE__ no ", "mobile\tno"])
def test_header_normalisation(raw):
    assert normalise_header(raw) == "mobile no"


def test_timestamps_three_forms_are_the_same_instant():
    utc = parse_timestamp("2026-10-03T04:31:00Z")
    offset = parse_timestamp("2026-10-03T10:01:00+05:30")
    naive = parse_timestamp("2026-10-03 10:01:00")    # naive means IST
    assert utc == offset == naive


def test_ist_date_differs_from_utc_date_near_midnight():
    t = parse_timestamp("2026-10-03T19:00:00Z")       # 00:30 IST on the 4th
    assert t.astimezone(IST).date() == date(2026, 10, 4)
    assert t.astimezone(timezone.utc).date() == date(2026, 10, 3)


def test_timestamp_garbage():
    assert parse_timestamp("yesterday") is None
    assert parse_timestamp(None) is None

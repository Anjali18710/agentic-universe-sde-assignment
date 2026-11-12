"""Pure value-cleaning rules from the spec ("Lead files" and "Event files").

Every function here is side-effect free, so it is easy to unit-test.
Parsers return the cleaned value, or None when the value is invalid.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- headers

_SEP_RUN = re.compile(r"[_\s]+")


def normalise_header(h: str) -> str:
    """Lower-case, trim, and collapse every run of '_' or whitespace to one space."""
    return _SEP_RUN.sub(" ", h.strip().lower()).strip()


# ---------------------------------------------------------------- phone

def normalise_phone(raw: str) -> str | None:
    """Indian mobile -> '+91XXXXXXXXXX', or None if invalid."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


# ---------------------------------------------------------------- date

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")          # YYYY-MM-DD
_DMY = re.compile(r"^(\d{1,2})([/-])(\d{1,2})\2(\d{4})$")     # DD/MM/YYYY or DD-MM-YYYY
_DMONY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")     # DD-Mon-YYYY


def parse_date(raw: str) -> date | None:
    """Day-first dates in the four accepted formats. Impossible dates -> None."""
    s = raw.strip()
    try:
        if m := _ISO.match(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DMY.match(s):
            return date(int(m[4]), int(m[3]), int(m[1]))
        if m := _DMONY.match(s):
            month = _MONTHS.get(m[2].lower())
            return date(int(m[3]), month, int(m[1])) if month else None
    except ValueError:  # e.g. 31/02/2026 or month 13
        return None
    return None


# ---------------------------------------------------------------- decimal

_CURRENCY_PREFIX = re.compile(r"^(₹|rs\.?|inr)\s*", re.IGNORECASE)
_NUMBER = re.compile(r"^\d+(\.\d+)?$")


def parse_decimal(raw: str) -> Decimal | None:
    """Optional ₹ / Rs / Rs. / INR prefix, commas as thousands separators, >= 0, 2 places."""
    s = _CURRENCY_PREFIX.sub("", raw.strip(), count=1).replace(",", "").strip()
    if not _NUMBER.match(s):  # rejects '-', 'N/A', words, and negative numbers
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------- enum

def parse_enum(raw: str, allowed: list[str]) -> str | None:
    v = raw.strip().lower()
    return v if v in {a.lower() for a in allowed} else None


# ---------------------------------------------------------------- timestamps

def parse_timestamp(raw) -> datetime | None:
    """'...Z' (UTC), '...+05:30' (offset) or naive (= IST). Returns an aware datetime."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)  # spec: naive means IST
    return dt


def to_utc_text(dt: datetime) -> str:
    """Fixed-width UTC text, so string order == time order inside SQLite."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_utc_text(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)

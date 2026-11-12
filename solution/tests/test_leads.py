"""Row validation: rejection order, warnings, defaults are not warnings."""
from solution.catalog import Catalog
from solution.leads import validate_row

MOTOR = Catalog().schemas["motor_renewal"]
GOOD = {"customer_name": "Asha Rao", "phone": "98765 43210", "policy_number": "MOT/1",
        "policy_expiry": "01-Dec-2026", "premium_amount": "₹12,000", "preferred_language": "HI"}


def test_good_row_is_cleaned():
    r = validate_row(MOTOR, GOOD)
    assert r.reject_reason is None and r.warnings == 0
    assert r.values == {"customer_name": "Asha Rao", "phone": "+919876543210",
                        "policy_number": "MOT/1", "policy_expiry": "2026-12-01",
                        "premium_amount": "12000.00", "preferred_language": "hi"}


def test_first_failure_in_schema_order_wins():
    # name (field 1) is missing AND phone (field 2) is invalid -> name is reported
    r = validate_row(MOTOR, {**GOOD, "customer_name": "", "phone": "N/A"})
    assert r.reject_reason == "missing_required:customer_name"


def test_invalid_phone_beats_missing_reference():
    r = validate_row(MOTOR, {**GOOD, "phone": "12345", "policy_number": ""})
    assert r.reject_reason == "invalid_phone"


def test_missing_phone_is_missing_required_not_invalid():
    assert validate_row(MOTOR, {**GOOD, "phone": ""}).reject_reason == "missing_required:phone"


def test_absent_column_counts_as_missing():
    row = {k: v for k, v in GOOD.items() if k != "policy_number"}
    assert validate_row(MOTOR, row).reject_reason == "missing_required:policy_number"


def test_invalid_optional_fields_become_null_with_one_warning_each():
    r = validate_row(MOTOR, {**GOOD, "policy_expiry": "31/02/2026", "premium_amount": "N/A",
                             "preferred_language": "Hindi"})
    assert r.reject_reason is None and r.warnings == 3
    assert r.values["policy_expiry"] is None and r.values["preferred_language"] is None


def test_empty_optional_field_is_not_a_warning():
    r = validate_row(MOTOR, {**GOOD, "preferred_language": "", "premium_amount": ""})
    assert r.warnings == 0

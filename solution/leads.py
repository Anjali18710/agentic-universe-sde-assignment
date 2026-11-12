"""Lead ingestion: map headers, validate, normalise, reject, merge duplicates."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import LeadSchema
from .crypto import Vault
from .normalize import normalise_header, normalise_phone, parse_date, parse_decimal, parse_enum


@dataclass
class RowResult:
    """Outcome of validating one CSV row against a schema."""
    values: dict = field(default_factory=dict)  # field name -> cleaned value (None = missing/invalid)
    reject_reason: str | None = None
    warnings: int = 0


def clean_value(f, raw: str):
    """Returns (cleaned_value, ok). Strings are always ok."""
    if f.type == "string":
        return raw, True
    if f.type == "phone":
        v = normalise_phone(raw)
    elif f.type == "date":
        d = parse_date(raw)
        v = d.isoformat() if d else None
    elif f.type == "decimal":
        d = parse_decimal(raw)
        v = str(d) if d is not None else None
    elif f.type == "enum":
        v = parse_enum(raw, f.values)
    else:
        raise ValueError(f"unknown field type {f.type!r} for field {f.name!r}")
    return v, v is not None


def validate_row(schema: LeadSchema, raw: dict[str, str]) -> RowResult:
    """raw: field name -> trimmed cell text ('' when the column is absent or empty).

    Walks fields in schema order; the first failure becomes the single rejection reason.
    """
    res = RowResult()
    for f in schema.fields:
        text = raw.get(f.name, "")
        if text == "":
            if f.required:
                res.reject_reason = f"missing_required:{f.name}"
                return res
            res.values[f.name] = None
            continue
        value, ok = clean_value(f, text)
        if not ok:
            if f.type == "phone":
                res.reject_reason = "invalid_phone"
                return res
            if f.required:
                # Not reachable with the given schemas (no required date/decimal/enum),
                # but a required field that is unusable cannot make a lead either.
                res.reject_reason = f"invalid:{f.name}"
                return res
            res.warnings += 1  # optional field present but invalid -> store null, warn
        res.values[f.name] = value
    return res


@dataclass
class FileStats:
    rows_total: int = 0
    rejected: int = 0
    merged: int = 0
    warnings: int = 0


def read_rows(path: Path, schema: LeadSchema):
    """Yields (row_number, {field name: trimmed text}) for every non-blank data row."""
    lookup = schema.header_lookup()
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return
        # column index -> field name; the first column that maps to a field wins
        col_to_field: dict[int, str] = {}
        for i, h in enumerate(header):
            name = lookup.get(normalise_header(h))
            if name and name not in col_to_field.values():
                col_to_field[i] = name
        row_number = 0
        for cells in reader:
            if all(c.strip() == "" for c in cells):
                continue  # blank rows are skipped and not counted
            row_number += 1
            raw = {name: (cells[i].strip() if i < len(cells) else "")
                   for i, name in col_to_field.items()}
            yield row_number, raw


def ingest_lead_file(conn, vault: Vault, schema: LeadSchema, path: Path, file_id: int) -> FileStats:
    """Must be called inside a transaction (see pipeline.py)."""
    stats = FileStats()
    wf, batch_id = schema.workflow_id, path.stem
    pii_names = [f.name for f in schema.fields if f.pii]
    phone_field = next(f.name for f in schema.fields if f.type == "phone")
    ref_field = schema.reference_field

    for row_number, raw in read_rows(path, schema):
        stats.rows_total += 1
        res = validate_row(schema, raw)
        if res.reject_reason:
            stats.rejected += 1
            conn.execute(
                "INSERT INTO rejections (workflow_id, batch_id, row_number, reason, file_id)"
                " VALUES (?, ?, ?, ?, ?)", (wf, batch_id, row_number, res.reject_reason, file_id))
            continue
        stats.warnings += res.warnings

        phone_hash = vault.phone_hash(res.values[phone_field])
        existing = conn.execute(
            "SELECT attributes, pii_enc FROM leads WHERE workflow_id=? AND phone_hash=?",
            (wf, phone_hash)).fetchone()

        if existing is None:
            merged = res.values
        else:
            # A later valid row for the same identity: overwrite only its non-null fields.
            stats.merged += 1
            merged = {**json.loads(existing[0]), **vault.decrypt(existing[1])}
            merged.update({k: v for k, v in res.values.items() if v is not None})

        # Defaults apply after merging, to fields that are still null. (Applying them on every
        # write is equivalent: a default is non-null, and nulls never overwrite during a merge.)
        for f in schema.fields:
            if merged.get(f.name) is None and f.default is not None:
                merged[f.name] = f.default

        attributes = json.dumps({k: v for k, v in merged.items() if k not in pii_names}, sort_keys=True)
        pii_enc = vault.encrypt({k: merged.get(k) for k in pii_names})
        if existing is None:
            conn.execute(
                "INSERT INTO leads (workflow_id, phone_hash, batch_id, external_ref, attributes,"
                " pii_enc, created_file, updated_file) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (wf, phone_hash, batch_id, merged.get(ref_field) or "", attributes, pii_enc,
                 file_id, file_id))
        else:  # batch_id is deliberately NOT updated: the first file owns the lead forever
            conn.execute(
                "UPDATE leads SET external_ref=?, attributes=?, pii_enc=?, updated_file=?"
                " WHERE workflow_id=? AND phone_hash=?",
                (merged.get(ref_field) or "", attributes, pii_enc, file_id, wf, phone_hash))
    return stats

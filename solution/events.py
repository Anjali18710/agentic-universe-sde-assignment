"""Event ingestion into the append-only ledger.

At ingest time we only decide what is about the *line itself*: malformed or duplicate.
Whether an event is an orphan or has an unknown disposition depends on which leads exist,
which can change with a later drop, so those are decided at report time (report.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .crypto import Vault
from .normalize import normalise_phone, parse_timestamp, to_utc_text


@dataclass
class FileStats:
    lines_total: int = 0
    malformed: int = 0
    duplicates: int = 0


def parse_line(line: str) -> dict | None:
    """Returns the event dict, or None if the line is malformed."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(ev, dict) or ev.get("event_id") in (None, ""):
        return None
    # Decision (see SUBMISSION.md): an event with no usable timestamp can't be placed on a
    # date or ordered against overrides, so it is treated as malformed too.
    if parse_timestamp(ev.get("occurred_at")) is None:
        return None
    return ev


def ingest_event_file(conn, vault: Vault, path: Path, file_id: int) -> FileStats:
    """Must be called inside a transaction (see pipeline.py)."""
    stats = FileStats()
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue  # events_total counts non-empty lines only
            stats.lines_total += 1
            ev = parse_line(line)
            if ev is None:
                stats.malformed += 1
                continue

            raw_phone = ev.get("phone")
            canonical = normalise_phone(raw_phone) if isinstance(raw_phone, str) else None
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            code = payload.get("disposition_code")

            seq += 1
            cur = conn.execute(
                "INSERT OR IGNORE INTO events (event_id, seq, file_id, event_type, workflow_id,"
                " call_id, occurred_at_utc, phone_hash, phone_enc, disposition_code, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(ev["event_id"]), seq, file_id, ev.get("event_type"), ev.get("workflow_id"),
                 ev.get("call_id"), to_utc_text(parse_timestamp(ev["occurred_at"])),
                 vault.phone_hash(canonical) if canonical else None,
                 vault.encrypt(raw_phone) if raw_phone is not None else None,
                 code if isinstance(code, str) else None,
                 json.dumps(payload, ensure_ascii=False, sort_keys=True)))
            if cur.rowcount == 0:  # event_id already in the ledger (this drop or an earlier one)
                stats.duplicates += 1
                seq -= 1
    return stats

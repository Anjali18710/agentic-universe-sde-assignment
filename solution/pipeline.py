"""Orchestrates ingestion of one or more drops, one file per transaction."""
from __future__ import annotations

import sys
from pathlib import Path

from . import events as event_ingest
from . import leads as lead_ingest
from .catalog import Catalog
from .crypto import Vault
from .db import already_ingested, register_file, sha256_of


def expand_drops(paths: list[Path]) -> list[Path]:
    """A drop is a folder with leads/ and/or events/. A parent folder holding several drops
    (e.g. data/) expands to its drop folders in name order."""
    drops = []
    for p in paths:
        if not p.is_dir():
            raise SystemExit(f"not a directory: {p}")
        if (p / "leads").is_dir() or (p / "events").is_dir():
            drops.append(p)
        else:
            children = sorted(c for c in p.iterdir()
                              if c.is_dir() and ((c / "leads").is_dir() or (c / "events").is_dir()))
            if not children:
                raise SystemExit(f"no leads/ or events/ folder found under {p}")
            drops.extend(children)
    return drops


def _in_transaction(conn, fn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
        conn.execute("COMMIT")
        return result
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def ingest_drop(conn, vault: Vault, catalog: Catalog, drop: Path, log=print) -> None:
    # Leads: workflows in name order, files in filename order, rows in file order.
    leads_dir = drop / "leads"
    for wf_dir in sorted(leads_dir.iterdir()) if leads_dir.is_dir() else []:
        if not wf_dir.is_dir():
            continue
        schema = catalog.schemas.get(wf_dir.name)
        if schema is None:
            print(f"warning: no schema for workflow {wf_dir.name!r}; skipping {wf_dir}", file=sys.stderr)
            continue
        for path in sorted(wf_dir.glob("*.csv"), key=lambda p: p.name):
            digest = sha256_of(path)
            if already_ingested(conn, "leads", schema.workflow_id, path, digest):
                log(f"  skip (already ingested) {path}")
                continue

            def work(path=path, digest=digest, schema=schema):
                file_id = register_file(conn, "leads", schema.workflow_id, path, digest)
                s = lead_ingest.ingest_lead_file(conn, vault, schema, path, file_id)
                conn.execute(
                    "UPDATE ingested_files SET lead_rows_total=?, lead_rows_rejected=?,"
                    " duplicates_merged=?, field_warnings=? WHERE file_id=?",
                    (s.rows_total, s.rejected, s.merged, s.warnings, file_id))
                return s

            s = _in_transaction(conn, work)
            log(f"  leads  {path}: {s.rows_total} rows, {s.rejected} rejected, "
                f"{s.merged} merged, {s.warnings} warnings")

    # Events: files in filename order, lines in file order.
    events_dir = drop / "events"
    for path in sorted(events_dir.glob("*.jsonl"), key=lambda p: p.name) if events_dir.is_dir() else []:
        digest = sha256_of(path)
        if already_ingested(conn, "events", "", path, digest):
            log(f"  skip (already ingested) {path}")
            continue

        def work(path=path, digest=digest):
            file_id = register_file(conn, "events", "", path, digest)
            s = event_ingest.ingest_event_file(conn, vault, path, file_id)
            conn.execute(
                "UPDATE ingested_files SET event_lines_total=?, malformed_events=?,"
                " duplicate_events=? WHERE file_id=?",
                (s.lines_total, s.malformed, s.duplicates, file_id))
            return s

        s = _in_transaction(conn, work)
        log(f"  events {path}: {s.lines_total} lines, {s.malformed} malformed, {s.duplicates} duplicates")

"""Builds the five report files from everything ingested so far."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .catalog import Catalog
from .crypto import Vault
from .derive import Call, Event, is_success, lead_bucket, link_events
from .normalize import IST, from_utc_text

BUCKETS = ["yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"]


def _write_csv(path: Path, header: list[str], rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def _bool(b: bool) -> str:
    return "true" if b else "false"


def load_events(conn) -> list[Event]:
    return [
        Event(seq=r[0], event_type=r[1], workflow_id=r[2], call_id=r[3],
              occurred_at=from_utc_text(r[4]), phone_hash=r[5], disposition_code=r[6])
        for r in conn.execute(
            "SELECT seq, event_type, workflow_id, call_id, occurred_at_utc, phone_hash,"
            " disposition_code FROM events ORDER BY seq")
    ]


def build_reports(conn, vault: Vault, catalog: Catalog, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    disp = catalog.dispositions

    leads = {(r[0], r[1]): {"batch_id": r[2], "external_ref": r[3], "pii_enc": r[4]}
             for r in conn.execute(
                 "SELECT workflow_id, phone_hash, batch_id, external_ref, pii_enc FROM leads")}

    linked = link_events(load_events(conn), set(leads), disp)
    calls_by_lead: dict[tuple[str, str], list[Call]] = defaultdict(list)
    for c in linked.calls:
        calls_by_lead[c.lead_key].append(c)

    # ---- 3. lead_report.csv (also feeds batch_summary)
    phone_field = {wf: next(f.name for f in s.fields if f.type == "phone")
                   for wf, s in catalog.schemas.items()}
    lead_rows = []
    batch_counts: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for key, lead in leads.items():
        wf = key[0]
        calls = sorted(calls_by_lead.get(key, []), key=lambda c: c.initiated_at)
        bucket = lead_bucket(calls, disp)
        batch_counts[(wf, lead["batch_id"])][bucket] += 1

        phone = vault.decrypt(lead["pii_enc"])[phone_field[wf]]
        last_call_at = calls[-1].initiated_at.astimezone(IST).isoformat(timespec="seconds") if calls else ""
        current = next((c.effective_disposition for c in reversed(calls) if c.effective_disposition), "")
        lead_rows.append([
            wf, lead["external_ref"], lead["batch_id"], "+91******" + phone[-4:], bucket,
            len(calls), sum(c.connected for c in calls), last_call_at, current,
            _bool(any(is_success(c, disp) for c in calls)),
        ])
    lead_rows.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    _write_csv(out_dir / "lead_report.csv",
               ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
                "connected_calls", "last_call_at", "current_disposition", "ever_success"], lead_rows)

    # ---- 1. batch_summary.csv
    _write_csv(out_dir / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *BUCKETS],
               [[wf, b, sum(cnt.values()), *(cnt[x] for x in BUCKETS)]
                for (wf, b), cnt in sorted(batch_counts.items())])

    # ---- 2. daily_overview.csv (dates are IST dates of call.initiated)
    daily = defaultdict(lambda: {"total": 0, "connected": 0, "success_leads": set()})
    for c in linked.calls:
        d = daily[(c.workflow_id, c.initiated_at.astimezone(IST).date().isoformat())]
        d["total"] += 1
        d["connected"] += c.connected
        if is_success(c, disp):
            d["success_leads"].add(c.lead_key)
    _write_csv(out_dir / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               [[wf, day, d["total"], d["connected"], f"{d['connected'] / d['total']:.4f}", len(d["success_leads"])]
                for (wf, day), d in sorted(daily.items())])

    # ---- 4. rejections.csv (no PII by construction: the table holds none)
    _write_csv(out_dir / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"],
               conn.execute("SELECT workflow_id, batch_id, row_number, reason FROM rejections"
                            " ORDER BY workflow_id, batch_id, row_number").fetchall())

    # ---- 5. data_quality.json
    (lead_total, lead_rejected, merged, warnings, ev_total, malformed, dup_events) = conn.execute(
        "SELECT COALESCE(SUM(lead_rows_total),0), COALESCE(SUM(lead_rows_rejected),0),"
        " COALESCE(SUM(duplicates_merged),0), COALESCE(SUM(field_warnings),0),"
        " COALESCE(SUM(event_lines_total),0), COALESCE(SUM(malformed_events),0),"
        " COALESCE(SUM(duplicate_events),0) FROM ingested_files").fetchone()
    by_reason = dict(conn.execute(
        "SELECT reason, COUNT(*) FROM rejections GROUP BY reason ORDER BY reason").fetchall())
    quality = {
        "lead_rows_total": lead_total,
        "lead_rows_rejected": lead_rejected,
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": merged,
        "field_warnings": warnings,
        "events_total": ev_total,
        "malformed_events": malformed,
        "duplicate_events_dropped": dup_events,
        "orphan_events": linked.orphan_events,
        "unknown_disposition_events": linked.unknown_disposition_events,
    }
    (out_dir / "data_quality.json").write_text(json.dumps(quality, indent=2) + "\n")

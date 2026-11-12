"""End-to-end: small hand-made drops with hand-computed expected output, plus
idempotency / incrementality checks on generated data."""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from solution.__main__ import main

REPO = Path(__file__).resolve().parents[2]


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ev(eid, etype, call, at, phone=None, **payload):
    e = {"event_id": eid, "event_type": etype, "workflow_id": "motor_renewal", "call_id": call,
         "occurred_at": at, "payload": payload}
    if phone:
        e["phone"] = phone
    return json.dumps(e)


@pytest.fixture
def drops(tmp_path):
    # drop_1: leads A and B; events for A, for B, for C (whose lead only arrives in drop_2),
    # a malformed line and an unknown-number orphan.
    write(tmp_path / "data/drop_1/leads/motor_renewal/batch_001.csv",
          "customer_name,phone,policy_number,premium_amount,preferred_language\n"
          "Asha Rao,98765 43210,MOT/2,\"₹1,000\",\n"            # A
          ",,,,\n"                                              # blank row: not counted
          "Bala K,(+91) 9123456780,MOT/1,Rs. 500,TA\n"          # B
          "No Phone,,MOT/3,,\n"                                 # rejected row 3
          "Asha R,098765-43210,,,\n")                           # row 4: rejected (missing ref)
    write(tmp_path / "data/drop_1/events/events_0001.jsonl", "\n".join([
        # A: call 1 at 23:50 IST Oct 4 (written in UTC), no answer
        ev("e1", "call.initiated", "c1", "2026-10-04T18:20:00Z", "+919876543210"),
        ev("e2", "call.ended", "c1", "2026-10-04T18:21:00Z", "9876543210", duration_sec=0),
        # A: call 2 Oct 5 connected, set callback, later overridden to renewed (override has no phone)
        ev("e3", "call.initiated", "c2", "2026-10-05 10:00:00", "91-98765-43210"),
        ev("e4", "call.connected", "c2", "2026-10-05T10:00:10+05:30", "9876543210"),
        ev("e5", "disposition.set", "c2", "2026-10-05T10:05:00+05:30", "9876543210",
           disposition_code="callback_requested"),
        ev("e6", "disposition.override", "c2", "2026-10-06T09:00:00+05:30",
           disposition_code="renewed", overridden_by="sup_1", reason="qa_review"),
        # B: telephony failure only
        ev("e7", "call.initiated", "c3", "2026-10-05T11:00:00+05:30", "9123456780"),
        ev("e8", "call.telephony_failed", "c3", "2026-10-05T11:00:02+05:30", "9123456780",
           reason="sip_503"),
        # C: arrives before its lead (lead is in drop_2)
        ev("e9", "call.initiated", "c4", "2026-10-05T12:00:00+05:30", "7000000001"),
        ev("e10", "call.connected", "c4", "2026-10-05T12:00:05+05:30", "7000000001"),
        ev("e11", "disposition.set", "c4", "2026-10-05T12:03:00+05:30", "7000000001",
           disposition_code="RENEWD"),                     # unknown code
        '{"event_id": "e12", "event_type": "call.ini',      # malformed
        ev("e13", "call.initiated", "c5", "2026-10-05T13:00:00+05:30", "6999999999"),  # never uploaded
        ev("e2", "call.ended", "c1", "2026-10-04T18:21:00Z", "9876543210", duration_sec=0),  # duplicate
    ]) + "\n")
    # drop_2: renamed + re-ordered headers, C's lead, a duplicate of A, a cross-drop duplicate event
    write(tmp_path / "data/drop_2/leads/motor_renewal/batch_003.csv",
          "Language,Insured Name,Policy No,Mobile No,Premium,agent_notes\n"
          "en,Chitra,MOT/0,+91 70000 00001,,VIP\n"
          "hi,Asha Rao,MOT/2b,9876543210,N/A,\n")       # duplicate of A: merges, warning for premium
    write(tmp_path / "data/drop_2/events/events_1001.jsonl", "\n".join([
        ev("e5", "disposition.set", "c2", "2026-10-05T10:05:00+05:30", "9876543210",
           disposition_code="callback_requested"),        # duplicate across drops
        ev("e14", "disposition.set", "c4", "2026-10-05T12:04:00+05:30", "7000000001",
           disposition_code="not_interested"),
    ]) + "\n\n")
    return tmp_path


def run(tmp_path, *args):
    main(["--db", str(tmp_path / "db"), *map(str, args)])


def read_reports(out: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in sorted(out.iterdir())}


def test_hand_computed_reports(drops):
    run(drops, "ingest", drops / "data/drop_1")
    run(drops, "ingest", drops / "data/drop_2")
    run(drops, "report", "--out", drops / "out")
    r = read_reports(drops / "out")

    assert r["lead_report.csv"].splitlines() == [
        "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,"
        "last_call_at,current_disposition,ever_success",
        # C: created by batch_003; events from drop_1 now link; unknown code ignored
        "motor_renewal,MOT/0,batch_003,+91******0001,completed,1,1,2026-10-05T12:00:00+05:30,not_interested,false",
        "motor_renewal,MOT/1,batch_001,+91******6780,in_progress_telephony_failed,1,0,2026-10-05T11:00:00+05:30,,false",
        # A: merged ref MOT/2b (last write wins), batch stays batch_001; override -> renewed
        "motor_renewal,MOT/2b,batch_001,+91******3210,completed,2,1,2026-10-05T10:00:00+05:30,renewed,true",
    ]
    assert r["batch_summary.csv"].splitlines()[1:] == [
        "motor_renewal,batch_001,2,0,1,0,1",
        "motor_renewal,batch_003,1,0,0,0,1",
    ]
    assert r["daily_overview.csv"].splitlines()[1:] == [
        "motor_renewal,2026-10-04,1,0,0.0000,0",   # 18:20Z = 23:50 IST on the 4th
        "motor_renewal,2026-10-05,3,2,0.6667,1",   # c2, c3, c4; success: A via override
    ]
    assert r["rejections.csv"].splitlines()[1:] == [
        "motor_renewal,batch_001,3,missing_required:phone",
        "motor_renewal,batch_001,4,missing_required:policy_number",
    ]
    assert json.loads(r["data_quality.json"]) == {
        "lead_rows_total": 6, "lead_rows_rejected": 2,
        "rejections_by_reason": {"missing_required:phone": 1, "missing_required:policy_number": 1},
        "duplicate_lead_rows_merged": 1,
        "field_warnings": 1,          # premium 'N/A' on the duplicate row
        "events_total": 16, "malformed_events": 1, "duplicate_events_dropped": 2,
        "orphan_events": 1,           # e13 only; C's events linked once its lead arrived
        "unknown_disposition_events": 1,
    }


def test_orphans_reflect_state_after_each_drop(drops):
    run(drops, "ingest", drops / "data/drop_1")
    run(drops, "report", "--out", drops / "out1")
    q = json.loads((drops / "out1/data_quality.json").read_text())
    assert q["orphan_events"] == 4 and q["unknown_disposition_events"] == 0  # e9, e10, e11, e13


def test_reingest_changes_nothing_and_drop_order_equals_all_at_once(drops):
    run(drops, "ingest", drops / "data/drop_1")
    run(drops, "ingest", drops / "data/drop_1")
    run(drops, "ingest", drops / "data/drop_2")
    run(drops, "ingest", drops / "data/drop_1", drops / "data/drop_2")
    run(drops, "report", "--out", drops / "a")
    main(["--db", str(drops / "db2"), "ingest", str(drops / "data")])
    main(["--db", str(drops / "db2"), "report", "--out", str(drops / "b")])
    assert read_reports(drops / "a") == read_reports(drops / "b")


def test_changed_file_with_same_name_is_refused(drops):
    run(drops, "ingest", drops / "data/drop_1")
    with open(drops / "data/drop_1/leads/motor_renewal/batch_001.csv", "a") as f:
        f.write("Extra,9000000000,MOT/9,,\n")
    with pytest.raises(Exception, match="different contents"):
        run(drops, "ingest", drops / "data/drop_1")


def test_no_plaintext_pii_and_ledger_is_append_only(drops):
    run(drops, "ingest", drops / "data")
    raw = b"".join(p.read_bytes() for p in (drops / "db").glob("ledger.db*"))
    for needle in [b"9876543210", b"9123456780", b"Asha", b"Bala", b"Chitra"]:
        assert needle not in raw
    conn = sqlite3.connect(drops / "db/ledger.db")
    for sql in ["UPDATE events SET event_type='x'", "DELETE FROM events"]:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            conn.execute(sql)


@pytest.mark.parametrize("seed", [42, 7])
def test_generated_data_incremental_equals_all_at_once(tmp_path, seed):
    subprocess.run([sys.executable, str(REPO / "assignment-a-data/tools/generate_data.py"),
                    "--seed", str(seed), "--scale", "small", "--out", str(tmp_path / "data")],
                   check=True, capture_output=True)
    for d in ["drop_1", "drop_1", "drop_2"]:  # includes a re-run of drop_1
        main(["--db", str(tmp_path / "x"), "ingest", str(tmp_path / "data" / d)])
    main(["--db", str(tmp_path / "x"), "report", "--out", str(tmp_path / "rx")])
    main(["--db", str(tmp_path / "y"), "ingest", str(tmp_path / "data")])
    main(["--db", str(tmp_path / "y"), "report", "--out", str(tmp_path / "ry")])
    assert read_reports(tmp_path / "rx") == read_reports(tmp_path / "ry")

    q = json.loads((tmp_path / "rx/data_quality.json").read_text())
    assert q["lead_rows_rejected"] == sum(q["rejections_by_reason"].values())
    rows = (tmp_path / "rx/lead_report.csv").read_text().splitlines()[1:]
    leads_total = sum(int(l.split(",")[2]) for l in
                      (tmp_path / "rx/batch_summary.csv").read_text().splitlines()[1:])
    assert len(rows) == leads_total == q["lead_rows_total"] - q["lead_rows_rejected"] - q["duplicate_lead_rows_merged"]

"""Effective disposition, linking (orphans, overrides via call_id) and bucket rules."""
from datetime import datetime, timedelta

from solution.catalog import Catalog
from solution.derive import Call, Event, effective_disposition, lead_bucket, link_events
from solution.normalize import IST

CAT = Catalog().dispositions
WF = "motor_renewal"
LEAD = (WF, "hash_a")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
_seq = iter(range(1, 10_000))


def ev(etype, minutes, call="c1", phone="hash_a", code=None, wf=WF):
    return Event(next(_seq), etype, wf, call, T0 + timedelta(minutes=minutes),
                 None if etype == "disposition.override" else phone, code)


# ---------------------------------------------------------------- effective disposition

def test_latest_set_wins_without_override():
    evs = [ev("disposition.set", 5, code="callback_requested"), ev("disposition.set", 9, code="renewed")]
    assert effective_disposition(evs) == "renewed"


def test_any_override_beats_sets_even_if_a_set_is_later():
    evs = [ev("disposition.override", 5, code="not_interested"), ev("disposition.set", 60, code="renewed")]
    assert effective_disposition(evs) == "not_interested"


def test_latest_override_by_time_not_by_arrival():
    later = ev("disposition.override", 90, code="renewed")
    earlier = ev("disposition.override", 30, code="not_interested")   # arrives after `later`
    assert effective_disposition([ev("disposition.set", 1, code="dnd"), later, earlier]) == "renewed"


def test_no_dispositions():
    assert effective_disposition([]) is None


# ---------------------------------------------------------------- linking

def test_full_call_is_linked():
    r = link_events([ev("call.initiated", 0), ev("call.connected", 1),
                     ev("disposition.set", 4, code="renewed")], {LEAD}, CAT)
    assert r.orphan_events == 0
    [call] = r.calls
    assert call.connected and call.effective_disposition == "renewed" and call.lead_key == LEAD


def test_unknown_phone_is_orphan_and_creates_no_call():
    r = link_events([ev("call.initiated", 0, phone="hash_unknown")], {LEAD}, CAT)
    assert r.orphan_events == 1 and r.calls == []


def test_invalid_phone_is_orphan():
    r = link_events([ev("call.initiated", 0, phone=None)], {LEAD}, CAT)
    assert r.orphan_events == 1


def test_same_phone_other_workflow_is_orphan():
    r = link_events([ev("call.initiated", 0, wf="policy_feedback")], {LEAD}, CAT)
    assert r.orphan_events == 1


def test_override_links_through_call_id():
    r = link_events([ev("call.initiated", 0), ev("disposition.set", 4, code="callback_requested"),
                     ev("disposition.override", 300, code="renewed")], {LEAD}, CAT)
    assert r.orphan_events == 0
    assert r.calls[0].effective_disposition == "renewed"


def test_override_for_unlinked_call_is_orphan():
    r = link_events([ev("call.initiated", 0, call="c9", phone="hash_unknown"),
                     ev("disposition.override", 300, call="c9", code="renewed")], {LEAD}, CAT)
    assert r.orphan_events == 2


def test_unknown_disposition_counted_and_ignored():
    r = link_events([ev("call.initiated", 0), ev("disposition.set", 4, code="renewed"),
                     ev("disposition.set", 9, code="RENEWD")], {LEAD}, CAT)
    assert r.unknown_disposition_events == 1
    assert r.calls[0].effective_disposition == "renewed"


def test_orphan_takes_precedence_over_unknown_disposition():
    r = link_events([ev("disposition.set", 4, phone="hash_unknown", code="RENEWD")], {LEAD}, CAT)
    assert (r.orphan_events, r.unknown_disposition_events) == (1, 0)


def test_call_without_initiated_is_not_counted():
    r = link_events([ev("call.connected", 1), ev("disposition.set", 4, code="renewed")], {LEAD}, CAT)
    assert r.calls == [] and r.orphan_events == 0


# ---------------------------------------------------------------- buckets

def call(eff=None, tf=False, connected=False):
    return Call(WF, "c", LEAD, T0, connected=connected, telephony_failed=tf, effective_disposition=eff)


def test_bucket_no_calls():
    assert lead_bucket([], CAT) == "yet_to_reach_out"


def test_bucket_completed_when_any_call_is_terminal():
    assert lead_bucket([call(tf=True), call("not_interested")], CAT) == "completed"


def test_bucket_completed_beats_all_telephony_failed():
    assert lead_bucket([call("renewed", tf=True)], CAT) == "completed"


def test_bucket_all_telephony_failed():
    assert lead_bucket([call(tf=True), call(tf=True)], CAT) == "in_progress_telephony_failed"


def test_bucket_dialed_when_not_every_call_failed():
    assert lead_bucket([call(tf=True), call()], CAT) == "in_progress_dialed"


def test_bucket_non_terminal_disposition_is_dialed():
    assert lead_bucket([call("callback_requested", connected=True)], CAT) == "in_progress_dialed"


def test_override_to_non_terminal_reopens_lead():
    # set=renewed (terminal) overridden to callback_requested (not terminal)
    r = link_events([ev("call.initiated", 0), ev("disposition.set", 4, code="renewed"),
                     ev("disposition.override", 60, code="callback_requested")], {LEAD}, CAT)
    assert lead_bucket(r.calls, CAT) == "in_progress_dialed"

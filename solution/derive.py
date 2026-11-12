"""Derived facts from the spec: linking, calls, effective disposition, buckets.

Pure functions over plain data (no database), so every rule here is unit-tested directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .catalog import Disposition

OVERRIDE = "disposition.override"
SET = "disposition.set"


@dataclass
class Event:
    seq: int                     # arrival order, used only to break exact timestamp ties
    event_type: str | None
    workflow_id: str | None
    call_id: str | None
    occurred_at: datetime
    phone_hash: str | None
    disposition_code: str | None = None


@dataclass
class Call:
    workflow_id: str
    call_id: str
    lead_key: tuple[str, str]
    initiated_at: datetime
    connected: bool = False
    telephony_failed: bool = False
    effective_disposition: str | None = None


@dataclass
class LinkResult:
    calls: list[Call] = field(default_factory=list)
    orphan_events: int = 0
    unknown_disposition_events: int = 0


def effective_disposition(disposition_events: list[Event]) -> str | None:
    """Latest override by occurred_at; if no override, latest set; otherwise None.
    Only known (catalogue) codes should be passed in. Ties on time fall back to arrival order."""
    overrides = [e for e in disposition_events if e.event_type == OVERRIDE]
    pool = overrides or [e for e in disposition_events if e.event_type == SET]
    if not pool:
        return None
    return max(pool, key=lambda e: (e.occurred_at, e.seq)).disposition_code


def link_events(events: list[Event], lead_keys: set[tuple[str, str]],
                catalog: dict[tuple[str, str], Disposition]) -> LinkResult:
    """Links every event to a lead (or counts it as an orphan) and assembles calls."""
    out = LinkResult()

    # 1. Events carrying a phone link by (workflow_id, phone hash).
    #    A call belongs to the lead its events point at; overrides then link via call_id.
    linked: list[tuple[Event, tuple[str, str]]] = []
    call_lead: dict[tuple[str, str], tuple[str, str]] = {}
    overrides: list[Event] = []
    for e in events:
        if e.event_type == OVERRIDE:
            overrides.append(e)
            continue
        key = (e.workflow_id, e.phone_hash)
        if e.phone_hash is None or key not in lead_keys:
            out.orphan_events += 1
            continue
        linked.append((e, key))
        if e.call_id is not None:
            ck = (e.workflow_id, e.call_id)
            # Prefer the lead of the call.initiated event; otherwise the first linked event.
            if e.event_type == "call.initiated" or ck not in call_lead:
                call_lead[ck] = key

    # 2. Overrides have no phone: link through call_id to the lead of that call's other events.
    for e in overrides:
        key = call_lead.get((e.workflow_id, e.call_id))
        if key is None:
            out.orphan_events += 1
        else:
            linked.append((e, key))

    # 3. Group linked events by call. Unknown dispositions are counted and ignored.
    by_call: dict[tuple[str, str], list[Event]] = {}
    for e, _ in linked:
        if e.event_type in (SET, OVERRIDE) and (e.workflow_id, e.disposition_code) not in catalog:
            out.unknown_disposition_events += 1
            continue
        if e.call_id is not None:
            by_call.setdefault((e.workflow_id, e.call_id), []).append(e)

    # 4. A call exists only if its call.initiated is present (and linked).
    for (wf, call_id), evs in by_call.items():
        initiated = [e for e in evs if e.event_type == "call.initiated"]
        if not initiated:
            continue
        first = min(initiated, key=lambda e: (e.occurred_at, e.seq))
        out.calls.append(Call(
            workflow_id=wf,
            call_id=call_id,
            lead_key=call_lead[(wf, call_id)],
            initiated_at=first.occurred_at,
            connected=any(e.event_type == "call.connected" for e in evs),
            telephony_failed=any(e.event_type == "call.telephony_failed" for e in evs),
            effective_disposition=effective_disposition(
                [e for e in evs if e.event_type in (SET, OVERRIDE)]),
        ))
    return out


def lead_bucket(calls: list[Call], catalog: dict[tuple[str, str], Disposition]) -> str:
    """First matching bucket, in the spec's order."""
    if not calls:
        return "yet_to_reach_out"
    if any(c.effective_disposition and catalog[(c.workflow_id, c.effective_disposition)].is_terminal
           for c in calls):
        return "completed"
    if all(c.telephony_failed for c in calls):
        return "in_progress_telephony_failed"
    return "in_progress_dialed"


def is_success(call: Call, catalog: dict[tuple[str, str], Disposition]) -> bool:
    return bool(call.effective_disposition) and catalog[(call.workflow_id, call.effective_disposition)].is_success

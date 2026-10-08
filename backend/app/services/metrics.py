from datetime import datetime
from collections import defaultdict
from sqlalchemy import select
from backend.app import models as m


def metrics(s, change_id: int | None = None):
    query = select(m.Event)
    if change_id is not None:
        query = query.where(m.Event.change_request_id == change_id)
    events = list(s.scalars(query.order_by(m.Event.timestamp, m.Event.id)))
    triage = {}
    ai_ids = set()
    manual = {}
    escaped = set()
    stages: dict[str, float] = defaultdict(float)
    per_change = defaultdict(list)
    for e in events:
        detail = e.detail or {}
        if e.entity_type == "finding_created" and detail.get("source") == "ai":
            ai_ids.add(e.entity_id)
        if e.entity_type == "triage" and detail.get("source") == "ai":
            ai_ids.add(e.entity_id)
            triage[e.entity_id] = e.to_state
        if e.entity_type == "manual":
            manual[e.entity_id] = detail
        if e.entity_type == "escaped":
            escaped.add(e.entity_id)
        if e.entity_type == "change":
            per_change[e.change_request_id].append(e)
    totals = []
    for id, sequence in per_change.items():
        duration = 0.0
        for before, after in zip(sequence, sequence[1:]):
            if before.to_state in ("Approved for release", "Release blocked"):
                continue
            minutes = max(
                0,
                (
                    datetime.fromisoformat(after.timestamp)
                    - datetime.fromisoformat(before.timestamp)
                ).total_seconds()
                / 60,
            )
            stages[before.to_state] += minutes
            duration += minutes
        change = s.get(m.Change, id)
        totals.append(
            {
                "change_request_id": id,
                "title": change.title if change else str(id),
                "minutes": round(duration, 2),
                "stage": "elapsed transitions, not labour time",
            }
        )
    denominator = len(ai_ids)

    def rate(n, d):
        return round(n / d, 4) if d else None

    pilot = s.scalar(select(m.Pilot))
    return {
        "ai_findings": denominator,
        "ai_triaged": len(triage),
        "ai_valid_rate": rate(sum(x == "valid" for x in triage.values()), denominator),
        "ai_noise_rate": rate(
            sum(x in ("not an issue", "by design") for x in triage.values()), denominator
        ),
        "ai_miss_rate": rate(sum(not x.get("matched") for x in manual.values()), len(manual)),
        "manual_findings": len(manual),
        "escaped_defects": len(escaped),
        "stage_minutes": dict(stages),
        "per_change": totals,
        "pilot": pilot.data if pilot else {},
        "limitations": "Elapsed stage time comes from audit events, not measured active labour. AI rates are provisional until every AI finding is triaged. Manual matching uses the same identified element, popup and viewport. Pilot preparation time was not measured; no savings claim is made.",
    }

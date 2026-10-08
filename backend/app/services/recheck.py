from sqlalchemy import select
from backend.app import models as m
from backend.app.services.capture.rules import SOURCE_MAP
from backend.app.services.capture.jobs import enqueue
from backend.app.services.events import status, event


def start_recheck(s, change, role: str):
    if change.status not in ("Fix in progress", "Synced to Asana", "Release blocked", "Recheck"):
        raise ValueError("Recheck follows synced findings and developer fixes.")
    queues = list(
        s.scalars(
            select(m.Queue).filter_by(change_request_id=change.id, status="Fixed: awaiting recheck")
        )
    )
    if not queues:
        raise ValueError("The Developer must mark at least one fix before recheck.")
    affected = []
    seen = set()
    vp_ids = set()
    for q in queues:
        finding = s.get(m.Finding, q.finding_id)
        evidence = [s.get(m.Finding, id) for id in q.evidence_ids]
        f = next(
            (
                x
                for x in evidence
                if x
                and (not finding.element or x.element == finding.element)
                and (not finding.recipe_id or x.recipe_id == finding.recipe_id)
                and (not finding.viewport or x.viewport == finding.viewport)
            ),
            finding,
        )
        selector = f.element
        if selector == "[data-testid=feature-item]":
            selector = "[data-testid=feature-list-premium]"
        profile = s.scalar(select(m.Viewport).filter_by(name=f.viewport))
        if not f.recipe_id or not profile:
            raise ValueError(
                "Recheck needs an explicitly identified popup recipe and viewport for every fixed item."
            )
        key = (f.recipe_id, f.viewport, selector)
        if key not in seen:
            affected.append(
                {"recipe_id": f.recipe_id, "viewport": f.viewport, "selector": selector}
            )
            seen.add(key)
        vp_ids.add(profile.id)
    run = enqueue(s, change, "v2", sorted(vp_ids), role, affected=affected)
    run.scope_json = {**run.scope_json, "recheck_queue_ids": [q.id for q in queues]}
    status(s, change, "Recheck", role, {"run_id": run.id, "affected": affected})
    return run


def finish_recheck(s, run):
    results = list(s.scalars(select(m.RuleResult).filter_by(capture_run_id=run.id)))
    for id in run.scope_json.get("recheck_queue_ids", []):
        q = s.get(m.Queue, id)
        f = s.get(m.Finding, q.finding_id)
        code = f.rule_code or next((k for k, v in SOURCE_MAP.items() if v == f.source_ref), None)
        evidence = [s.get(m.Finding, x) for x in q.evidence_ids]
        anchor = next(
            (
                x
                for x in evidence
                if x
                and (not f.element or x.element == f.element)
                and (not f.recipe_id or x.recipe_id == f.recipe_id)
                and (not f.viewport or x.viewport == f.viewport)
            ),
            f,
        )
        vp = s.scalar(select(m.Viewport).filter_by(name=anchor.viewport))
        matched = [
            r
            for r in results
            if r.rule_code == code
            and r.recipe_id == anchor.recipe_id
            and vp
            and r.viewport_profile_id == vp.id
            and (r.selector == anchor.element or code == "MIN_FONT_MOBILE")
        ]
        old = q.status
        if matched and all(r.status == "Passed" for r in matched):
            q.status = "Closed"
            q.next_action = "Recheck verified the previously confirmed condition. Release approval remains human."
            f.status = "Fixed verified"
        else:
            q.status = "Reopened"
            q.next_action = "QA reviewer inspects the failed or unverifiable recheck."
            q.accountable_role = "QA reviewer"
        event(
            s,
            run.change_request_id,
            "recheck",
            q.id,
            old,
            q.status,
            "QA reviewer",
            {
                "run_id": run.id,
                "matched_rule_result_ids": [r.id for r in matched],
                "automated_condition_only": True,
            },
        )

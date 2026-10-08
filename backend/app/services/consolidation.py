from typing import Literal, Any
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import add, record
from backend.app.services.events import status, event
from backend.app.services.workflow import untriaged
from backend.app.services.capture.rules import SOURCE_MAP


class QueueProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: list[str]
    issue_summary: str
    status: Literal[
        "Needs evidence",
        "Needs clarification",
        "Change request: needs approval",
        "Blocked",
        "Ready for fix",
    ]
    missing_evidence: str
    next_action: str
    accountable_role: Literal[
        "QA reviewer", "QA lead", "Designer", "Requester / Product owner", "Developer"
    ]
    tool_hint: str


FIXTURE: dict[str, tuple[Any, Any, str]] = {
    "F1": (
        "Needs evidence",
        "QA reviewer",
        "Capture the header at mobile-390; then ask Designer about intended spacing.",
    ),
    "F2": (
        "Needs clarification",
        "QA reviewer",
        "Identify the headline and exact popup; then ask Designer.",
    ),
    "F3": (
        "Change request: needs approval",
        "Requester / Product owner",
        "Approve the proposed HOURS copy before treating the request as a defect.",
    ),
    "F4": ("Blocked", "QA reviewer", "Ask the reviewer to identify the element and viewport"),
    "F5": (
        "Needs clarification",
        "Designer",
        "Confirm divider behaviour when scrolling is not required.",
    ),
    "F6": (
        "Needs evidence",
        "Designer",
        "Check approved feature order; the difference may be intentional.",
    ),
}


def mock_proposal(f) -> QueueProposal:
    if f.source == "imported_comment":
        state, role, next_action = FIXTURE.get(
            f.source_ref, ("Needs evidence", "QA reviewer", "Collect supporting evidence.")
        )
    elif not f.element or not f.viewport:
        state, role, next_action = (
            "Blocked",
            "QA reviewer",
            "Ask the reviewer to identify the element and viewport",
        )
    elif f.status == "Needs evidence" or (f.ai_confidence is not None and f.ai_confidence < 0.6):
        state, role, next_action = (
            "Needs evidence",
            "QA reviewer",
            "Collect evidence; AI confidence is too low to judge.",
        )
    elif f.kind == "change_request":
        state, role, next_action = (
            "Change request: needs approval",
            "Requester / Product owner",
            "Approve or reject the proposed requirement.",
        )
    else:
        state, role, next_action = (
            "Needs evidence",
            "QA reviewer",
            "Inspect evidence and record a human triage decision.",
        )
    return QueueProposal(
        source_ids=[f.source_ref],
        issue_summary=f.title,
        status=state,
        missing_evidence="Unconfirmed intent or evidence" if state != "Ready for fix" else "",
        next_action=next_action,
        accountable_role=role,
        tool_hint="DOM measurement + screenshot at "
        + (f.viewport or "an explicitly identified viewport"),
    )


def populate(s, change_id: int):
    findings = list(s.scalars(select(m.Finding).filter_by(change_request_id=change_id)))
    rows = []
    for f in findings:
        q = s.scalar(select(m.Queue).filter_by(change_request_id=change_id, finding_id=f.id))
        if q and f.status == "Fixed verified":
            rows.append(q)
            continue
        if q and q.group_id:
            continue
        if q and q.status in ("Fixed: awaiting recheck", "Reopened"):
            rows.append(q)
            continue
        proposal = mock_proposal(f).model_dump()
        evidence = [
            r
            for r in findings
            if r.source == "rule"
            and r.capture_run_id
            and (
                f.rule_code == r.rule_code
                if f.rule_code
                else SOURCE_MAP.get(r.rule_code) == f.source_ref
            )
            and (not f.element or f.element == r.element)
            and (not f.viewport or f.viewport == r.viewport)
            and (not f.recipe_id or f.recipe_id == r.recipe_id)
        ]
        if f.triage_outcome in ("not an issue", "by design", "out of scope"):
            proposal.update(
                status="Closed",
                missing_evidence="",
                next_action="Human disposition recorded. Retain the audit trail.",
            )
        elif f.triage_outcome == "valid":
            if (
                f.kind == "defect"
                and f.element
                and f.viewport
                and (evidence or f.evidence_screenshot_id)
            ):
                proposal.update(
                    status="Ready for fix",
                    missing_evidence="",
                    next_action="Developer implements the confirmed correction, then QA rechecks.",
                    accountable_role="Developer",
                )
            elif f.kind == "change_request":
                proposal.update(
                    status="Change request: needs approval",
                    accountable_role="Requester / Product owner",
                    next_action="Product owner approves the requested change.",
                )
            elif f.kind == "question":
                proposal.update(
                    status="Needs clarification",
                    accountable_role="Designer",
                    next_action="Designer answers the open question.",
                )
        if q:
            for key, value in proposal.items():
                setattr(q, key, value)
        else:
            q = add(s, m.Queue, change_request_id=change_id, finding_id=f.id, **proposal)
        q.evidence_ids = [r.id for r in evidence]
        rows.append(q)
    if not s.scalar(select(m.Group).filter_by(change_request_id=change_id)):
        originals = {f.source_ref: f for f in findings if f.source == "imported_comment"}
        if "F1" in originals and "F2" in originals:
            group = add(
                s,
                m.Group,
                change_request_id=change_id,
                label="Possible group A: F1 + F2",
                state="possible",
                rationale="Both mention tight spacing at 390px, but element and popup are not established as the same. Keep separate.",
            )
            for key in ("F1", "F2"):
                add(s, m.Member, group_id=group.id, finding_id=originals[key].id)
    return rows


def consolidate(s, change, role: str):
    if change.status != "In human review":
        raise ValueError("Consolidation follows the AI first pass and human review.")
    pending = untriaged(s, change.id)
    if pending:
        raise ValueError(
            f"Cannot move to Queue consolidated: {len(pending)} AI or rule findings not yet triaged."
        )
    prompt = s.scalar(
        select(m.Prompt)
        .filter_by(name="Review consolidation", active=True)
        .order_by(m.Prompt.version.desc())
    )
    if not prompt:
        raise ValueError("No active versioned consolidation prompt.")
    rows = populate(s, change.id)
    # Deterministic proposal provider. Group approval is always a distinct human action.
    event(
        s,
        change.id,
        "consolidation",
        change.id,
        "",
        "Proposed",
        "QA lead",
        {
            "provider": "MockVisionProvider deterministic consolidation",
            "prompt_id": prompt.id,
            "prompt_version": prompt.version,
            "rows": [record(r) for r in rows],
        },
    )
    status(s, change, "Queue consolidated", role)
    return rows


def group_action(s, group, action: str, role: str, reason: str, label: str = ""):
    members = [
        s.get(m.Finding, x.finding_id)
        for x in s.scalars(select(m.Member).filter_by(group_id=group.id))
    ]
    old = group.state
    if action == "confirm":
        keys = {(f.element, f.viewport, f.recipe_id) for f in members}
        if len(keys) != 1 or any(
            not f.element or not f.viewport or not f.recipe_id for f in members
        ):
            raise ValueError(
                "These findings do not identify the same element, popup and viewport. Split or reject the proposed grouping; keep their source rows separate."
            )
        if any(f.triage_outcome != "valid" or f.kind != "defect" for f in members):
            raise ValueError("Only human-confirmed defects can be merged into a fix group.")
        group.state = "confirmed"
        group.confirmed_by = role
        rows = list(
            s.scalars(select(m.Queue).where(m.Queue.finding_id.in_([f.id for f in members])))
        )
        for q in rows:
            q.group_id = group.id
        # Keep rows for traceability; only the first is active in the consolidated queue.
        for q in rows[1:]:
            q.status = "Closed"
            q.next_action = "Included in confirmed group " + str(group.id)
        if rows:
            rows[0].source_ids = [f.source_ref for f in members]
            rows[0].issue_summary = label or group.label
    elif action in ("split", "reject"):
        group.state = "rejected"
        group.confirmed_by = role
        for q in s.scalars(select(m.Queue).filter_by(group_id=group.id)):
            q.group_id = 0
        s.flush()
        populate(s, group.change_request_id)
    elif action == "edit":
        if not label:
            raise ValueError("Enter a revised grouping label.")
        group.label = label
    else:
        raise ValueError("Unknown grouping action.")
    group.rationale = reason
    event(
        s,
        group.change_request_id,
        "group",
        group.id,
        old,
        group.state,
        role,
        {"action": action, "reason": reason, "source_ids": [f.source_ref for f in members]},
    )

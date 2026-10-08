from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session, record
from backend.app.api.deps import require, current_role
from backend.app.api.changes import needed
from backend.app.services.events import event, status
from backend.app.services.recheck import start_recheck
from backend.app.services.workflow import untriaged

router = APIRouter(prefix="/api", tags=["Fixes & release"])


class Reason(BaseModel):
    reason: str = Field(min_length=5)


class Resolution(Reason):
    decision: str = "approved"


@router.post("/queue/items/{id}/fixed")
def fixed(id: int, body: Reason, role: str = Depends(current_role)):
    require(role, "Developer")
    with session() as s:
        q = s.get(m.Queue, id)
        if not q or q.status not in ("Ready for fix", "Reopened"):
            raise HTTPException(409, "Only Ready for fix or Reopened items can be marked fixed.")
        change = needed(s, q.change_request_id)
        if change.status not in (
            "Synced to Asana",
            "Fix in progress",
            "Release blocked",
            "Recheck",
        ):
            raise HTTPException(409, "QA lead must sync the reviewed queue before developer fixes.")
        old = q.status
        q.status = "Fixed: awaiting recheck"
        q.next_action = "QA reviewer runs the affected-only build v2 recheck."
        q.accountable_role = "QA reviewer"
        event(s, q.change_request_id, "queue", q.id, old, q.status, role, body.model_dump())
        status(s, change, "Fix in progress", role)
        s.commit()
        return record(q)


@router.post("/queue/items/{id}/resolve")
def resolve(id: int, body: Resolution, role: str = Depends(current_role)):
    require(role, "Requester / Product owner", "Designer")
    with session() as s:
        q = s.get(m.Queue, id)
        if not q or q.status not in ("Needs clarification", "Change request: needs approval"):
            raise HTTPException(
                409,
                "This item requires evidence, a fix or explicit location clarification instead.",
            )
        if q.status == "Change request: needs approval":
            require(role, "Requester / Product owner")
        f = s.get(m.Finding, q.finding_id)
        if f is None:
            raise HTTPException(409, "Source finding no longer exists.")
        old = q.status
        f.triage_outcome = "by design" if body.decision == "approved" else "out of scope"
        f.status = "Dismissed"
        f.kind = "change_request" if q.status.startswith("Change request") else "question"
        q.status = "Closed"
        q.next_action = "Human intent decision recorded. New implementation scope belongs to a separate change request."
        event(s, q.change_request_id, "intent", q.id, old, "Closed", role, body.model_dump())
        s.commit()
        return record(q)


@router.post("/change-requests/{id}/recheck")
def recheck(id: int, role: str = Depends(current_role)):
    require(role, "QA reviewer", "QA lead")
    with session() as s:
        run = start_recheck(s, needed(s, id), role)
        s.commit()
        return record(run)


@router.post("/release/{id}/approve")
def release(id: int, body: Reason, role: str = Depends(current_role)):
    require(role, "Release owner")
    with session() as s:
        change = needed(s, id)
        if change.status not in ("Recheck", "Synced to Asana", "Release blocked"):
            raise HTTPException(
                409,
                "Release review follows QA consolidation, Asana sync and any required rechecks.",
            )
        blockers = list(
            s.scalars(
                select(m.Queue).where(m.Queue.change_request_id == id, m.Queue.status != "Closed")
            )
        )
        pending = untriaged(s, id)
        active = s.scalar(
            select(m.Run).where(
                m.Run.change_request_id == id, m.Run.status.in_(["Queued", "Running"])
            )
        )
        if blockers or pending or active:
            status(
                s,
                change,
                "Release blocked",
                role,
                {
                    "open_queue_items": [x.id for x in blockers],
                    "untriaged": len(pending),
                    "reason": body.reason,
                },
            )
            s.commit()
            raise HTTPException(
                409,
                f"Release blocked: {len(blockers)} open queue items, {len(pending)} untriaged findings"
                + (" and an active capture." if active else "."),
            )
        status(s, change, "Approved for release", role, body.model_dump())
        s.commit()
        return record(change)

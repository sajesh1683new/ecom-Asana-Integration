from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session, record, all_rows
from backend.app.api.deps import require, current_role
from backend.app.api.changes import needed
from backend.app.services.consolidation import populate, consolidate, group_action
from backend.app.services.capture.safety import validate_selector
from backend.app.services.events import event

router = APIRouter(prefix="/api", tags=["Review queue"])


class GroupDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5)
    label: str = ""


class Clarify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    element: str
    viewport: str
    recipe_id: int
    reason: str = Field(min_length=5)


@router.get("/queue/{change_id}")
def queue(change_id: int):
    with session() as s:
        needed(s, change_id)
        # Refresh only derived queue projections, never overwrite human decisions.
        populate(s, change_id)
        s.commit()
        return {
            "items": all_rows(s, m.Queue, change_request_id=change_id),
            "groups": [
                {
                    **record(g),
                    "members": [
                        record(s.get(m.Finding, x.finding_id))
                        for x in s.scalars(select(m.Member).filter_by(group_id=g.id))
                    ],
                }
                for g in s.scalars(select(m.Group).filter_by(change_request_id=change_id))
            ],
        }


@router.post("/queue/{change_id}/consolidate")
def consolidate_queue(change_id: int, role: str = Depends(current_role)):
    require(role, "QA lead")
    with session() as s:
        result = consolidate(s, needed(s, change_id), role)
        s.commit()
        return [record(r) for r in result]


@router.post("/queue/groups/{id}/{action}")
def decide_group(
    id: int,
    action: Literal["confirm", "split", "reject", "edit"],
    body: GroupDecision,
    role: str = Depends(current_role),
):
    require(role, "QA lead")
    with session() as s:
        group = s.get(m.Group, id)
        if not group:
            raise HTTPException(404, "Grouping not found.")
        group_action(s, group, action, role, body.reason, body.label)
        s.commit()
        return record(group)


@router.post("/findings/{id}/clarify")
def clarify(id: int, body: Clarify, role: str = Depends(current_role)):
    require(role, "QA reviewer", "Designer", "QA lead")
    validate_selector(body.element)
    with session() as s:
        finding = s.get(m.Finding, id)
        if not finding:
            raise HTTPException(404, "Finding not found.")
        if not s.scalar(select(m.Viewport).filter_by(name=body.viewport)):
            raise HTTPException(422, "Choose a named viewport profile.")
        recipe = s.get(m.Recipe, body.recipe_id)
        if not recipe or recipe.change_request_id != finding.change_request_id:
            raise HTTPException(422, "Choose a recipe for this change.")
        old = {
            "element": finding.element,
            "viewport": finding.viewport,
            "recipe_id": finding.recipe_id,
        }
        finding.element = body.element
        finding.viewport = body.viewport
        finding.recipe_id = body.recipe_id
        event(
            s,
            finding.change_request_id,
            "clarification",
            id,
            "Unknown",
            "Identified",
            role,
            {"before": old, **body.model_dump(), "original_comment_preserved": True},
        )
        populate(s, finding.change_request_id)
        s.commit()
        return record(finding)

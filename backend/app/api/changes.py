import os
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete
from backend.app import models as m
from backend.app.db import session, all_rows, record, add
from backend.app.api.deps import current_role, require
from backend.app.services.intake import intake
from backend.app.services.events import status
from backend.app.services.capture.safety import validate_recipe, validate_selector
from backend.app.integrations.asana.factory import get_client

router = APIRouter(prefix="/api", tags=["Change requests"])


class IntakeEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3)
    target_url: str
    sections: list[dict[str, Any]] = []
    recipes: list[dict[str, Any]] = []
    viewport_ids: list[int] = []


def needed(s, id):
    obj = s.get(m.Change, id)
    if not obj:
        raise HTTPException(404, "Change request not found.")
    return obj


@router.get("/change-requests")
def changes():
    with session() as s:
        return all_rows(s, m.Change)


@router.get("/change-requests/{id}")
def detail(id: int):
    with session() as s:
        change = needed(s, id)
        return {
            **record(change),
            "intake": intake(s, change),
            "sections": all_rows(s, m.Section, change_request_id=id),
            "recipes": all_rows(s, m.Recipe, change_request_id=id),
            "runs": all_rows(s, m.Run, change_request_id=id),
            "events": all_rows(s, m.Event, change_request_id=id),
            "specs": all_rows(s, m.Spec),
            "viewports": all_rows(s, m.Viewport),
        }


@router.get("/change-requests/{id}/intake-check")
def check(id: int):
    with session() as s:
        return intake(s, needed(s, id))


@router.post("/change-requests")
def create(body: IntakeEdit, role: str = Depends(current_role)):
    require(role, "Requester / Product owner", "Designer", "QA lead")
    with session() as s:
        task = get_client(s).create_task(
            name=body.title,
            project_gid=os.getenv("ASANA_PROJECT_GID", "1000"),
            section_gid="1001",
            notes="Created from Visual QA Bridge",
        )
        change = add(
            s,
            m.Change,
            asana_gid=task["gid"],
            title=body.title,
            code="NEW",
            target_url=body.target_url,
        )
        id = change.id
        s.commit()
    return edit(id, body, role)


@router.patch("/change-requests/{id}")
def edit(id: int, body: IntakeEdit, role: str = Depends(current_role)):
    require(role, "Requester / Product owner", "Designer", "QA lead")
    with session() as s:
        change = needed(s, id)
        if change.status not in ("Draft", "Intake incomplete", "Ready for QA"):
            raise HTTPException(
                409,
                "Intake cannot change during an active review. Complete or return the review first.",
            )
        for section in body.sections:
            validate_selector(section.get("selector", ""))
        for recipe in body.recipes:
            validate_recipe(
                recipe.get("steps_json", recipe.get("steps", [])),
                recipe.get("allow_cart_interaction", False),
            )
        change.title = body.title
        change.target_url = body.target_url
        change.viewport_ids = body.viewport_ids
        s.execute(delete(m.Section).where(m.Section.change_request_id == id))
        s.execute(delete(m.Recipe).where(m.Recipe.change_request_id == id))
        for section in body.sections:
            add(
                s,
                m.Section,
                change_request_id=id,
                name=section.get("name", "Section"),
                selector=section["selector"],
                design_spec_id=section.get("design_spec_id", 0),
                notes=section.get("notes", ""),
            )
        for recipe in body.recipes:
            add(
                s,
                m.Recipe,
                change_request_id=id,
                name=recipe.get("name", "Recipe"),
                steps_json=recipe.get("steps_json", recipe.get("steps", [])),
                allow_cart_interaction=recipe.get("allow_cart_interaction", False),
                version=recipe.get("version", 0) + 1,
            )
        check = intake(s, change)
        change.intake_complete = check["complete"]
        status(s, change, "Ready for QA" if check["complete"] else "Intake incomplete", role, check)
        if not check["complete"]:
            get_client(s).add_comment(
                change.asana_gid,
                "[Visual QA Bridge intake] Please complete: " + ", ".join(check["missing"]),
            )
        s.commit()
        return record(change)


@router.post("/recipes/validate", tags=["Recipes"])
def validate(body: dict[str, Any]):
    validate_recipe(body.get("steps", []), body.get("allow_cart_interaction", False))
    return {"valid": True}

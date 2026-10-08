import difflib
import os
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session, record, all_rows, add
from backend.app.api.deps import current_role, require
from backend.app.services.events import event
from backend.app.services.metrics import metrics

router = APIRouter(prefix="/api", tags=["Settings & metrics"])


@router.get("/settings")
def settings():
    with session() as s:
        prompts = all_rows(s, m.Prompt)
        for p in prompts:
            previous = next(
                (x for x in prompts if x["name"] == p["name"] and x["version"] == p["version"] - 1),
                None,
            )
            p["diff"] = "\n".join(
                difflib.unified_diff(
                    (previous or {}).get("body", "").splitlines(),
                    p["body"].splitlines(),
                    fromfile="previous",
                    tofile="version " + str(p["version"]),
                    lineterm="",
                )
            )
        return {
            "rules": all_rows(s, m.Rule),
            "viewports": all_rows(s, m.Viewport),
            "prompts": prompts,
            "specs": all_rows(s, m.Spec),
            "users": all_rows(s, m.User),
            "asana_mode": os.getenv("ASANA_MODE", "mock"),
            "ai_provider": os.getenv("AI_PROVIDER", "mock"),
            "live_capture_enabled": os.getenv("LIVE_CAPTURE_ENABLED", "false") == "true",
            "allowed_hosts": os.getenv("CAPTURE_ALLOWED_HOSTS", ""),
            "contact": os.getenv("CAPTURE_CONTACT_EMAIL", "demo@example.invalid"),
        }


class RuleEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    description: str = Field(min_length=3)
    rule_type: str
    params_json: dict[str, Any]
    applies_to: str = "all"
    active: bool = True


class PromptEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3)
    body: str = Field(min_length=20)
    active: bool = True


class ViewportEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3)
    width: int = Field(ge=320, le=2560)
    height: int = Field(ge=320, le=2560)
    device_name: str | None = None
    is_mobile: bool = False


@router.get("/settings/rules")
def rules():
    with session() as s:
        return all_rows(s, m.Rule)


@router.post("/settings/rules")
def edit_rule(body: RuleEdit, role: str = Depends(current_role)):
    require(role, "QA lead")
    with session() as s:
        old = s.scalar(select(m.Rule).filter_by(code=body.code).order_by(m.Rule.version.desc()))
        if not old:
            raise HTTPException(
                422,
                "Use an existing approved rule template; arbitrary executable checks cannot be added.",
            )
        old.active = False
        new = add(s, m.Rule, **body.model_dump(), version=old.version + 1)
        event(s, 0, "rule", new.id, str(old.version), str(new.version), role, body.model_dump())
        s.commit()
        return record(new)


@router.get("/settings/prompts")
def prompts():
    return settings()["prompts"]


@router.post("/settings/prompts")
def edit_prompt(body: PromptEdit, role: str = Depends(current_role)):
    require(role, "QA lead")
    with session() as s:
        old = s.scalar(select(m.Prompt).filter_by(name=body.name).order_by(m.Prompt.version.desc()))
        if old:
            old.active = False
        new = add(s, m.Prompt, **body.model_dump(), version=old.version + 1 if old else 1)
        event(s, 0, "prompt", new.id, str(old.version) if old else "", str(new.version), role)
        s.commit()
        return record(new)


@router.get("/settings/viewports")
def viewports():
    with session() as s:
        return all_rows(s, m.Viewport)


@router.post("/settings/viewports")
def edit_viewport(body: ViewportEdit, role: str = Depends(current_role)):
    require(role, "QA lead")
    if body.device_name not in (None, "iPhone 13", "Pixel 7"):
        raise HTTPException(422, "This prototype supports iPhone 13 and Pixel 7 emulation.")
    with session() as s:
        if s.scalar(select(m.Viewport).filter_by(name=body.name)):
            raise HTTPException(
                409, "Use a new name to preserve existing viewport profiles and evidence."
            )
        new = add(s, m.Viewport, **body.model_dump())
        event(s, 0, "viewport", new.id, "", "Created", role)
        s.commit()
        return record(new)


@router.get("/metrics/summary")
def summary():
    with session() as s:
        return metrics(s)


@router.get("/metrics/change-requests/{id}")
def change_metrics(id: int):
    with session() as s:
        return metrics(s, id)

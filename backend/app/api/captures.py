from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session, record, all_rows
from backend.app.api.deps import require, current_role
from backend.app.api.changes import needed
from backend.app.services.capture.jobs import enqueue

router = APIRouter(prefix="/api", tags=["Capture & evidence"])


class CaptureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    change_request_id: int
    build_label: Literal["v1", "v2"] = "v1"
    viewport_profile_ids: list[int] = []


@router.post("/capture-runs")
def start(body: CaptureRequest, role: str = Depends(current_role)):
    require(role, "QA reviewer", "QA lead")
    with session() as s:
        change = needed(s, body.change_request_id)
        if change.status not in ("Ready for QA", "Intake incomplete"):
            raise HTTPException(
                409,
                "Initial capture requires Ready for QA. Use the affected-only recheck action after fixes.",
            )
        run = enqueue(s, change, body.build_label, body.viewport_profile_ids, role)
        s.commit()
        return record(run)


@router.get("/capture-runs/{id}")
def detail(id: int):
    with session() as s:
        run = s.get(m.Run, id)
        if not run:
            raise HTTPException(404, "Capture not found.")
        return {
            **record(run),
            "screenshots": all_rows(s, m.Screenshot, capture_run_id=id),
            "diffs": all_rows(s, m.Diff, capture_run_id=id),
            "ai_reviews": all_rows(s, m.AIReview, capture_run_id=id),
            "rule_results": all_rows(s, m.RuleResult, capture_run_id=id),
        }


@router.get("/screenshots/{id}/file")
def screenshot(id: int):
    with session() as s:
        shot = s.get(m.Screenshot, id)
        if not shot:
            raise HTTPException(404, "Screenshot not found.")
        return FileResponse(shot.file_path, media_type="image/png")


@router.get("/screenshots/{id}/measurements")
def measurements(id: int):
    with session() as s:
        return all_rows(s, m.Measurement, screenshot_id=id)


@router.post("/recipes/{id}/test-run", tags=["Recipes"])
def test_recipe(id: int, role: str = Depends(current_role)):
    require(role, "QA reviewer", "QA lead", "Designer")
    with session() as s:
        recipe = s.get(m.Recipe, id)
        if not recipe:
            raise HTTPException(404, "Recipe not found.")
        change = needed(s, recipe.change_request_id)
        if change.status not in ("Ready for QA", "Intake incomplete"):
            raise HTTPException(409, "Recipe testing is available before the initial capture.")
        run = enqueue(s, change, "v1", change.viewport_ids[:1], role, recipe_id=id)
        s.commit()
        return record(run)


@router.post("/screenshots/{id}/approve-baseline")
def approve_baseline(id: int, role: str = Depends(current_role)):
    require(role, "Designer", "QA lead")
    from backend.app.db import add
    from backend.app.services.events import event

    with session() as s:
        shot = s.get(m.Screenshot, id)
        if not shot or shot.kind != "full_page":
            raise HTTPException(
                422, "Approve a full-page screenshot after inspecting its evidence."
            )
        run = s.get(m.Run, shot.capture_run_id)
        if run is None or run.status != "Completed":
            raise HTTPException(409, "Wait for the capture to finish.")
        measurement = s.scalar(
            select(m.Measurement).where(
                m.Measurement.screenshot_id.in_(
                    select(m.Screenshot.id).where(
                        m.Screenshot.capture_run_id == run.id,
                        m.Screenshot.recipe_id == shot.recipe_id,
                        m.Screenshot.viewport_profile_id == shot.viewport_profile_id,
                    )
                )
            )
        )
        if not measurement:
            raise HTTPException(409, "No browser context measurement found.")
        baseline = add(
            s,
            m.Baseline,
            change_request_id=run.change_request_id,
            recipe_id=shot.recipe_id,
            viewport_profile_id=shot.viewport_profile_id,
            screenshot_id=shot.id,
            browser=measurement.computed_json.get("browser", ""),
            build=run.build_label,
            approved_by=role,
        )
        event(
            s,
            run.change_request_id,
            "baseline",
            baseline.id,
            "Unapproved",
            "Approved",
            role,
            {"screenshot": id},
        )
        s.commit()
        return record(baseline)


@router.get("/diffs/{id}/file")
def diff_file(id: int):
    with session() as s:
        diff = s.get(m.Diff, id)
        if not diff:
            raise HTTPException(404, "Diff not found.")
        return FileResponse(diff.file_path, media_type="image/png")

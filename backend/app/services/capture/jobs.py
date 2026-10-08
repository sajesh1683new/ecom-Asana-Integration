from sqlalchemy import select
from backend.app import models as m
from backend.app.db import add, record
from backend.app.services.intake import intake
from backend.app.services.events import status


def enqueue(
    s,
    change,
    build: str,
    viewport_ids: list[int],
    role: str,
    recipe_id: int | None = None,
    affected: list | None = None,
):
    if s.scalar(select(m.Job).where(m.Job.status.in_(["Queued", "Running"]))):
        raise ValueError("A browser job is active. Wait for it to finish.")
    check = intake(s, change)
    if not check["complete"]:
        raise ValueError("Cannot capture: " + ", ".join(check["missing"]))
    ids = viewport_ids or change.viewport_ids
    profiles = [record(s.get(m.Viewport, id)) for id in ids if s.get(m.Viewport, id)]
    if len(profiles) != len(ids) or not profiles:
        raise ValueError("Select valid viewport profiles.")
    recipes = [
        record(r)
        for r in s.scalars(select(m.Recipe).filter_by(change_request_id=change.id))
        if recipe_id is None or r.id == recipe_id
    ]
    if not recipes:
        raise ValueError("Capture recipe not found.")
    payload = {
        "recipes": recipes,
        "viewports": profiles,
        "sections": [
            record(x) for x in s.scalars(select(m.Section).filter_by(change_request_id=change.id))
        ],
        "affected": affected,
        "test_recipe": recipe_id is not None,
        "specs": [record(x) for x in s.scalars(select(m.Spec))],
        "rules": [record(x) for x in s.scalars(select(m.Rule).filter_by(active=True))],
    }
    run = add(
        s,
        m.Run,
        change_request_id=change.id,
        build_label=build,
        triggered_by=role,
        scope_json=payload,
    )
    add(s, m.Job, type="capture", payload_json={"run_id": run.id}, status="Queued")
    status(s, change, "Capturing", role, {"run_id": run.id, "affected_only": affected is not None})
    return run

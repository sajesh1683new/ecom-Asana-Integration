from pathlib import Path
from sqlalchemy import select
from PIL import Image, ImageDraw
from pixelmatch.contrib.PIL import pixelmatch
from backend.app import models as m
from backend.app.db import add


def analyse_visual(s, run):
    rule = next(
        (r for r in run.scope_json["rules"] if r["code"] == "VISUAL_DIFF_VS_BASELINE"), None
    )
    if not rule:
        return
    threshold = rule["params_json"].get("threshold_percent", 0.5)
    for shot in s.scalars(select(m.Screenshot).filter_by(capture_run_id=run.id, kind="full_page")):
        base = s.scalar(
            select(m.Baseline)
            .filter_by(
                change_request_id=run.change_request_id,
                recipe_id=shot.recipe_id,
                viewport_profile_id=shot.viewport_profile_id,
            )
            .order_by(m.Baseline.id.desc())
        )
        values = s.scalars(
            select(m.Measurement).where(
                m.Measurement.screenshot_id.in_(
                    select(m.Screenshot.id).where(
                        m.Screenshot.capture_run_id == run.id,
                        m.Screenshot.recipe_id == shot.recipe_id,
                        m.Screenshot.viewport_profile_id == shot.viewport_profile_id,
                    )
                )
            )
        ).all()
        browser = next(
            (v.computed_json.get("browser") for v in values if v.computed_json.get("browser")), ""
        )
        profile = next(
            v for v in run.scope_json["viewports"] if v["id"] == shot.viewport_profile_id
        )
        baseline_shot = s.get(m.Screenshot, base.screenshot_id) if base else None
        baseline_run = s.get(m.Run, baseline_shot.capture_run_id) if baseline_shot else None
        comparable = (
            base
            and baseline_run is not None
            and base.browser == browser
            and profile in baseline_run.scope_json["viewports"]
            and baseline_run.scope_json["specs"] == run.scope_json["specs"]
        )
        if not comparable or not baseline_shot or base is None or baseline_run is None:
            add(
                s,
                m.RuleResult,
                capture_run_id=run.id,
                recipe_id=shot.recipe_id,
                viewport_profile_id=shot.viewport_profile_id,
                selector="body",
                rule_code="VISUAL_DIFF_VS_BASELINE",
                status="Blocked",
                expected=threshold,
                observed={
                    "reason": "No approved matching recipe, viewport, browser and design-version baseline."
                },
                screenshot_id=shot.id,
            )
            continue
        a = Image.open(baseline_shot.file_path).convert("RGBA")
        b = Image.open(shot.file_path).convert("RGBA")
        if a.size != b.size:
            add(
                s,
                m.RuleResult,
                capture_run_id=run.id,
                recipe_id=shot.recipe_id,
                viewport_profile_id=shot.viewport_profile_id,
                selector="body",
                rule_code=rule["code"],
                status="Blocked",
                expected=threshold,
                observed={"reason": "Baseline/current image dimensions differ."},
                screenshot_id=shot.id,
            )
            continue
        diff = Image.new("RGBA", a.size)
        count = pixelmatch(a, b, diff, threshold=0.1, includeAA=False)
        percent = 100 * count / (a.width * a.height)
        masked_a = a.copy()
        masked_b = b.copy()
        draw_a = ImageDraw.Draw(masked_a)
        draw_b = ImageDraw.Draw(masked_b)
        scale = a.width / profile["width"]
        boxes = [
            v.computed_json["bounding_box"]
            for v in values
            if v.computed_json.get("element_index") == 0
            and v.computed_json.get("testid") != "popup-body"
        ]
        previous = s.scalars(
            select(m.Measurement).where(
                m.Measurement.screenshot_id.in_(
                    select(m.Screenshot.id).where(
                        m.Screenshot.capture_run_id == baseline_run.id,
                        m.Screenshot.recipe_id == shot.recipe_id,
                        m.Screenshot.viewport_profile_id == shot.viewport_profile_id,
                    )
                )
            )
        ).all()
        boxes.extend(
            v.computed_json["bounding_box"]
            for v in previous
            if v.computed_json.get("element_index") == 0
            and v.computed_json.get("testid") != "popup-body"
        )
        for box in boxes:
            coords = (
                int(box["x"] * scale),
                int(box["y"] * scale),
                int((box["x"] + box["width"]) * scale),
                int((box["y"] + box["height"]) * scale),
            )
            draw_a.rectangle(coords, fill="white")
            draw_b.rectangle(coords, fill="white")
        outside = (
            100
            * pixelmatch(masked_a, masked_b, threshold=0.1, includeAA=False)
            / (a.width * a.height)
        )
        path = Path(shot.file_path).with_name(Path(shot.file_path).stem + "_diff.png")
        diff.save(path)
        add(
            s,
            m.Diff,
            capture_run_id=run.id,
            screenshot_id=shot.id,
            baseline_id=base.id,
            file_path=str(path),
            changed_percent=percent,
            outside_percent=outside,
            threshold=threshold,
        )
        add(
            s,
            m.RuleResult,
            capture_run_id=run.id,
            recipe_id=shot.recipe_id,
            viewport_profile_id=shot.viewport_profile_id,
            selector="body",
            rule_code=rule["code"],
            status="Failed" if percent > threshold else "Passed",
            expected=threshold,
            observed={
                "changed_percent": percent,
                "outside_percent": outside,
                "pixelmatch_color_threshold": 0.1,
                "anti_aliasing": "excluded",
            },
            screenshot_id=shot.id,
        )
        if outside > threshold:
            add(
                s,
                m.Finding,
                change_request_id=run.change_request_id,
                capture_run_id=run.id,
                source="rule",
                source_ref=f"V{run.id}-{shot.id}",
                title="Unexpected change outside scope",
                description=f"{outside:.2f}% changed pixels outside the union of declared section boxes. Rendering differences need human review; this is not a confirmed defect.",
                element="body",
                viewport=profile["name"],
                recipe_id=shot.recipe_id,
                evidence_screenshot_id=shot.id,
                rule_code=rule["code"],
                kind="question",
            )

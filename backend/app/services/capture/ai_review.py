import base64
import json
import os
import time
from pathlib import Path
from typing import Literal, Protocol, Any
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import add, record


class VisionFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    element: str
    viewport: str
    observation: str
    rule_code: str | None
    confidence: float = Field(ge=0, le=1)
    category: Literal["defect", "change_request", "question", "cannot_judge", "out_of_scope"]


class VisionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[VisionFinding]


class VisionProvider(Protocol):
    name: str
    model: str

    def review(self, context: dict[str, Any], images: list[str], prompt: str) -> VisionOutput: ...


class MockVisionProvider:
    name = "MockVisionProvider"
    model = "deterministic mock — no model call"

    def review(self, context: dict[str, Any], images: list[str], prompt: str) -> VisionOutput:
        findings = [
            VisionFinding(
                element=f["element"],
                viewport=f["viewport"],
                observation="Possible issue: " + f["description"],
                rule_code=f["rule_code"],
                confidence=0.9,
                category="defect",
            )
            for f in context["rule_findings"]
        ]
        if context["popup"]:
            findings.extend(
                [
                    VisionFinding(
                        element="[data-testid=feature-list-premium]",
                        viewport=context["viewport"],
                        observation="The card background may be too warm. Seeded false positive for human review; no measured rule failure supports this claim.",
                        rule_code=None,
                        confidence=0.45,
                        category="cannot_judge",
                    ),
                    VisionFinding(
                        element="[data-testid=best-value]",
                        viewport=context["viewport"],
                        observation="Best value badge appears only on the higher tier. This may be an intentional design difference; ask the Designer.",
                        rule_code=None,
                        confidence=0.8,
                        category="question",
                    ),
                ]
            )
        return VisionOutput(findings=findings)


class OpenAIVisionProvider:
    name = "OpenAIVisionProvider"

    def __init__(self):
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is missing. Choose AI_PROVIDER=mock to run offline.")
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

    def review(self, context: dict[str, Any], images: list[str], prompt: str) -> VisionOutput:
        from openai import OpenAI

        content: Any = [
            {
                "type": "input_image",
                "image_url": "data:image/png;base64,"
                + base64.b64encode(Path(path).read_bytes()).decode(),
            }
            for path in images
        ]
        content.append({"type": "input_text", "text": json.dumps(context)})
        response = OpenAI(timeout=60, max_retries=1).responses.parse(
            model=self.model,
            input=[{"role": "system", "content": prompt}, {"role": "user", "content": content}],
            text_format=VisionOutput,
        )
        if response.output_parsed is None:
            raise ValueError("OpenAI response was refused or did not match the schema.")
        return response.output_parsed


class AnthropicVisionProvider:
    name = "AnthropicVisionProvider"

    def __init__(self):
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise ValueError(
                "ANTHROPIC_API_KEY is missing. Choose AI_PROVIDER=mock to run offline."
            )
        self.model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

    def review(self, context: dict[str, Any], images: list[str], prompt: str) -> VisionOutput:
        from anthropic import Anthropic

        content: Any = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(Path(path).read_bytes()).decode(),
                },
            }
            for path in images
        ]
        content.append(
            {
                "type": "text",
                "text": json.dumps(context)
                + "\nReturn only JSON for this schema: "
                + json.dumps(VisionOutput.model_json_schema()),
            }
        )
        response = Anthropic(timeout=60, max_retries=1).messages.create(
            model=self.model,
            max_tokens=4000,
            system=prompt,
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(block.text for block in response.content if block.type == "text")
        return VisionOutput.model_validate_json(raw)


def provider() -> VisionProvider:
    mode = os.getenv("AI_PROVIDER", "mock")
    if mode == "openai":
        return OpenAIVisionProvider()
    if mode == "anthropic":
        return AnthropicVisionProvider()
    if mode != "mock":
        raise ValueError("AI_PROVIDER must be mock, openai or anthropic.")
    return MockVisionProvider()


def analyse_ai(s, run):
    engine = provider()
    prompt = s.scalar(
        select(m.Prompt)
        .filter_by(name="Vision review", active=True)
        .order_by(m.Prompt.version.desc())
    )
    if not prompt:
        raise ValueError("An active versioned vision prompt is required.")
    shots = s.scalars(select(m.Screenshot).filter_by(capture_run_id=run.id)).all()
    for recipe in run.scope_json["recipes"]:
        for profile in run.scope_json["viewports"]:
            matching = [
                x
                for x in shots
                if x.recipe_id == recipe["id"] and x.viewport_profile_id == profile["id"]
            ]
            if not matching:
                continue
            screenshot = next((x for x in matching if x.kind == "popup"), matching[0])
            rules = [
                record(x)
                for x in s.scalars(
                    select(m.Finding).filter_by(
                        capture_run_id=run.id,
                        source="rule",
                        recipe_id=recipe["id"],
                        viewport=profile["name"],
                    )
                )
            ]
            measurements = [
                record(x)
                for x in s.scalars(
                    select(m.Measurement).where(
                        m.Measurement.screenshot_id.in_([x.id for x in matching])
                    )
                )
            ]
            context = {
                "rule_findings": rules,
                "measurements": measurements,
                "checklist": run.scope_json["rules"],
                "viewport": profile["name"],
                "popup": screenshot.kind == "popup",
                "synthetic": True,
            }
            spec = next(
                (
                    x
                    for x in run.scope_json["specs"]
                    if ("Premium to" in x["name"]) == ("premium" in recipe["name"].lower())
                ),
                run.scope_json["specs"][0],
            )
            started = time.perf_counter()
            output = engine.review(
                context, [screenshot.file_path, spec["frame_image_path"]], prompt.body
            )
            # Validate even provider-owned objects before persisting or displaying them.
            output = VisionOutput.model_validate(output.model_dump())
            for item in output.findings:
                if item.viewport != profile["name"]:
                    raise ValueError(
                        "AI response invented a viewport outside its supplied evidence."
                    )
                f = add(
                    s,
                    m.Finding,
                    change_request_id=run.change_request_id,
                    capture_run_id=run.id,
                    source="ai",
                    source_ref="",
                    title=item.observation[:110],
                    description=item.observation,
                    element=item.element,
                    viewport=item.viewport,
                    recipe_id=recipe["id"],
                    evidence_screenshot_id=screenshot.id,
                    rule_code=item.rule_code,
                    ai_confidence=item.confidence,
                    kind="question"
                    if item.category in ("cannot_judge", "question", "out_of_scope")
                    else item.category,
                    created_by=engine.name,
                    status="Needs evidence"
                    if item.confidence < 0.6 or item.category == "cannot_judge"
                    else "Unconfirmed",
                )
                f.source_ref = f"AI-{f.id}"
                from backend.app.services.events import event

                event(
                    s,
                    run.change_request_id,
                    "finding_created",
                    f.id,
                    "",
                    "Unconfirmed",
                    engine.name,
                    {
                        "source": "ai",
                        "run_id": run.id,
                        "element": item.element,
                        "viewport": item.viewport,
                    },
                )
            add(
                s,
                m.AIReview,
                capture_run_id=run.id,
                provider=engine.name,
                model=engine.model,
                prompt_version=prompt.version,
                response=output.model_dump()["findings"],
                duration_ms=(time.perf_counter() - started) * 1000,
            )

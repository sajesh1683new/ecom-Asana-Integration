"""Browser collection only. Analysis is applied by the worker after capture."""

import hashlib
import re
from typing import Any
from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse
from playwright.sync_api import sync_playwright
from backend.app.config import DATA, ORIGIN
from backend.app.db import add
from backend.app import models as m
from backend.app.services.capture.measure import measure
from backend.app.services.capture.safety import (
    validate_recipe,
    validate_url,
    before_navigation,
    user_agent,
)


def safe_name(value: str) -> str:
    return re.sub("[^a-zA-Z0-9_-]", "-", value)[:80]


def build_url(value: str, build: str) -> str:
    parsed = urlparse(validate_url(value))
    # Build variants belong only to the fictional local site, never rewrite live URLs.
    if parsed.netloc == urlparse(ORIGIN).netloc:
        query = dict(parse_qsl(parsed.query))
        query["build"] = build
        return urlunparse(parsed._replace(query=urlencode(query)))
    return parsed.geturl()


def capture(s, run, payload: dict[str, Any]) -> dict[str, Any]:
    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for recipe in payload["recipes"]:
                validate_recipe(recipe["steps_json"], recipe["allow_cart_interaction"])
                for profile in payload["viewports"]:
                    scope = payload.get("affected")
                    scoped = [
                        x
                        for x in scope or []
                        if x["recipe_id"] == recipe["id"] and x["viewport"] == profile["name"]
                    ]
                    if scope is not None and not scoped:
                        continue
                    selectors = {x["selector"] for x in scoped} if scope is not None else None
                    sections = [
                        x
                        for x in payload["sections"]
                        if selectors is None or x["selector"] in selectors
                    ]
                    if selectors:
                        known = {x["selector"] for x in sections}
                        sections.extend(
                            {
                                "name": x["selector"],
                                "selector": x["selector"],
                                "design_spec_id": payload["sections"][0]["design_spec_id"],
                            }
                            for x in scoped
                            if x["selector"] not in known
                        )
                    options: dict[str, Any] = {
                        "viewport": {"width": profile["width"], "height": profile["height"]},
                        "device_scale_factor": 2,
                    }
                    if profile["device_name"]:
                        options = dict(p.devices[profile["device_name"]])
                    options.update(
                        locale="en-US",
                        timezone_id="UTC",
                        color_scheme="light",
                        reduced_motion="reduce",
                        user_agent=user_agent(),
                    )
                    context = browser.new_context(**options)
                    failures = []

                    def guard(route):
                        request = route.request
                        try:
                            validate_url(request.url)
                            if request.method not in ("GET", "HEAD"):
                                path = urlparse(request.url).path
                                if not recipe["allow_cart_interaction"] or path not in (
                                    "/cart/add",
                                    "/cart/add.js",
                                ):
                                    raise ValueError(
                                        "Live website writes are prohibited except the explicitly allowed add-to-cart endpoint."
                                    )
                            if (
                                request.is_navigation_request()
                                and request.resource_type == "document"
                            ):
                                before_navigation(request.url)
                            route.continue_()
                        except ValueError as e:
                            if request.is_navigation_request():
                                failures.append(str(e))
                            route.abort()

                    context.route("**/*", guard)
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    try:
                        for step in recipe["steps_json"]:
                            action = step["action"]
                            selector = step.get("selector")
                            if action == "goto":
                                page.goto(
                                    build_url(step["url"], run.build_label),
                                    wait_until="networkidle",
                                )
                            elif action == "click":
                                page.locator(selector).click()
                            elif action == "wait_for":
                                page.locator(selector).wait_for(state="visible")
                            elif action == "hover":
                                page.locator(selector).hover()
                            elif action == "scroll":
                                page.locator(selector).evaluate(
                                    "(e,y)=>e.scrollTop=y", max(0, min(int(step.get("y", 0)), 5000))
                                )
                            elif action == "set_viewport":
                                # Named evidence profiles remain authoritative for comparable screenshots.
                                if (
                                    step["width"] != profile["width"]
                                    or step["height"] != profile["height"]
                                ):
                                    raise ValueError(
                                        "set_viewport must match the selected named profile; create a new profile for another size."
                                    )
                            elif action == "measure":
                                measure(page, selector)
                            elif action == "screenshot":
                                page.locator(step.get("target", "body")).wait_for(state="visible")
                        if failures:
                            raise ValueError(failures[0])
                        page.evaluate("document.fonts.ready")
                        page.evaluate(
                            "Promise.all(Array.from(document.images).map(i=>i.complete?Promise.resolve():new Promise(resolve=>{i.onload=resolve;i.onerror=resolve})))"
                        )
                        page.add_style_tag(
                            content="*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}"
                        )
                        root = (
                            DATA
                            / "screenshots"
                            / str(run.change_request_id)
                            / run.build_label
                            / str(run.id)
                            / safe_name(profile["name"])
                        )
                        root.mkdir(parents=True, exist_ok=True)

                        def save(kind: str, selector: str, name: str):
                            path = root / (
                                safe_name(recipe["name"])
                                + "_"
                                + safe_name(name)
                                + "_"
                                + kind
                                + ".png"
                            )
                            if kind == "full_page":
                                page.screenshot(
                                    path=str(path), full_page=True, animations="disabled"
                                )
                            else:
                                page.locator(selector).screenshot(
                                    path=str(path), animations="disabled"
                                )
                            from PIL import Image

                            with Image.open(path) as image:
                                width, height = image.size
                            shot = add(
                                s,
                                m.Screenshot,
                                capture_run_id=run.id,
                                viewport_profile_id=profile["id"],
                                recipe_id=recipe["id"],
                                kind=kind,
                                selector=selector,
                                file_path=str(path),
                                width=width,
                                height=height,
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                            )
                            captured.append(shot.id)
                            return shot

                        full = save("full_page", "body", "page")
                        if page.locator("[data-testid=upgrade-popup]").is_visible():
                            save("popup", "[data-testid=upgrade-popup]", "popup")
                        measurements = []
                        for section in sections:
                            selector = section["selector"]
                            values = measure(page, selector)
                            # Hidden elements have evidence in the full page and a measurement, not a fabricated crop.
                            shot = (
                                save("element", selector, section["name"])
                                if page.locator(selector).is_visible()
                                else full
                            )
                            for value in values:
                                value.update(
                                    parent_selector=selector,
                                    profile=profile["name"],
                                    recipe_id=recipe["id"],
                                    browser=browser.version,
                                    device_scale_factor=options["device_scale_factor"],
                                )
                                actual_selector = (
                                    f"[data-testid={value['testid']}]"
                                    if value["testid"]
                                    else selector
                                )
                                add(
                                    s,
                                    m.Measurement,
                                    screenshot_id=shot.id,
                                    selector=actual_selector,
                                    computed_json=value,
                                )
                                measurements.append(value)
                        # Overflow belongs to the popup body even when only the divider is in scope.
                        body = page.locator("[data-testid=popup-body]")
                        if body.count():
                            for value in measure(page, "[data-testid=popup-body]")[:1]:
                                value.update(
                                    parent_selector="[data-testid=popup-body]",
                                    profile=profile["name"],
                                    recipe_id=recipe["id"],
                                    browser=browser.version,
                                )
                                add(
                                    s,
                                    m.Measurement,
                                    screenshot_id=full.id,
                                    selector="[data-testid=popup-body]",
                                    computed_json=value,
                                )
                        s.flush()
                    finally:
                        context.close()
        finally:
            browser.close()
    return {"screenshots": captured}

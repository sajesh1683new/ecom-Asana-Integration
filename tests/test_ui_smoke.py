"""Browser smoke of the actual React app connected to the isolated API."""

import os
import shutil
import subprocess
import time
import httpx
from playwright.sync_api import sync_playwright, expect


def test_frontend_bridge_smoke(client, storefront):
    env = {**os.environ, "VQA_API_ORIGIN": "http://127.0.0.1:8001"}
    process = subprocess.Popen(
        [
            shutil.which("node") or "node",
            "node_modules/vite/bin/vite.js",
            "--host",
            "127.0.0.1",
            "--port",
            "5174",
            "--strictPort",
        ],
        cwd="frontend",
        env=env,
    )
    try:
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:5174").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto("http://127.0.0.1:5174")
            expect(
                page.get_by_role("heading", name="See the change. Close the loop.")
            ).to_be_visible()
            page.get_by_label("Current role").select_option("QA lead")
            page.get_by_role("link", name="Mock Asana", exact=True).click()
            page.get_by_role("button").filter(has_text="CHG-101a").click()
            expect(page.get_by_role("heading", name="Subtasks", exact=True)).to_be_visible()
            expect(
                page.locator(".modal .comment p").filter(has_text="Developer").first
            ).to_be_visible()
            page.get_by_role("button", name="Close", exact=True).click()
            page.get_by_role("link", name="Metrics", exact=True).click()
            expect(
                page.get_by_role(
                    "heading", name="Evidence of effort, not a savings claim.", exact=True
                )
            ).to_be_visible()
            page.screenshot(
                path=os.path.join(os.environ["DATA_DIR"], "ui-smoke.png"), full_page=True
            )
            assert not errors, errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)

from collections import Counter
from pathlib import Path
import pytest
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session
from backend.app.worker import run_once
from backend.app.services.capture.safety import validate_url, validate_recipe

LEAD = {"X-Role": "QA lead"}
REASON = "Inspected recorded measurements against the synthetic approved requirement."


def post(client, path, body=None, role="QA lead"):
    response = client.post("/api" + path, json=body or {}, headers={"X-Role": role})
    assert response.status_code == 200, response.text
    return response.json()


def test_01_import_intake_fixture_and_idempotency(client):
    post(client, "/asana/pull")
    first = client.get("/api/asana/mock/board").json()
    post(client, "/asana/pull")
    second = client.get("/api/asana/mock/board").json()
    assert len(first["tasks"]) == len(second["tasks"])
    assert len(first["stories"]) == len(second["stories"])
    changes = client.get("/api/change-requests").json()
    incomplete = next(c for c in changes if c["code"] == "CHG-101")
    assert incomplete["status"] == "Intake incomplete"
    assert any(
        "changed sections" in s["text"].lower()
        for s in first["stories"]
        if s["task_gid"] == incomplete["asana_gid"]
    )
    change = next(c for c in changes if c["code"] == "CHG-101a")
    queue = client.get(f"/api/queue/{change['id']}").json()
    expected = [
        "Needs evidence",
        "Needs clarification",
        "Change request: needs approval",
        "Blocked",
        "Needs clarification",
        "Needs evidence",
    ]
    assert [q["status"] for q in queue["items"]] == expected
    assert [q["source_ids"] for q in queue["items"]] == [[f"F{i}"] for i in range(1, 7)]
    assert queue["groups"][0]["state"] == "possible"
    assert (
        client.post(
            "/api/capture-runs", json={"change_request_id": incomplete["id"]}, headers=LEAD
        ).status_code
        == 422
    )


def test_02_safety(monkeypatch):
    with pytest.raises(ValueError, match="disabled"):
        validate_url("https://example.com/product")
    monkeypatch.setenv("LIVE_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("CAPTURE_ALLOWED_HOSTS", "example.com")
    assert validate_url("https://example.com/product").endswith("/product")
    with pytest.raises(ValueError, match="not in"):
        validate_url("https://unlisted.invalid/")
    with pytest.raises(ValueError, match="prohibited"):
        validate_url("https://example.com/checkout")
    with pytest.raises(ValueError):
        validate_recipe([{"action": "evaluate", "code": "alert(1)"}])


def test_03_browser_full_happy_path(client, storefront):
    change = next(c for c in client.get("/api/change-requests").json() if c["code"] == "CHG-101a")
    cid = change["id"]
    original = {
        f["source_ref"]: f["description"]
        for f in client.get(f"/api/findings?change_request_id={cid}").json()
    }
    run = post(client, "/capture-runs", {"change_request_id": cid, "build_label": "v1"})
    assert run_once()
    evidence = client.get(f"/api/capture-runs/{run['id']}").json()
    assert evidence["status"] == "Completed", evidence["error"]
    popups = [s for s in evidence["screenshots"] if s["kind"] == "popup"]
    assert len(popups) == 6
    assert all(Path(s["file_path"]).is_file() for s in popups)
    failed = {r["rule_code"]: r for r in evidence["rule_results"] if r["status"] == "Failed"}
    assert {
        "MIN_FONT_MOBILE",
        "SIDE_PADDING_MATCHES_SPEC",
        "COPY_MATCHES_APPROVED",
        "CONDITIONAL_VISIBILITY",
        "ELEMENT_ORDER_MATCHES_SPEC",
    } <= failed.keys()
    assert failed["MIN_FONT_MOBILE"]["observed"] == 14
    assert failed["SIDE_PADDING_MATCHES_SPEC"]["observed"]["left"] == 2
    assert failed["COPY_MATCHES_APPROVED"]["observed"] == "HRS"
    assert failed["CONDITIONAL_VISIBILITY"]["expected"] is False
    assert failed["CONDITIONAL_VISIBILITY"]["observed"]["visible"] is True
    assert failed["ELEMENT_ORDER_MATCHES_SPEC"]["observed"][2:] == ["delivery", "cooling"]
    assert all(
        r["status"] == "Blocked"
        for r in evidence["rule_results"]
        if r["rule_code"] == "VISUAL_DIFF_VS_BASELINE"
    )
    post(client, f"/change-requests/{cid}/transition", {"status": "In human review"})
    assert client.post(f"/api/queue/{cid}/consolidate", headers=LEAD).status_code == 422
    findings = client.get(f"/api/findings?change_request_id={cid}").json()
    f4 = next(f for f in findings if f["source_ref"] == "F4")
    assert (
        client.post(
            f"/api/findings/{f4['id']}/triage",
            json={"outcome": "valid", "kind": "defect", "reason": REASON},
        ).status_code
        == 409
    )
    recipe = client.get(f"/api/change-requests/{cid}").json()["recipes"][0]["id"]
    post(
        client,
        f"/findings/{f4['id']}/clarify",
        {
            "element": "[data-testid=feature-list-premium]",
            "viewport": "mobile-390",
            "recipe_id": recipe,
            "reason": "Synthetic demo clarification by the reviewer.",
        },
    )
    # A human-authored fixture explicitly confirms one measured condition and rejects other claims.
    for f in findings:
        outcome = "valid" if f["source_ref"] == "F4" else "by design"
        post(
            client,
            f"/findings/{f['id']}/triage",
            {"outcome": outcome, "kind": "defect", "reason": REASON},
        )
    post(client, f"/queue/{cid}/consolidate")
    queue = client.get(f"/api/queue/{cid}").json()
    post(
        client,
        f"/queue/groups/{queue['groups'][0]['id']}/reject",
        {"reason": "Header and headline are separate observations; no merge."},
    )
    post(client, f"/asana/push/{cid}")
    board = client.get("/api/asana/mock/board").json()
    children = [t for t in board["tasks"] if t["parent_gid"] == change["asana_gid"]]
    assert len(children) == 1
    counts = {k: len(board[k]) for k in ("tasks", "stories", "attachments")}
    post(client, f"/asana/push/{cid}")
    assert counts == {k: len(client.get("/api/asana/mock/board").json()[k]) for k in counts}
    assert (
        client.post(
            f"/api/release/{cid}/approve",
            json={"reason": REASON},
            headers={"X-Role": "Release owner"},
        ).status_code
        == 409
    )
    fixed = next(q for q in queue["items"] if q["source_ids"] == ["F4"])
    assert (
        client.post(
            f"/api/queue/items/{fixed['id']}/fixed", json={"reason": REASON}, headers=LEAD
        ).status_code
        == 403
    )
    post(
        client,
        f"/queue/items/{fixed['id']}/fixed",
        {"reason": "Applied synthetic v2 correction."},
        "Developer",
    )
    recheck = post(client, f"/change-requests/{cid}/recheck")
    assert len(recheck["scope_json"]["affected"]) == 1
    assert recheck["scope_json"]["affected"][0]["selector"] == "[data-testid=feature-list-premium]"
    assert len(recheck["scope_json"]["viewports"]) == 1
    assert run_once()
    rerun = client.get(f"/api/capture-runs/{recheck['id']}").json()
    assert rerun["status"] == "Completed", rerun["error"]
    assert all(
        r["status"] == "Passed"
        for r in rerun["rule_results"]
        if r["rule_code"] == "MIN_FONT_MOBILE"
    )
    rows = client.get(f"/api/queue/{cid}").json()["items"]
    assert all(q["status"] == "Closed" for q in rows), Counter(q["status"] for q in rows)
    post(client, f"/asana/push/{cid}")
    post(
        client,
        f"/release/{cid}/approve",
        {"reason": "Human demo release review completed."},
        "Release owner",
    )
    after = client.get(f"/api/findings?change_request_id={cid}").json()
    assert original == {
        f["source_ref"]: f["description"] for f in after if f["source"] == "imported_comment"
    }
    with session() as s:
        assert not list(s.scalars(select(m.Baseline)))
    shot = next(s for s in rerun["screenshots"] if s["kind"] == "full_page")
    assert client.post(f"/api/screenshots/{shot['id']}/approve-baseline").status_code == 403
    post(client, f"/screenshots/{shot['id']}/approve-baseline")
    metrics = client.get("/api/metrics/summary").json()
    assert metrics["ai_noise_rate"] > 0
    assert metrics["escaped_defects"] == 1
    assert metrics["pilot"]


def test_04_clean_capture(client, storefront):
    change = next(c for c in client.get("/api/change-requests").json() if c["code"] == "CHG-102")
    run = post(client, "/capture-runs", {"change_request_id": change["id"]})
    run_once()
    result = client.get(f"/api/capture-runs/{run['id']}").json()
    assert result["status"] == "Completed", result["error"]
    assert not [r for r in result["rule_results"] if r["status"] == "Failed"]
    assert not client.get(f"/api/findings?change_request_id={change['id']}").json()


def test_05_all_fixes_and_explicit_visual_baseline(client, storefront):
    from backend.app.db import add, record
    from backend.app.services.capture.runner import capture
    from backend.app.services.capture.rules import analyse
    from backend.app.services.capture.visual_diff import analyse_visual

    with session() as s:
        initial = s.scalar(select(m.Run).where(m.Run.build_label == "v1").order_by(m.Run.id))
        fixed = add(
            s,
            m.Run,
            change_request_id=initial.change_request_id,
            build_label="v2",
            scope_json=initial.scope_json,
            triggered_by="QA reviewer",
        )
        capture(s, fixed, fixed.scope_json)
        results = analyse(s, fixed)
        assert results and all(r.status == "Passed" for r in results)
        fixed.status = "Completed"
        fixed_id, initial_id = fixed.id, initial.id
        s.commit()
    shots = client.get(f"/api/capture-runs/{fixed_id}").json()["screenshots"]
    for shot in shots:
        if shot["kind"] == "full_page":
            post(client, f"/screenshots/{shot['id']}/approve-baseline")
    with session() as s:
        candidate = s.get(m.Run, initial_id)
        analyse_visual(s, candidate)
        differences = list(s.scalars(select(m.Diff).filter_by(capture_run_id=initial_id)))
        assert len(differences) == 6
        assert all(Path(d.file_path).is_file() for d in differences)
        assert any(record(d)["changed_percent"] > 0 for d in differences)
        s.rollback()  # Comparison is a test; do not alter the reviewed change.


def test_06_missing_credentials_and_ai_authority(monkeypatch, client):
    from backend.app.integrations.asana.real_client import RealAsanaClient
    from backend.app.services.capture.ai_review import provider, VisionOutput
    from pydantic import ValidationError

    monkeypatch.setenv("ASANA_MODE", "real")
    monkeypatch.delenv("ASANA_PAT", raising=False)
    with pytest.raises(ValueError, match="missing"):
        RealAsanaClient()
    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="missing"):
        provider()
    with pytest.raises(ValidationError):
        VisionOutput.model_validate({"findings": [], "release_approved": True})
    assert client.get("/api/health").status_code == 200

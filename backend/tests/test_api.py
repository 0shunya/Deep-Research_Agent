"""HTTP API through the real scheduler thread, with the scripted model and the simulated web."""

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(make_env):
    from app.api.ratelimit import run_limiter
    from app.main import create_app
    from app.research.scheduler import RunScheduler

    run_limiter.reset()
    scheduler = RunScheduler(env_factory=make_env)
    with TestClient(create_app(scheduler=scheduler)) as test_client:
        yield test_client


def wait_for(client, run_id, statuses, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.1)
    raise AssertionError(f"run stuck in {run['status']}")


def test_health(client):
    body = client.get("/api/health").json()
    assert body["database"] == "ok" and body["status"] == "ok"
    assert body["search_provider"] == "wikipedia"  # no TAVILY_API_KEY in tests


def test_full_flow_plan_approve_report_and_claim_audit(client):
    created = client.post("/api/runs", json={"question": "When was the Eiffel Tower completed?", "depth": "quick"})
    assert created.status_code == 202
    run_id = created.json()["id"]
    run = wait_for(client, run_id, {"awaiting_approval", "failed"})
    assert run["status"] == "awaiting_approval"
    plan = client.get(f"/api/runs/{run_id}/plan").json()["plan"]
    plan["sub_questions"][0]["ask"] = "In which year was the Eiffel Tower completed?"  # user edits the plan
    approved = client.post(f"/api/runs/{run_id}/plan/approve", json={"plan": plan})
    assert approved.status_code == 200, approved.text
    run = wait_for(client, run_id, {"completed", "failed"})
    assert run["status"] == "completed", run["error_message"]
    assert run["tasks"][0]["question"] == "In which year was the Eiffel Tower completed?"
    assert run["counts"]["supported"] >= 1 and run["counts"]["domains"] == 2  # quick depth reads 2 sources per task

    report = client.get(f"/api/runs/{run_id}/report").json()
    assert report["stats"]["sentences_dropped_uncited"] == 1
    cited = [cid for s in report["sections"] for sent in s["sentences"] for cid in sent["claim_ids"]]
    assert cited and all(cid in report["claims"] for cid in cited)

    claim = client.get(f"/api/claims/{cited[0]}").json()
    assert claim["evidence"] and claim["evidence"][0]["source"]["content_hash"]
    found = [e for e in claim["evidence"] if e["quote_found"]]
    assert found and found[0]["context"]

    events = client.get(f"/api/runs/{run_id}/events")
    assert events.status_code == 200
    assert "task_started" in events.text and "report_ready" in events.text and "event: end" in events.text

    metrics = client.get("/api/metrics").json()
    assert metrics["completed_runs"] == 1
    assert 0 <= metrics["cited_supported_share"] <= 1
    # Scripted (offline) model calls never mix into the measured cost dashboard.
    assert metrics["model_calls"] == 0 and metrics["by_feature"] == {}


def test_idempotency_key_returns_same_run(client):
    body = {"question": "When was the Eiffel Tower completed?", "depth": "quick"}
    first = client.post("/api/runs", json=body, headers={"Idempotency-Key": "abc-123"}).json()
    second = client.post("/api/runs", json=body, headers={"Idempotency-Key": "abc-123"}).json()
    assert first["id"] == second["id"]
    assert len(client.get("/api/runs").json()) == 1


def test_invalid_plan_edit_is_rejected(client):
    run_id = client.post("/api/runs", json={"question": "When was the Eiffel Tower completed?"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    plan = client.get(f"/api/runs/{run_id}/plan").json()["plan"]
    plan["sub_questions"][0]["depends_on"] = ["q9"]
    response = client.post(f"/api/runs/{run_id}/plan/approve", json={"plan": plan})
    assert response.status_code == 422 and response.json()["error"]["code"] == "invalid_plan"


def test_cancel_before_approval(client):
    run_id = client.post("/api/runs", json={"question": "When was the Eiffel Tower completed?"}).json()["id"]
    wait_for(client, run_id, {"awaiting_approval"})
    assert client.post(f"/api/runs/{run_id}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 409
    assert client.post(f"/api/runs/{run_id}/plan/approve", json={}).status_code == 409


def test_validation_and_not_found(client):
    assert client.post("/api/runs", json={"question": "short"}).status_code == 422
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/claims/00000000-0000-0000-0000-000000000000").json()["error"]["code"] == "claim_not_found"


def test_access_code_guards_api_but_not_health(make_env, settings_override):
    from app.main import create_app
    from app.research.scheduler import RunScheduler

    settings_override(demo_access_code="letmein")
    with TestClient(create_app(scheduler=RunScheduler(env_factory=make_env))) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/runs").status_code == 401
        assert c.get("/api/runs", headers={"X-Access-Code": "letmein"}).status_code == 200
        assert c.get("/api/runs?access_code=letmein").status_code == 200

"""Run with: pytest -q (uses isolated temporary database)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from observability import database
from observability.generator import generate_batch
from observability.quality import baseline_from_runs, evaluate_quality
from observability.rca import analyze_root_cause
from observability.service import get_incident, list_incidents, run_pipeline, seed_demo, summary, update_incident


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(database, "engine", test_engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=test_engine, expire_on_commit=False))
    monkeypatch.setattr("observability.service.DATA_DIR", tmp_path)
    database.init_db()
    yield
    test_engine.dispose()


@pytest.mark.parametrize("scenario,expected_failed", [
    ("normal", None),
    ("late_data", "freshness"),
    ("null_values", "completeness"),
    ("duplicate_keys", "uniqueness"),
    ("schema_change", "schema"),
    ("distribution_drift", "distribution_drift"),
    ("upstream_failure", "validity"),
    ("volume_spike", "volume"),
])
def test_quality_detects_scenario(scenario, expected_failed):
    batch = generate_batch(scenario, seed=23)
    checks, metrics, score = evaluate_quality(batch, baseline_from_runs([]))
    failed_codes = {c.code for c in checks if not c.passed}
    if expected_failed is None:
        assert not failed_codes
        assert score == 100
    else:
        assert expected_failed in failed_codes
        assert score < 100


def test_seed_and_incident_workflow(temp_db):
    result = seed_demo()
    assert result["created_runs"] == 16
    assert summary()["runs_total"] == 16
    incidents = list_incidents()
    assert len(incidents) >= 4
    incident = incidents[0]
    assert incident["root_cause"]["evidence"]
    assert not incident["root_cause"]["human_review_required"] is False
    assert get_incident(incident["id"])["events"]
    changed = update_incident(incident["id"], "acknowledge")
    assert changed["status"] == "ACKNOWLEDGED"
    changed = update_incident(incident["id"], "resolve")
    assert changed["status"] == "RESOLVED"
    assert changed["resolved_at"] is not None
    assert len(get_incident(incident["id"])["events"]) >= 5


def test_upstream_rca_not_reading_scenario():
    batch = generate_batch("upstream_failure", seed=25)
    checks, metrics, _ = evaluate_quality(batch, baseline_from_runs([]))
    reason = analyze_root_cause(checks, batch.upstream, metrics, {"message": "unit test"})
    assert "raw_payments" in reason["conclusion"]
    assert reason["human_review_required"] is True
    assert "scenario" not in reason


def test_api_routes(temp_db):
    from fastapi.testclient import TestClient
    from observability.api import app
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.post("/api/seed").status_code == 200
        initial = client.get("/api/summary").json()
        assert initial["runs_total"] == 16
        response = client.post("/api/runs", json={"scenario": "schema_change"})
        assert response.status_code == 200
        value = response.json()
        assert value["status"] == "FAILED"
        iid = value["incident_id"]
        assert iid is not None
        assert client.get(f"/api/incidents/{iid}").status_code == 200
        assert client.get("/api/lineage").json()["nodes"]
        assert client.post("/api/runs", json={"scenario": "invalid"}).status_code == 400
        assert client.get("/api/runs/999999").status_code == 404

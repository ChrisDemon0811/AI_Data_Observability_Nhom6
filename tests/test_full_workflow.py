"""Integration coverage of all five tasks, using only isolated local data."""
import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from observability import database, service, settings
from observability.api import app
from observability.generator import generate_batch, SCENARIOS
from observability.quality import evaluate_quality, baseline_from_runs


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_all_scenarios_persist_monitoring_context(isolated_db, scenario):
    with TestClient(app) as client:
        result = client.post("/api/runs", json={"scenario": scenario})
        assert result.status_code == 200
        run = result.json()
        detail = client.get(f"/api/runs/{run['id']}").json()
        assert len(detail["checks"]) == 8
        assert all(check["run_id"] == run["id"] and check["checked_at"] for check in detail["checks"])
        assert [log["type"] for log in detail["execution_logs"]] == ["STARTED", "SNAPSHOT_SAVED", "FINISHED"]
        assert detail["metrics"]["statistics"]["ks"]["status"] == "NO_PREVIOUS_RUN"
        sources = client.get(f"/api/runs/{run['id']}/sources").json()
        assert sources["stored_counts"]["raw_orders"] == run["row_count"]
        if scenario == "normal":
            assert run["status"] == "HEALTHY"
        else:
            assert run["status"] == "FAILED"
            incident = client.get(f"/api/incidents/{run['incident_id']}").json()
            reason = incident["root_cause"]
            assert reason["analysis_source"] == "RULE_BASED_FALLBACK"
            assert reason["ai_analysis"]["human_review_required"] is True
            assert reason["analysis_context"]["run"]["id"] == run["id"]
            assert len(reason["analysis_context"]["run"]["execution_logs"]) == 3
            assert reason["analysis_context"]["run"]["metrics"]["statistics"]
            assert client.post(f"/api/incidents/{run['incident_id']}/acknowledge").json()["status"] == "ACKNOWLEDGED"
            simulated = client.post(f"/api/incidents/{run['incident_id']}/simulate-fix").json()
            assert any(event["type"] == "DEMO_REMEDIATION" for event in simulated["events"])
            assert client.post(f"/api/incidents/{run['incident_id']}/resolve").json()["status"] == "RESOLVED"


def test_new_endpoints_and_reanalysis_are_bounded(isolated_db):
    with TestClient(app) as client:
        configuration = client.get("/api/config").json()
        assert configuration["quality_thresholds"]["freshness_minutes"] == 60
        assert "LLM_API_KEY" not in json.dumps(configuration)
        run = client.post("/api/runs", json={"scenario": "upstream_failure"}).json()
        incident_id = run["incident_id"]
        for _ in range(3):
            result = client.post(f"/api/incidents/{incident_id}/analyze")
            assert result.status_code == 200
            analysis = result.json()["root_cause"]["ai_analysis"]
            assert analysis["analysis_source"] == "RULE_BASED_FALLBACK"
            assert "ai_analysis" not in analysis
        assert client.post("/api/incidents/999999/analyze").status_code == 404
        assert len(json.dumps(result.json()["root_cause"])) < 35000


def test_threshold_labels_use_configured_values(monkeypatch):
    monkeypatch.setitem(settings.CHECK_LIMITS, "freshness_minutes", 1)
    monkeypatch.setitem(settings.CHECK_LIMITS, "completeness", 0.90)
    checks, _, _ = evaluate_quality(generate_batch("normal", seed=23), baseline_from_runs([]))
    by_code = {check.code: check for check in checks}
    assert by_code["freshness"].passed is False
    assert by_code["freshness"].expected == "≤ 1 phút"
    assert by_code["completeness"].expected == "≥ 90.00%"


def test_dashboard_five_tabs_render_against_real_api(isolated_db, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import requests

    for seed in range(7):
        service.run_pipeline("normal", seed=100 + seed * 11, export_csv=False)
    service.run_pipeline("distribution_drift", seed=23, export_csv=False)
    with TestClient(app) as client:
        def request(method, url, **kwargs):
            path = urlsplit(url)
            return client.request(method, path.path + ("?" + path.query if path.query else ""), json=kwargs.get("json"))

        monkeypatch.setattr(requests, "request", request)
        view = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run(timeout=30)
        assert not list(view.exception), [error.message for error in view.exception]
        assert len(view.tabs) == 5
        assert any("Z-score" in item.value for item in view.markdown)
        assert any("Email / Slack" in item.value for item in view.markdown)
        assert any("RULE_BASED_FALLBACK" in item.value for item in view.info)
        assert any("email_mock" in expander.label for expander in view.expander)


def test_statistics_recovery_and_reopen_history(isolated_db):
    for seed in range(7):
        service.run_pipeline("normal", seed=100 + seed * 11, export_csv=False)
    drift = service.run_pipeline("distribution_drift", seed=23, export_csv=False)
    assert drift["metrics"]["statistics"]["zscore"]["anomalous"] is True
    assert drift["metrics"]["statistics"]["ks"]["anomalous"] is True
    assert drift["metrics"]["statistics"]["ks"]["previous_run_id"] == drift["id"] - 1
    recovery = service.run_pipeline("normal", seed=24, export_csv=False)
    assert recovery["metrics"]["statistics"]["ks"]["previous_run_id"] == drift["id"]
    assert recovery["status"] == "HEALTHY"
    database.engine.dispose()
    database.init_db()
    assert service.get_run(drift["id"])["metrics"]["statistics"] == drift["metrics"]["statistics"]
    with database.get_session() as session:
        assert session.scalar(select(func.count()).select_from(database.PipelineRun)) == 9
        assert all(run.id != drift["id"] for run in service.healthy_history(session))


def test_llm_success_persisted_then_timeout_restores_fallback(isolated_db, monkeypatch):
    from types import SimpleNamespace
    import requests

    for name, value in {"LLM_MODE": "openai_compatible", "LLM_ALLOW_EXTERNAL": "true",
                        "LLM_BASE_URL": "https://provider.example/v1", "LLM_MODEL": "test-model",
                        "LLM_API_KEY": "test-key-not-a-real-credential"}.items():
        monkeypatch.setenv(name, value)
    output = {
        "conclusion": "Nguồn thanh toán có thể gián đoạn", "confidence": 0.8,
        "affected_node": "raw_payments", "explanation": "Telemetry lỗi đi kèm dữ liệu thiếu; cần kiểm chứng.",
        "evidence_refs": ["check:completeness", "upstream:raw_payments"], "alternatives": [],
        "checklist": ["Đối chiếu log nguồn trước khi kết luận"],
        "suspected_upstream": [{"node_id": "raw_payments", "evidence_refs": ["upstream:raw_payments"]}],
        "human_review_required": True,
    }
    envelope = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}

    def post(*args, **kwargs):
        assert database.engine.pool.checkedout() == 0, "LLM HTTP must not hold DB connections"
        sent = json.dumps(kwargs["json"], ensure_ascii=False)
        assert "ORD-" not in sent and "payload_json" not in sent
        return SimpleNamespace(status_code=200, content=json.dumps(envelope).encode(), json=lambda: envelope, close=lambda: None)

    monkeypatch.setattr(requests, "post", post)
    run = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    incident_id = run["incident_id"]
    incident = service.get_incident(incident_id)
    original_rule_conclusion = incident["root_cause"]["conclusion"]
    assert incident["root_cause"]["analysis_source"] == "LLM"
    assert incident["root_cause"]["ai_analysis"]["conclusion"] == output["conclusion"]

    def timeout(*args, **kwargs):
        raise requests.Timeout("mock provider timeout")

    monkeypatch.setattr(requests, "post", timeout)
    result = service.analyze_incident_rca(incident_id)
    assert result["root_cause"]["conclusion"] == original_rule_conclusion
    analysis = result["root_cause"]["ai_analysis"]
    assert analysis["analysis_source"] == "RULE_BASED_FALLBACK"
    assert analysis["fallback_reason_code"] == "REQUEST_TIMEOUT"


def test_legacy_exact_five_table_upgrade_preserves_history(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", connect_args={"check_same_thread": False})
    original_models = (database.PipelineRun, database.QualityCheck, database.Incident, database.IncidentEvent, database.Alert)
    database.Base.metadata.create_all(engine, tables=[model.__table__ for model in original_models])
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    try:
        assert len(inspect(engine).get_table_names()) == 5
        with database.get_session() as session:
            legacy = database.PipelineRun(dataset="fact_orders", scenario="normal", started_at=database.now_utc(),
                                          finished_at=database.now_utc(), status="HEALTHY", row_count=400,
                                          quality_score=100, ml_anomaly=False, ml_anomaly_score=0,
                                          metrics_json='{}', schema_json='[]', sources_json='{}', preview_json='[]', duration_ms=1)
            session.add(legacy)
            session.flush()
            before = database.run_to_dict(legacy)
        database.init_db()
        after = service.get_run(legacy.id)
        assert {key: after[key] for key in before} == before
        assert after["execution_logs"] == []
        assert "pipeline_events" in inspect(engine).get_table_names()
        assert len(service.list_runs()) == 1
    finally:
        engine.dispose()

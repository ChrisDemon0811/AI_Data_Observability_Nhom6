"""Render actual Streamlit UI against FastAPI and isolated DB; provider is mocked."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest
import requests
from streamlit.testing.v1 import AppTest

from observability import database, service
from observability.api import app


def provider_output(version="ONE"):
    return {
        "conclusion": f"PROVIDER_SUMMARY_{version}", "confidence": 0.8,
        "affected_node": "raw_payments", "explanation": f"PROVIDER_EXPLANATION_{version}",
        "evidence_refs": ["check:completeness", "upstream:raw_payments"],
        "alternatives": [{"hypothesis": f"PROVIDER_HYPOTHESIS_{version}", "priority": 0.5,
                          "node": "stg_orders", "evidence_refs": ["check:completeness"]}],
        "checklist": [f"PROVIDER_CHECKLIST_{version}"],
        "suspected_upstream": [{"node_id": "raw_payments", "evidence_refs": ["upstream:raw_payments"]}],
        "human_review_required": True,
    }


def response(output):
    envelope = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}
    return SimpleNamespace(status_code=200, content=json.dumps(envelope).encode(), json=lambda: envelope, close=lambda: None)


def enable_provider(monkeypatch):
    for name, value in {"LLM_MODE": "openai_compatible", "LLM_ALLOW_EXTERNAL": "true",
                        "LLM_API_KEY": "mock-private-key-not-real", "LLM_MODEL": "mock-model",
                        "LLM_BASE_URL": "https://provider.invalid/v1"}.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def dashboard(isolated_db, monkeypatch):
    controls = {"failure": None, "calls": []}
    monkeypatch.setattr(requests, "post", Mock(side_effect=AssertionError("Unexpected provider HTTP")))
    with TestClient(app) as client:
        def request(method, url, **kwargs):
            parsed = urlsplit(url)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            controls["calls"].append((method, path))
            if controls.get("hide_incidents") and parsed.path == "/api/incidents":
                return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [])
            if controls.get("fail_read") == path:
                rsp = requests.Response()
                rsp.status_code, rsp.url, rsp._content = 500, url, b'{"detail":"read failed"}'
                return rsp
            if controls["failure"] and path.endswith("/analyze"):
                rsp = requests.Response()
                rsp.status_code = 500 if controls["failure"] == "http" else 200
                rsp.url = url
                rsp._content = b'not-json' if controls["failure"] == "json" else b'{"detail":"failed"}'
                return rsp
            result = client.request(method, path, json=kwargs.get("json"))
            if controls.get("fail_read_after_post") and method == "POST" and path.endswith("/analyze"):
                controls["fail_read"] = path.removesuffix("/analyze")
            return result

        monkeypatch.setattr(requests, "request", request)
        def render():
            return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run(timeout=30)
        yield render, controls


def ai_text(view):
    tab = view.tabs[4]
    return "\n".join(str(item.value) for kind in ("markdown", "caption", "info", "warning", "json")
                     for item in getattr(tab, kind))


def assert_report(view, source):
    assert not list(view.exception), [exc.message for exc in view.exception]
    text = ai_text(view)
    for label in ("Thông tin incident", "Incident ID", "Severity:", "Owner:", "Status:",
                  source, "Tóm tắt phân tích", "Upstream nghi ngờ", "Giả thuyết nguyên nhân",
                  "Bằng chứng", "Recommended checklist", "Human review required",
                  "Data lineage", "Execution events", "Context đã sử dụng"):
        assert label in text
    assert view.button(key="reanalyze")
    return text


def test_no_incident_and_no_previous_incident_state(dashboard):
    render, _ = dashboard
    service.run_pipeline("normal", seed=23, export_csv=False)
    view = render()
    assert not list(view.exception)
    assert "Chưa có incident" in ai_text(view)
    assert not [button for button in view.button if button.key == "reanalyze"]
    assert "PROVIDER_" not in ai_text(view)


def test_incident_disappears_without_reusing_previous_ui(dashboard):
    render, controls = dashboard
    service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    view = render()
    assert_report(view, "RULE_BASED_FALLBACK")
    controls["hide_incidents"] = True
    view.run(timeout=30)
    assert not list(view.exception)
    assert "Chưa có incident" in ai_text(view)
    assert "Tóm tắt phân tích hiện tại" not in ai_text(view)
    assert not [button for button in view.button if button.key == "reanalyze"]


def test_offline_without_key_shows_complete_report(dashboard, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    render, _ = dashboard
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    text = assert_report(render(), "RULE_BASED_FALLBACK")
    assert "OFFLINE_MODE" in text
    assert "Thời điểm phân tích" in text
    assert service.get_incident(result["incident_id"])["root_cause"]["analyzed_at"]


def test_llm_success_renders_without_fallback_reason(dashboard, monkeypatch):
    render, _ = dashboard
    enable_provider(monkeypatch)
    post = Mock(return_value=response(provider_output()))
    monkeypatch.setattr(requests, "post", post)
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    text = assert_report(render(), "Nguồn phân tích: LLM.")
    for marker in ("PROVIDER_SUMMARY_ONE", "PROVIDER_HYPOTHESIS_ONE", "PROVIDER_CHECKLIST_ONE", "raw_payments", "upstream:raw_payments"):
        assert marker in text
    assert "Lý do fallback" not in text
    assert post.call_count == 1
    assert service.get_incident(result["incident_id"])["root_cause"]["ai_analysis"]["fallback_reason_code"] is None


def test_click_reanalyze_reads_latest_committed_result(dashboard, monkeypatch):
    render, controls = dashboard
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    view = render()
    assert "RULE_BASED_FALLBACK" in ai_text(view)
    enable_provider(monkeypatch)
    monkeypatch.setattr(requests, "post", Mock(return_value=response(provider_output("TWO"))))
    controls["calls"].clear()
    view.button(key="reanalyze").click().run(timeout=30)
    text = assert_report(view, "Nguồn phân tích: LLM.")
    assert "PROVIDER_SUMMARY_TWO" in text and "PROVIDER_CHECKLIST_TWO" in text
    assert "Lý do fallback" not in text
    path = f"/api/incidents/{result['incident_id']}"
    calls = controls["calls"]
    assert ("POST", path + "/analyze") in calls
    assert ("GET", path) in calls[calls.index(("POST", path + "/analyze")) + 1:]
    stored = service.get_incident(result["incident_id"])["root_cause"]
    assert stored["ai_analysis"]["summary"] == "PROVIDER_SUMMARY_TWO"
    assert stored["analysis_source"] == "LLM" and stored["analyzed_at"]


@pytest.mark.parametrize("failure,reason", [
    ("timeout", "REQUEST_TIMEOUT"), (401, "HTTP_ERROR"), (429, "HTTP_ERROR"), (500, "HTTP_ERROR"),
    ("bad_json", "INVALID_RESPONSE"), ("missing_field", "INVALID_RESPONSE"),
    ("evidence", "INVALID_RESPONSE"), ("upstream", "INVALID_RESPONSE"),
])
def test_provider_failure_reanalyzes_to_visible_fallback(dashboard, monkeypatch, failure, reason):
    render, _ = dashboard
    enable_provider(monkeypatch)
    post = Mock(return_value=response(provider_output()))
    monkeypatch.setattr(requests, "post", post)
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    view = render()
    assert "PROVIDER_SUMMARY_ONE" in ai_text(view)
    if failure == "timeout":
        post.side_effect = requests.Timeout("mock-private-key-not-real")
    elif isinstance(failure, int):
        post.return_value.status_code = failure
    else:
        invalid = provider_output()
        if failure == "bad_json":
            invalid = "not an object"
        elif failure == "missing_field":
            del invalid["checklist"]
        elif failure == "evidence":
            invalid["evidence_refs"] = ["invented"]
        else:
            invalid["suspected_upstream"][0]["node_id"] = "invented_table"
        post.return_value = response(invalid)
    view.button(key="reanalyze").click().run(timeout=30)
    text = assert_report(view, "RULE_BASED_FALLBACK")
    assert reason in text and "PROVIDER_SUMMARY_ONE" not in text
    assert "mock-private-key-not-real" not in text
    assert service.get_incident(result["incident_id"])["root_cause"]["analysis_source"] == "RULE_BASED_FALLBACK"


@pytest.mark.parametrize("failure", ["http", "json"])
def test_api_failure_has_error_and_preserves_prior_result(dashboard, failure):
    render, controls = dashboard
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    before = deepcopy(service.get_incident(result["incident_id"])["root_cause"])
    view = render()
    controls["failure"] = failure
    view.button(key="reanalyze").click().run(timeout=30)
    assert not list(view.exception)
    assert view.tabs[4].error
    assert "Đang hiển thị kết quả đã lưu trước" in ai_text(view)
    assert service.get_incident(result["incident_id"])["root_cause"] == before


def test_post_commits_but_refresh_fails_does_not_claim_success(dashboard, monkeypatch):
    render, controls = dashboard
    result = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    view = render()
    enable_provider(monkeypatch)
    monkeypatch.setattr(requests, "post", Mock(return_value=response(provider_output("COMMITTED"))))
    controls["fail_read_after_post"] = True
    view.button(key="reanalyze").click().run(timeout=30)
    assert not list(view.exception)
    assert view.tabs[4].error
    assert "PROVIDER_SUMMARY_COMMITTED" not in ai_text(view)
    assert service.get_incident(result["incident_id"])["root_cause"]["ai_analysis"]["summary"] == "PROVIDER_SUMMARY_COMMITTED"
    controls["fail_read"] = None
    controls["fail_read_after_post"] = False
    view.run(timeout=30)
    assert "PROVIDER_SUMMARY_COMMITTED" in assert_report(view, "Nguồn phân tích: LLM.")


@pytest.mark.parametrize("minutes,breached", [(-1, False), (1, True), (0, False)])
def test_resolved_sla_uses_resolution_time(isolated_db, minutes, breached):
    result = service.run_pipeline("schema_change", seed=23, export_csv=False)
    with database.get_session() as session:
        item = session.get(database.Incident, result["incident_id"])
        item.sla_deadline = database.now_utc() - timedelta(days=2)
        item.status = "RESOLVED"
        item.resolved_at = item.sla_deadline + timedelta(minutes=minutes)
    assert service.get_incident(result["incident_id"])["sla_breached"] is breached

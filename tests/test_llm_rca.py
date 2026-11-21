"""Offline/provider contract tests: every HTTP request is intercepted."""

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest
import requests

from observability import llm_rca
from observability.generator import generate_batch
from observability.quality import baseline_from_runs, evaluate_quality
from observability.rca import analyze_root_cause


@pytest.fixture(autouse=True)
def provider(monkeypatch):
    for name in ("LLM_MODE", "LLM_ALLOW_EXTERNAL", "LLM_API_KEY", "LLM_MODEL",
                 "LLM_BASE_URL", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    post = Mock(side_effect=AssertionError("Unexpected external HTTP request"))
    monkeypatch.setattr(llm_rca.requests, "post", post)
    # Also prevent accidental real HTTP if the implementation changes transport.
    monkeypatch.setattr(requests.sessions.Session, "request",
                        Mock(side_effect=AssertionError("Real HTTP is forbidden")))
    return post


@pytest.fixture
def rule_reason():
    batch = generate_batch("upstream_failure", seed=25)
    checks, metrics, _ = evaluate_quality(batch, baseline_from_runs([]))
    return analyze_root_cause(checks, batch.upstream, metrics, {"message": "rule evidence"})


@pytest.fixture
def inputs():
    from observability.service import LINEAGE

    return {
        "run": {
            "id": 12, "dataset": "fact_orders", "status": "FAILED", "row_count": 400,
            "quality_score": 75.0, "ml_anomaly": True, "ml_anomaly_score": 82.0,
            "scenario": "DO_NOT_SEND_SCENARIO", "duration_ms": 140,
            "preview": [{"order_id": "PRIVATE_ORDER", "customer_id": 9988, "amount": 132.12}],
            "rows": [{"order_id": "PRIVATE_ORDER"}], "api_key": "PRIVATE_SOURCE_KEY",
            "metrics": {
                "row_count": 400, "amount_mean": 250.0, "validity": 0.4,
                "quality_thresholds": {"payment_validity": 0.97, "volume_ratio_low": 0.5,
                                       "source_key": "PRIVATE_THRESHOLD_KEY", "unknown": 123},
                "row_samples": [{"customer_id": 9988}], "bad_float": float("nan"),
                "ml": {"trained_samples": 10, "threshold": -0.05, "test_decision": -0.15,
                       "message": "PRIVATE_ML_MESSAGE",
                       "most_changed_features": [{"feature": "validity", "deviation": 8.2}]},
                "statistics": {
                    "zscore": {"status": "ALERT", "anomalous": True, "threshold": 3.0,
                               "min_history": 5, "raw_samples": ["PRIVATE_STAT_SAMPLE"],
                               "metrics": {
                                   "amount_mean": {"status": "ALERT", "anomalous": True,
                                                   "reason": "zero_variance", "value": 250.0,
                                                   "baseline_mean": 200.0, "baseline_std": 0.0,
                                                   "history_count": 8, "zscore": None},
                                   "row_count": {"status": "OK", "anomalous": False,
                                                 "reason": None, "value": 400, "zscore": 0.2,
                                                 "baseline_mean": 398.0, "baseline_std": 10.0,
                                                 "history_count": 8},
                               }},
                    "ks": {"status": "ALERT", "anomalous": True, "reason": None,
                           "run_id": 12, "previous_run_id": 11, "current_samples": 400,
                           "previous_samples": 400, "ks_distance": 0.25, "pvalue": 0.001,
                           "significant": True, "effect_detected": True, "alpha": 0.05,
                           "min_samples": 20, "effect_threshold": 0.1,
                           "samples": ["PRIVATE_KS_SAMPLE"]},
                },
            },
            "upstream": {
                "raw_payments": {"status": "ERROR", "error_count": 46, "rows": 140,
                                 "delay_minutes": 150, "api_key": "PRIVATE_SOURCE_KEY",
                                 "rows_preview": [{"order_id": "PRIVATE_ORDER"}]},
                "unregistered_node": {"status": "ERROR", "error_count": 10},
            },
            "execution_logs": [
                {"type": "STARTED", "timestamp": "2026-11-21T01:00:00Z", "message": "PRIVATE_LOG"},
                {"event_type": "SNAPSHOT_SAVED", "timestamp": "2026-11-21T01:00:01Z"},
                {"type": "FINISHED", "timestamp": "2026-11-21T01:00:02Z"},
            ],
        },
        "checks": [{"code": "validity", "passed": False, "observed": 0.4, "severity": "HIGH",
                    "expected": "PRIVATE_EXPECTED", "details": "PRIVATE_CHECK_DETAILS",
                    "title": "PRIVATE_TITLE"}],
        "incident": {"id": 5, "run_id": 12, "status": "OPEN", "severity": "P2 - HIGH",
                     "root_cause": {"ai_analysis": "PRIVATE_PRIOR_ANALYSIS"}, "owner": "PRIVATE_OWNER"},
        "events": [{"type": "PIPELINE_FAILED", "timestamp": "2026-11-21T01:00:02Z",
                    "message": "PRIVATE_EVENT", "actor": "PRIVATE_ACTOR"}],
        "lineage": deepcopy(LINEAGE),
    }


@pytest.fixture
def context(inputs):
    return llm_rca.build_context(**inputs)


@pytest.fixture
def output():
    return {
        "conclusion": "Payment source interruption may explain failed validity.",
        "confidence": 0.8, "affected_node": "raw_payments",
        "explanation": "Upstream errors coincide with a failed validity check; verify with the owner.",
        "evidence_refs": ["check:validity", "upstream:raw_payments", "statistics:ks", "ml"],
        "alternatives": [{"hypothesis": "Staging mapping may need review.", "priority": 0.5,
                          "node": "stg_orders", "evidence_refs": ["check:validity"]}],
        "checklist": ["Review payment ingestion and verify the failed validity check."],
        "suspected_upstream": [{"node_id": "raw_payments", "evidence_refs": ["upstream:raw_payments"]}],
        "human_review_required": True,
    }


def enable(monkeypatch):
    for key, value in {
        "LLM_MODE": "openai_compatible", "LLM_ALLOW_EXTERNAL": "true",
        "LLM_API_KEY": "sk-PRIVATE_PROVIDER_KEY", "LLM_MODEL": "test-model",
        "LLM_BASE_URL": "https://provider.invalid/v1/",
    }.items():
        monkeypatch.setenv(key, value)


def mock_response(provider, output, *, status=200, envelope=None):
    if envelope is None:
        envelope = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(output)}}]}
    response = Mock(status_code=status, content=json.dumps(envelope).encode())
    response.json.return_value = envelope
    provider.side_effect = None
    provider.return_value = response
    return response


def assert_fallback(result, reason, code):
    for key, value in reason.items():
        if key not in {"ai_analysis", "analysis_context", "rule_based_reason", "analysis_source", "fallback_reason_code"}:
            assert result[key] == value
    assert result["analysis_source"] == "RULE_BASED_FALLBACK"
    assert result["fallback_reason_code"] == code
    assert result["human_review_required"] is True
    assert result["summary"] == reason["conclusion"]
    assert result["recommended_checklist"] == reason["checklist"]
    assert result["possible_root_causes"][0]["hypothesis"] == reason["conclusion"]
    json.dumps(result, allow_nan=False)


def test_context_preserves_aggregate_statistics_logs_and_excludes_private_data(inputs, context):
    assert context["data_mode"] == "DEMO_SYNTHETIC"
    assert context["upstream_telemetry_simulated"] is True
    serialized = json.dumps(context, allow_nan=False)
    assert "PRIVATE_" not in serialized
    assert "DO_NOT_SEND_SCENARIO" not in serialized
    assert "unregistered_node" not in serialized
    assert context["run"]["row_count"] == 400
    assert context["checks"][0]["observed"] == 0.4
    assert context["run"]["metrics"]["quality_thresholds"] == {
        "payment_validity": 0.97, "volume_ratio_low": 0.5,
    }
    stats = context["run"]["metrics"]["statistics"]
    assert stats["zscore"]["metrics"]["amount_mean"]["zscore"] is None
    assert stats["zscore"]["metrics"]["amount_mean"]["reason"] == "zero_variance"
    assert stats["zscore"]["threshold"] == 3.0
    assert stats["ks"]["pvalue"] == 0.001
    assert stats["ks"]["significant"] is True
    assert stats["ks"]["previous_run_id"] == 11
    assert context["run"]["metrics"]["ml"]["most_changed_features"][0]["deviation"] == 8.2
    assert context["run"]["execution_logs"] == [
        {"type": "STARTED", "timestamp": "2026-11-21T01:00:00+00:00"},
        {"type": "SNAPSHOT_SAVED", "timestamp": "2026-11-21T01:00:01+00:00"},
        {"type": "FINISHED", "timestamp": "2026-11-21T01:00:02+00:00"},
    ]
    assert "statistics:zscore" in {e["id"] for e in context["evidence"]}
    assert "execution_log:0" in {e["id"] for e in context["evidence"]}
    assert "threshold:payment_validity" in {e["id"] for e in context["evidence"]}
    assert inputs["run"]["execution_logs"][0]["message"] == "PRIVATE_LOG"
    # Provider-bound re-sanitization must preserve all approved context, including stats/logs.
    assert llm_rca.build_context(**{key: context[key] for key in inputs}) == context


def test_offline_is_deterministic_and_preserves_actual_rule_reason(context, rule_reason, provider):
    first = llm_rca.analyze_incident(context, rule_reason)
    assert first == llm_rca.analyze_incident(context, rule_reason)
    assert_fallback(first, rule_reason, "OFFLINE_MODE")
    assert first["suspected_upstream"] == [{"node_id": "raw_payments", "evidence_refs": []}]
    first["checklist"].append("local edit")
    assert "local edit" not in rule_reason["checklist"]
    provider.assert_not_called()


@pytest.mark.parametrize("optin", [None, "false", "1", "yes", ""])
def test_external_requires_explicit_optin(monkeypatch, context, rule_reason, provider, optin):
    enable(monkeypatch)
    if optin is None:
        monkeypatch.delenv("LLM_ALLOW_EXTERNAL")
    else:
        monkeypatch.setenv("LLM_ALLOW_EXTERNAL", optin)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "EXTERNAL_NOT_ALLOWED")
    provider.assert_not_called()


@pytest.mark.parametrize("name,value", [
    ("LLM_MODE", "unsupported"), ("LLM_API_KEY", ""), ("LLM_MODEL", ""),
    ("LLM_BASE_URL", ""), ("LLM_BASE_URL", "https://user:password@provider.invalid/v1"),
    ("LLM_BASE_URL", "http://provider.invalid/v1"), ("LLM_BASE_URL", "https://provider.invalid/v1?key=abc"),
    ("LLM_BASE_URL", "https://provider.invalid:invalid/v1"), ("LLM_TIMEOUT_SECONDS", "nan"),
    ("LLM_TIMEOUT_SECONDS", "inf"), ("LLM_TIMEOUT_SECONDS", "0"),
    ("LLM_TIMEOUT_SECONDS", "31"), ("LLM_TIMEOUT_SECONDS", "invalid"),
])
def test_invalid_config_never_requests(monkeypatch, context, rule_reason, provider, name, value):
    enable(monkeypatch)
    monkeypatch.setenv(name, value)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_CONFIG")
    provider.assert_not_called()


def test_success_uses_runtime_config_and_only_sanitized_context(monkeypatch, inputs, context, rule_reason, provider, output, caplog):
    assert llm_rca.analyze_incident(context, rule_reason)["analysis_source"] == "RULE_BASED_FALLBACK"
    enable(monkeypatch)  # Configuration changes after import take effect immediately.
    monkeypatch.setenv("SOURCE_API_KEY", "PRIVATE_SOURCE_KEY")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "4")
    response = mock_response(provider, output)
    # Inject rows/forged evidence after building: analyze_incident must sanitize again.
    context["run"]["preview"] = inputs["run"]["preview"]
    context["evidence"].append({"id": "forged", "data": "PRIVATE_FORGED"})
    result = llm_rca.analyze_incident(context, rule_reason)
    assert result["analysis_source"] == "LLM"
    assert result["fallback_reason_code"] is None
    assert result["conclusion"] == output["conclusion"]
    assert result["rule_based_reason"] == rule_reason
    assert result["human_review_required"] is True
    assert result["summary"] == result["conclusion"]
    assert result["recommended_checklist"] == result["checklist"]
    assert all(isinstance(item, str) for item in result["evidence"])
    assert "pvalue" in result["evidence"][2]
    provider.assert_called_once()
    args, kwargs = provider.call_args
    assert args == ("https://provider.invalid/v1/chat/completions",)
    assert kwargs["timeout"] == (3.0, 4.0)
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Authorization"] == "Bearer sk-PRIVATE_PROVIDER_KEY"
    payload = kwargs["json"]
    assert payload["model"] == "test-model"
    assert payload["temperature"] == 0
    assert payload["response_format"] == {"type": "json_object"}
    assert "PRIVATE_" not in json.dumps(payload)
    assert rule_reason["conclusion"] not in json.dumps(payload)
    sent_context = json.loads(payload["messages"][1]["content"])
    assert sent_context["run"]["metrics"]["statistics"]["ks"]["pvalue"] == 0.001
    assert len(sent_context["run"]["execution_logs"]) == 3
    assert sent_context["run"]["metrics"]["quality_thresholds"]["payment_validity"] == 0.97
    assert not caplog.text
    response.close.assert_called_once()
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("error,code", [
    (requests.Timeout("sk-PRIVATE_PROVIDER_KEY PRIVATE_SOURCE_KEY"), "REQUEST_TIMEOUT"),
    (requests.ConnectionError("sk-PRIVATE_PROVIDER_KEY PRIVATE_SOURCE_KEY"), "REQUEST_FAILED"),
])
def test_request_errors_are_safe_and_never_retried(monkeypatch, context, rule_reason, provider, error, code, caplog):
    enable(monkeypatch)
    provider.side_effect = error
    result = llm_rca.analyze_incident(context, rule_reason)
    assert_fallback(result, rule_reason, code)
    assert "PRIVATE_" not in json.dumps(result) + caplog.text
    provider.assert_called_once()


@pytest.mark.parametrize("status", [302, 401, 429, 500])
def test_http_errors_never_expose_body_or_retry(monkeypatch, context, rule_reason, provider, output, status):
    enable(monkeypatch)
    response = mock_response(provider, output, status=status)
    response.content = b"PRIVATE_SERVER_BODY"
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "HTTP_ERROR")
    provider.assert_called_once()
    response.json.assert_not_called()
    response.close.assert_called_once()


@pytest.mark.parametrize("field,value", [
    ("human_review_required", False), ("human_review_required", 1), ("human_review_required", "true"),
    ("confidence", "0.8"), ("confidence", 1.1), ("confidence", float("nan")),
    ("conclusion", ""), ("conclusion", "   "), ("conclusion", "x" * 1201),
    ("checklist", []), ("checklist", ["step"] * 11), ("checklist", "step"),
    ("evidence_refs", []), ("evidence_refs", ["run"] * 13), ("evidence_refs", ["invented"]),
    ("affected_node", "invented_source"), ("affected_node", "raw_orders -> stg_orders"),
    ("alternatives", [{"hypothesis": "test", "priority": 0.2, "node": "invented", "evidence_refs": ["run"]}]),
    ("alternatives", [{"hypothesis": "test", "priority": 0.2, "node": "stg_orders", "evidence_refs": ["missing"]}]),
    ("suspected_upstream", [{"node_id": "unregistered_node", "evidence_refs": ["upstream:raw_payments"]}]),
    ("suspected_upstream", [{"node_id": "raw_orders", "evidence_refs": ["upstream:raw_payments"]}]),
    ("suspected_upstream", [{"node_id": "raw_payments", "evidence_refs": ["check:validity"]}]),
    ("suspected_upstream", [{"node_id": "dashboard_sales", "evidence_refs": ["upstream:raw_payments"]}]),
    ("unexpected_field", "reject extras"),
])
def test_invalid_or_ungrounded_output_falls_back(monkeypatch, context, rule_reason, provider, output, field, value):
    enable(monkeypatch)
    output[field] = value
    response = mock_response(provider, output)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_RESPONSE")
    provider.assert_called_once()
    response.close.assert_called_once()


@pytest.mark.parametrize("envelope", [
    [], {}, {"choices": []}, {"choices": [None]}, {"choices": "invalid"},
    {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": {"invalid": "object"}}}]},
])
def test_malformed_chat_envelopes(monkeypatch, context, rule_reason, provider, envelope):
    enable(monkeypatch)
    mock_response(provider, None, envelope=envelope)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_RESPONSE")


def test_non_json_and_oversized_response(monkeypatch, context, rule_reason, provider, output):
    enable(monkeypatch)
    response = mock_response(provider, output)
    response.json.side_effect = ValueError("PRIVATE_RAW_BODY")
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_RESPONSE")
    response.json.reset_mock(side_effect=True)
    response.content = b"x" * (llm_rca.MAX_RESPONSE_BYTES + 1)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_RESPONSE")
    response.json.assert_not_called()


def test_reanalysis_strips_previous_nested_analysis_and_keeps_size_bounded(monkeypatch, context, rule_reason, provider, output):
    current = deepcopy(rule_reason)
    for _ in range(5):
        current.update(ai_analysis={"nested": deepcopy(current)}, analysis_context=context,
                       rule_based_reason=deepcopy(current))
        current = llm_rca.analyze_incident(context, current)
        assert not {"ai_analysis", "analysis_context", "rule_based_reason"} & current.keys()
    assert len(json.dumps(current)) < len(json.dumps(rule_reason)) * 4
    enable(monkeypatch)
    current.update(ai_analysis={"previous": "PRIVATE_OLD"}, analysis_context=context,
                   rule_based_reason={"previous": "PRIVATE_OLD"})
    mock_response(provider, output)
    result = llm_rca.analyze_incident(context, current)
    assert not {"ai_analysis", "analysis_context", "rule_based_reason"} & result["rule_based_reason"].keys()
    assert result["rule_based_reason"]["conclusion"] == rule_reason["conclusion"]
    assert "PRIVATE_OLD" not in json.dumps(result)


def test_fallback_nodes_are_known_and_path_reason_is_preserved(context, rule_reason):
    rule_reason["affected_node"] = "raw_orders / raw_payments → stg_orders / invented_source"
    result = llm_rca.analyze_incident(context, rule_reason)
    assert result["affected_node"] == rule_reason["affected_node"]
    assert [item["node_id"] for item in result["suspected_upstream"]] == ["raw_orders", "raw_payments", "stg_orders"]


def test_bad_timestamps_nonfinite_values_and_unknown_fields_are_dropped(inputs):
    inputs["run"]["execution_logs"].append({"type": "STARTED", "timestamp": "PRIVATE_INVALID_TIME"})
    inputs["run"]["execution_logs"].append({"type": "PRIVATE_EVENT_TYPE", "message": "PRIVATE"})
    inputs["run"]["metrics"]["amount_mean"] = float("inf")
    inputs["run"]["metrics"]["statistics"]["ks"]["pvalue"] = float("nan")
    context = llm_rca.build_context(**inputs)
    assert "PRIVATE" not in json.dumps(context, allow_nan=False)
    assert "amount_mean" not in context["run"]["metrics"]
    assert "pvalue" not in context["run"]["metrics"]["statistics"]["ks"]
    assert context["run"]["execution_logs"][-1] == {"type": "STARTED"}


def test_thresholds_are_snapshot_only_not_current_settings(inputs, monkeypatch):
    monkeypatch.setitem(llm_rca.settings.CHECK_LIMITS, "payment_validity", 0.999)
    assert llm_rca.build_context(**inputs)["run"]["metrics"]["quality_thresholds"]["payment_validity"] == 0.97
    del inputs["run"]["metrics"]["quality_thresholds"]
    assert "quality_thresholds" not in llm_rca.build_context(**inputs)["run"]["metrics"]


def test_secret_in_allowlisted_node_or_output_is_blocked(monkeypatch, context, rule_reason, provider, output):
    enable(monkeypatch)
    monkeypatch.setenv("SOURCE_API_KEY", "sensitive_node")
    context["lineage"]["nodes"].append({"id": "sensitive_node"})
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_CONTEXT")
    provider.assert_not_called()
    context["lineage"]["nodes"].pop()
    output["conclusion"] = "Response echoed sk-PRIVATE_PROVIDER_KEY"
    mock_response(provider, output)
    assert_fallback(llm_rca.analyze_incident(context, rule_reason), rule_reason, "INVALID_RESPONSE")

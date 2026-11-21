"""Task 3 checks use only the existing isolated_db fixture, never the demo DB."""

import json
import math
from types import SimpleNamespace

import pytest
from scipy.stats import ks_2samp
from sqlalchemy import func, select

from observability import database, settings
from observability.statistics import ks_for_run, statistics_for_run, zscore_metrics


pytestmark = pytest.mark.usefixtures("isolated_db")


@pytest.fixture
def session(isolated_db):
    with database.SessionLocal() as value:
        yield value
        value.rollback()


@pytest.fixture
def make_run(session):
    def create(amounts=None, *, metrics=None, dataset="fact_orders", status="HEALTHY",
               run_id=None, started_at=None):
        record = database.PipelineRun(
            dataset=dataset, scenario="normal", status=status,
            started_at=started_at or database.now_utc(), finished_at=database.now_utc(),
            row_count=len(amounts) if amounts is not None else 400,
            quality_score=100, ml_anomaly=False, ml_anomaly_score=0,
            metrics_json=json.dumps(metrics if metrics is not None else {
                "row_count": 400, "amount_mean": 250,
            }),
            schema_json='["amount"]', sources_json='{}',
            # Deliberately unrelated: KS must never read the twelve-row preview.
            preview_json='[{"amount": -999999}]', duration_ms=1,
        )
        if run_id is not None:
            record.id = run_id
        session.add(record)
        session.flush()
        if amounts is not None:
            session.add_all(database.RawOrder(
                run_id=record.id, row_number=index, amount=amount,
                payload_json=json.dumps({"amount": amount if amount is not None
                                         and math.isfinite(amount) else None}),
            ) for index, amount in enumerate(amounts))
            session.flush()
        return record
    return create


def history(values=(80, 90, 100, 110, 120)):
    return [SimpleNamespace(id=index, dataset="fact_orders", status="HEALTHY",
                            metrics_json=json.dumps({"row_count": value, "amount_mean": value}))
            for index, value in enumerate(values, 1)]


def assert_json_safe(value):
    # Reject non-standard JSON NaN/Infinity anywhere in the evidence tree.
    assert json.loads(json.dumps(value, allow_nan=False)) == value


def test_zscore_contract_population_std_and_anomalous_aggregate(monkeypatch):
    monkeypatch.delattr(settings, "STATISTICAL_LIMITS", raising=False)
    result = zscore_metrics({"row_count": 200, "amount_mean": 100}, history())
    assert set(result) == {"status", "anomalous", "threshold", "min_history", "metrics"}
    assert result["status"] == "ALERT" and result["anomalous"] is True
    assert result["threshold"] == 3.0 and result["min_history"] == 5
    count = result["metrics"]["row_count"]
    assert set(count) == {"status", "anomalous", "reason", "value", "history_count",
                          "baseline_mean", "baseline_std", "zscore"}
    assert count["baseline_mean"] == 100
    assert count["baseline_std"] == pytest.approx(math.sqrt(200))
    assert count["zscore"] == pytest.approx(100 / math.sqrt(200))
    assert result["metrics"]["amount_mean"]["zscore"] == 0
    assert_json_safe(result)


@pytest.mark.parametrize("count", [0, 1, 4])
def test_zscore_minimum_history(count):
    result = zscore_metrics({"row_count": 1000, "amount_mean": 1000}, history()[:count])
    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert result["anomalous"] is False
    for metric in result["metrics"].values():
        assert metric["history_count"] == count
        assert metric["zscore"] is None
        assert metric["baseline_std"] is None
    assert_json_safe(result)


@pytest.mark.parametrize("current,anomalous", [(100, False), (100.0000000001, True), (99, True)])
def test_zscore_exact_zero_variance_without_epsilon(current, anomalous):
    result = zscore_metrics({"row_count": current, "amount_mean": current}, history([100] * 5))
    assert result["anomalous"] is anomalous
    for metric in result["metrics"].values():
        assert metric["baseline_std"] == 0
        assert metric["reason"] == "zero_variance"
        assert metric["zscore"] is None if anomalous else metric["zscore"] == 0
        assert metric["status"] == ("ALERT" if anomalous else "OK")
    assert_json_safe(result)


def test_zscore_invalid_history_is_filtered_per_metric_and_failed_runs_excluded():
    training = history()
    training += [SimpleNamespace(status="FAILED", metrics_json='{"row_count": 1e200, "amount_mean": 1e200}')]
    for payload in ('null', '[]', '{broken', '{"row_count": null, "amount_mean": Infinity}',
                    '{"row_count": NaN, "amount_mean": "bad"}'):
        training.append(SimpleNamespace(status="HEALTHY", metrics_json=payload))
    result = zscore_metrics({"row_count": 100, "amount_mean": 100}, training)
    assert result["status"] == "OK"
    assert all(metric["history_count"] == 5 for metric in result["metrics"].values())
    training[0].metrics_json = '{"row_count": 80}'
    result = zscore_metrics({"row_count": 100, "amount_mean": 100}, training)
    assert result["status"] == "PARTIAL"
    assert result["metrics"]["amount_mean"]["status"] == "INSUFFICIENT_HISTORY"
    assert_json_safe(result)


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), -float("inf"), "bad", True])
def test_invalid_current_metric_does_not_hide_other_alert(value):
    result = zscore_metrics({"row_count": 1000, "amount_mean": value}, history())
    assert result["anomalous"] is True and result["status"] == "ALERT"
    assert result["metrics"]["amount_mean"]["status"] == "INVALID_VALUE"
    assert result["metrics"]["amount_mean"]["value"] is None
    assert_json_safe(result)


def test_zscore_missing_current_metrics():
    result = zscore_metrics({}, history())
    assert result["status"] == "INVALID_VALUE"
    assert result["anomalous"] is False
    assert_json_safe(result)


@pytest.mark.parametrize("values,current", [
    ([-1e308, 1e308, -1e308, 1e308, 0], 1e308),
    ([-1e-300, 1e-300, -1e-300, 1e-300, 0], 1e308),
    ([-1e308] * 5, 1e308),
])
def test_zscore_finite_extremes_cannot_emit_infinity(values, current):
    result = zscore_metrics({"row_count": current, "amount_mean": current}, history(values))
    assert_json_safe(result)
    if values[0] == -1e-300:
        assert result["anomalous"] is True
        assert result["metrics"]["row_count"]["zscore"] is None
        assert result["metrics"]["row_count"]["reason"] == "score_overflow"


def test_zscore_reads_settings_at_call_time_and_threshold_boundary(monkeypatch):
    monkeypatch.setattr(settings, "STATISTICAL_LIMITS", {
        "zscore_threshold": 2, "min_history": 2,
    }, raising=False)
    training = history([0, 2])
    result = zscore_metrics({"row_count": 3, "amount_mean": -1}, training)
    assert result["status"] == "OK"  # exactly +/-2 is not greater than 2
    monkeypatch.setitem(settings.STATISTICAL_LIMITS, "zscore_threshold", 1.9)
    result = zscore_metrics({"row_count": 3, "amount_mean": -1}, training)
    assert result["anomalous"] is True
    assert result["min_history"] == 2


@pytest.mark.parametrize("count,shift,significant,effect,anomalous", [
    (40, 0, False, False, False),
    (40, 100, True, True, True),
    (20, 5, False, True, False),
    (2000, 100, True, False, False),
])
def test_ks_distance_is_separate_from_significance(
        session, make_run, count, shift, significant, effect, anomalous):
    previous = make_run(list(range(count)))
    amounts = [amount + shift for amount in range(count)]
    current = make_run(amounts)
    result = ks_for_run(session, current.id)
    expected = ks_2samp(amounts, list(range(count)))
    assert result["ks_distance"] == pytest.approx(float(expected.statistic))
    assert result["pvalue"] == pytest.approx(float(expected.pvalue))
    assert result["significant"] is significant
    assert result["effect_detected"] is effect
    assert result["anomalous"] is anomalous
    assert result["status"] == ("ALERT" if anomalous else "OK")
    assert result["previous_run_id"] == previous.id
    assert result["current_samples"] == result["previous_samples"] == count
    assert_json_safe(result)


def test_ks_uses_previous_id_same_dataset_even_if_failed_not_timestamp(session, make_run):
    make_run([999.] * 40, run_id=10)
    previous = make_run([1.] * 40, run_id=30, status="FAILED")
    make_run([999.] * 40, run_id=40, dataset="other_dataset")
    current = make_run([1.] * 40, run_id=50)
    # A later run has an older timestamp. Neither it nor the latest healthy run
    # nor another dataset may replace the immediately preceding same-dataset ID.
    make_run([999.] * 40, run_id=60, started_at=previous.started_at)
    current.started_at = previous.started_at
    session.flush()
    result = ks_for_run(session, current.id)
    assert result["previous_run_id"] == previous.id
    assert result["ks_distance"] == 0 and result["pvalue"] == 1


@pytest.mark.parametrize("current_missing,previous_missing", [(True, False), (False, True), (True, True)])
def test_ks_legacy_snapshot_is_not_replaced_by_older_run(
        session, make_run, current_missing, previous_missing):
    make_run([1.] * 40)
    previous = make_run(None if previous_missing else [1.] * 40)
    current = make_run(None if current_missing else [1.] * 40)
    result = ks_for_run(session, current.id)
    assert result["status"] == "SNAPSHOT_UNAVAILABLE"
    assert result["previous_run_id"] == previous.id
    assert result["anomalous"] is False
    assert result["pvalue"] is result["ks_distance"] is None
    assert_json_safe(result)


@pytest.mark.parametrize("count", [0, 19, 20])
def test_ks_excludes_null_nonfinite_and_obeys_minimum(session, make_run, count):
    invalid = [None, float("nan"), float("inf"), -float("inf")]
    previous = make_run([1.] * count + invalid)
    current = make_run([1.] * 20 + invalid)
    result = ks_for_run(session, current.id)
    assert result["current_samples"] == 20
    assert result["previous_samples"] == count
    assert result["previous_run_id"] == previous.id
    assert result["status"] == ("OK" if count == 20 else "INSUFFICIENT_SAMPLES")
    assert_json_safe(result)


def test_ks_missing_run_and_first_run_contract(session, make_run):
    missing = ks_for_run(session, 123)
    assert set(missing) == {
        "status", "anomalous", "reason", "run_id", "previous_run_id", "current_samples",
        "previous_samples", "ks_distance", "pvalue", "significant", "effect_detected",
        "alpha", "min_samples", "effect_threshold",
    }
    assert missing["status"] == "RUN_NOT_FOUND"
    assert missing["previous_run_id"] is None
    assert missing["significant"] is missing["effect_detected"] is None
    first = make_run([1.] * 40)
    result = ks_for_run(session, first.id)
    assert result["status"] == "NO_PREVIOUS_RUN"
    assert result["previous_run_id"] is None
    assert_json_safe(result)


def test_statistics_wrapper_settings_history_exclusion_and_transaction(
        session, make_run, monkeypatch):
    monkeypatch.setattr(settings, "STATISTICAL_LIMITS", {
        "zscore_threshold": 1.5, "min_history": 2,
        "ks_alpha": .01, "ks_min_samples": 3, "ks_effect_threshold": .25,
    }, raising=False)
    older = make_run([1.] * 3, metrics={"row_count": 10, "amount_mean": 10})
    previous = make_run([1.] * 3, metrics={"row_count": 20, "amount_mean": 20})
    wrong_dataset = make_run([1.] * 3, dataset="other_dataset")
    failed = make_run([100.] * 3, status="FAILED")
    current = make_run([1.] * 3, metrics={"row_count": 100, "amount_mean": 100,
                                                       "statistics": {"old": "ignored"}})
    future = make_run([100.] * 3)
    before = current.metrics_json
    result = statistics_for_run(session, current, [older, previous, wrong_dataset, failed, current, future])
    assert set(result) == {"zscore", "ks"}
    assert result["zscore"]["anomalous"] is True
    assert result["zscore"]["threshold"] == 1.5
    assert result["zscore"]["min_history"] == 2
    assert result["zscore"]["metrics"]["row_count"]["history_count"] == 2
    assert result["ks"]["previous_run_id"] == failed.id
    assert result["ks"]["alpha"] == .01
    assert result["ks"]["min_samples"] == 3
    assert result["ks"]["effect_threshold"] == .25
    assert result["ks"]["effect_detected"] is True
    assert current.metrics_json == before and current.status == "HEALTHY"
    assert session.scalar(select(func.count()).select_from(database.RawOrder)) == 18
    assert statistics_for_run(session, current, [older, previous]) == result
    assert_json_safe(result)
    session.rollback()
    assert session.scalar(select(func.count()).select_from(database.PipelineRun)) == 0


def test_ks_diagnostic_alert_does_not_change_zscore_health(session, make_run):
    training = [make_run([1.] * 40) for _ in range(5)]
    current = make_run([100.] * 40)
    result = statistics_for_run(session, current, training)
    assert result["zscore"]["anomalous"] is False
    assert result["ks"]["anomalous"] is True
    assert current.status == "HEALTHY"


@pytest.mark.parametrize("kwargs", [
    {"alpha": float("nan")}, {"alpha": 0}, {"alpha": 1},
    {"min_samples": 0}, {"min_samples": 2.5},
    {"effect_threshold": float("inf")}, {"effect_threshold": -1},
])
def test_ks_rejects_invalid_threshold_configuration(session, kwargs):
    with pytest.raises(ValueError):
        ks_for_run(session, 999, **kwargs)


@pytest.mark.parametrize("limits", [
    {"zscore_threshold": float("inf")}, {"zscore_threshold": 0},
    {"min_history": 1}, {"min_history": 2.5},
])
def test_zscore_rejects_invalid_threshold_configuration(monkeypatch, limits):
    monkeypatch.setattr(settings, "STATISTICAL_LIMITS", limits, raising=False)
    with pytest.raises(ValueError):
        zscore_metrics({"row_count": 100, "amount_mean": 100}, history())

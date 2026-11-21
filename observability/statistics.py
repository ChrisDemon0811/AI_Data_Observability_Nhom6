"""JSON-safe statistical evidence; callers decide whether to fail a pipeline.

``statistics_for_run`` returns ``{"zscore": zscore_metrics(...), "ks":
ks_for_run(...)}``. Only ``result["zscore"]["anomalous"]`` is intended to
affect run health. KS compares adjacent runs, including failed runs, and is
diagnostic: recovery can differ from the preceding failed distribution.

Settings are read at call time from ``settings.STATISTICAL_LIMITS`` with
defaults: zscore_threshold=3, min_history=5, ks_alpha=.05,
ks_min_samples=20, ks_effect_threshold=.1. No writes or commits are made.
"""

import json
import math
from collections.abc import Mapping
from statistics import mean, pstdev

from scipy.stats import ks_2samp
from sqlalchemy import select

from . import settings
from .database import PipelineRun, RawOrder


def _finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _metrics(record):
    try:
        value = json.loads(record.metrics_json)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _limits():
    return getattr(settings, "STATISTICAL_LIMITS", {}) or {}


def _positive_integer(value, name, minimum):
    number = _finite(value)
    if number is None or number < minimum or not number.is_integer():
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return int(number)


def zscore_metrics(metrics, healthy_runs):
    """Return healthy-history evidence for row_count and amount_mean.

    The exact return keys are status, anomalous, threshold, min_history,
    metrics. Each metric has status, anomalous, reason, value, history_count,
    baseline_mean, baseline_std, zscore. Status is OK, ALERT,
    INSUFFICIENT_HISTORY or INVALID_VALUE; aggregate status can also be PARTIAL.
    An ALERT takes precedence over incomplete evidence in the aggregate.

    Only HEALTHY ORM runs with valid metrics contribute, independently per
    metric. The wrapper additionally excludes current/future/other-dataset runs.
    Standard deviation is population-based (ddof=0); abs(zscore) > threshold
    alerts. EXACT zero variance: equality yields zscore=0 and OK; any change
    yields zscore=None and ALERT, reason='zero_variance'. There is no epsilon
    or artificial scale. Overflowing scores are also None, never infinity.
    Skipped metrics have anomalous=False and unavailable numbers are None.
    """
    limits = _limits()
    threshold = _finite(limits.get("zscore_threshold", 3.0))
    if threshold is None or threshold <= 0:
        raise ValueError("zscore_threshold must be finite and > 0")
    minimum = _positive_integer(limits.get("min_history", 5), "min_history", 2)
    history = [_metrics(run) for run in healthy_runs if run.status == "HEALTHY"]
    results = {}
    for name in ("row_count", "amount_mean"):
        values = [value for item in history if (value := _finite(item.get(name))) is not None]
        value = _finite(metrics.get(name))
        result = {
            "status": "INSUFFICIENT_HISTORY", "anomalous": False,
            "reason": "insufficient_history", "value": value,
            "history_count": len(values), "baseline_mean": None,
            "baseline_std": None, "zscore": None,
        }
        results[name] = result
        if value is None:
            result.update(status="INVALID_VALUE", reason="invalid_value")
            continue
        if len(values) < minimum:
            continue
        center, spread = mean(values), pstdev(values)
        result.update(baseline_mean=center, baseline_std=spread, reason=None)
        if spread == 0:
            anomalous = value != center
            result.update(zscore=None if anomalous else 0.0, reason="zero_variance")
        else:
            difference = value - center
            if math.isfinite(difference):
                score = _finite(difference / spread)
            else:
                # Normalize only on subtraction overflow, preserving ordinary
                # arithmetic (and exact threshold boundaries) otherwise.
                scale = max(abs(value), abs(center), spread)
                difference = value / scale - center / scale
                scaled_std = spread / scale
                score = _finite(difference / scaled_std) if scaled_std else None
            anomalous = score is None or abs(score) > threshold
            result.update(zscore=score, reason="score_overflow" if score is None else None)
        result.update(status="ALERT" if anomalous else "OK", anomalous=anomalous)
    statuses = {result["status"] for result in results.values()}
    status = "ALERT" if "ALERT" in statuses else next(iter(statuses)) if len(statuses) == 1 else "PARTIAL"
    return {
        "status": status, "anomalous": "ALERT" in statuses,
        "threshold": threshold, "min_history": minimum, "metrics": results,
    }


def ks_for_run(session, run_id, alpha=.05, min_samples=20, effect_threshold=.1):
    """Compare finite stored RawOrder.amount values with the adjacent run by ID.

    The previous run is max(id) < run_id in the same dataset, regardless of
    health, timestamps or snapshot availability. Never skip a legacy run to
    select an older snapshot. A missing snapshot means zero stored order rows;
    rows whose amounts are all invalid instead mean insufficient samples.

    Return keys: status, anomalous, reason, run_id, previous_run_id,
    current_samples, previous_samples, ks_distance, pvalue, significant,
    effect_detected, alpha, min_samples, effect_threshold. Distances/pvalues
    and both flags are None when skipped. KS distance is the effect size,
    separate from significance (pvalue < alpha). ALERT requires BOTH
    significance and ks_distance >= effect_threshold. Statuses are OK, ALERT,
    RUN_NOT_FOUND, NO_PREVIOUS_RUN, SNAPSHOT_UNAVAILABLE, INSUFFICIENT_SAMPLES.
    Defaults here are explicit arguments; the wrapper supplies settings values.
    """
    alpha, effect_threshold = _finite(alpha), _finite(effect_threshold)
    if alpha is None or not 0 < alpha < 1:
        raise ValueError("alpha must be finite and between 0 and 1")
    if effect_threshold is None or not 0 <= effect_threshold <= 1:
        raise ValueError("effect_threshold must be finite and between 0 and 1 inclusive")
    min_samples = _positive_integer(min_samples, "min_samples", 1)
    result = {
        "status": "RUN_NOT_FOUND", "anomalous": False, "reason": "run_not_found",
        "run_id": run_id, "previous_run_id": None,
        "current_samples": 0, "previous_samples": 0,
        "ks_distance": None, "pvalue": None,
        "significant": None, "effect_detected": None,
        "alpha": alpha, "min_samples": min_samples, "effect_threshold": effect_threshold,
    }
    current = session.get(PipelineRun, run_id)
    if current is None:
        return result
    previous_id = session.scalar(select(PipelineRun.id).where(
        PipelineRun.id < run_id, PipelineRun.dataset == current.dataset,
    ).order_by(PipelineRun.id.desc()).limit(1))
    result["previous_run_id"] = previous_id
    if previous_id is None:
        result.update(status="NO_PREVIOUS_RUN", reason="no_previous_run")
        return result
    samples = {run_id: [], previous_id: []}
    counts = {run_id: 0, previous_id: 0}
    for snapshot_run_id, amount in session.execute(select(RawOrder.run_id, RawOrder.amount).where(
        RawOrder.run_id.in_((previous_id, run_id)),
    )):
        counts[snapshot_run_id] += 1
        if (value := _finite(amount)) is not None:
            samples[snapshot_run_id].append(value)
    result.update(current_samples=len(samples[run_id]), previous_samples=len(samples[previous_id]))
    if not counts[run_id] or not counts[previous_id]:
        result.update(status="SNAPSHOT_UNAVAILABLE", reason="snapshot_unavailable")
        return result
    if min(len(samples[run_id]), len(samples[previous_id])) < min_samples:
        result.update(status="INSUFFICIENT_SAMPLES", reason="insufficient_samples")
        return result
    test = ks_2samp(samples[run_id], samples[previous_id], alternative="two-sided", method="auto")
    distance, pvalue = float(test.statistic), float(test.pvalue)
    significant, effect_detected = pvalue < alpha, distance >= effect_threshold
    anomalous = significant and effect_detected
    result.update(
        status="ALERT" if anomalous else "OK", anomalous=anomalous, reason=None,
        ks_distance=distance, pvalue=pvalue,
        significant=significant, effect_detected=effect_detected,
    )
    return result


def statistics_for_run(session, record, healthy_runs):
    """Return {zscore: aggregate evidence, ks: adjacent-run diagnostics}.

    Call after record and its raw snapshots have been flushed. The session
    transaction remains owned by the caller. Existing metrics['statistics']
    is ignored, so repeated calls cannot nest evidence or train on it.
    """
    limits = _limits()
    history = [run for run in healthy_runs if run.id is not None and run.id < record.id
               and run.dataset == record.dataset and run.status == "HEALTHY"]
    return {
        "zscore": zscore_metrics(_metrics(record), history),
        "ks": ks_for_run(
            session, record.id, alpha=limits.get("ks_alpha", .05),
            min_samples=limits.get("ks_min_samples", 20),
            effect_threshold=limits.get("ks_effect_threshold", .1),
        ),
    }

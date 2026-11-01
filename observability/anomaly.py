"""Unsupervised model for unusual numeric patterns, fitted on healthy history."""
import json

import numpy as np
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

FEATURES = [
    "row_count", "freshness_minutes", "completeness", "uniqueness", "validity",
    "amount_mean", "payment_card", "payment_bank_transfer", "payment_wallet",
]


def ml_score(current_metrics, healthy_history):
    """Returns (anomaly, 0..100 novelty score, explainability metadata).

    Only healthy runs train the model. Model decisions do not replace DQ checks.
    A conservative novelty margin helps avoid false alarms in a tiny demo dataset.
    """
    if len(healthy_history) < 7:
        return False, 0.0, {"model": "IsolationForest", "trained_samples": len(healthy_history), "message": "Cần ít nhất 7 lần chạy khỏe mạnh để học baseline."}

    samples = [json.loads(row.metrics_json) for row in healthy_history[-100:]]
    train = np.asarray([[s[k] for k in FEATURES] for s in samples], dtype=float)
    test = np.asarray([[current_metrics[k] for k in FEATURES]], dtype=float)
    scaler = RobustScaler(quantile_range=(10, 90)).fit(train)
    x = scaler.transform(train)
    y = scaler.transform(test)
    # Contain extreme values so single feature doesn't dominate distance.
    x, y = np.clip(x, -30, 30), np.clip(y, -30, 30)
    model = IsolationForest(n_estimators=110, max_samples="auto", contamination="auto", random_state=41).fit(x)
    train_values = model.decision_function(x)
    test_value = float(model.decision_function(y)[0])
    novelty_threshold = float(np.min(train_values) - 0.025)
    # A robust baseline distance complements IsolationForest when just 7-20
    # healthy samples are available. Units get feature-specific minimum scales.
    medians = np.median(train, axis=0)
    robust_ranges = np.percentile(train, 90, axis=0) - np.percentile(train, 10, axis=0)
    min_scales = np.asarray([80, 5, .02, .02, .02, 18, .05, .05, .05])
    scale = np.maximum(robust_ranges, min_scales)
    deviations = np.abs((test[0] - medians) / scale)
    max_deviation = float(np.max(deviations))
    anomaly_by_iforest = test_value < novelty_threshold
    anomaly_by_robust = max_deviation > 3.5
    anomalous = anomaly_by_iforest or anomaly_by_robust
    novelty = max(0.0, (max_deviation - 2.5) * 17, (novelty_threshold - test_value) * 400)
    score = float(round(min(100.0, novelty), 1)) if anomalous else 0.0
    ranked = np.argsort(deviations)[::-1][:3]
    evidence = [{"feature": FEATURES[int(i)], "deviation": round(float(deviations[i]), 2)} for i in ranked]
    return bool(anomalous), score, {
        "model": "IsolationForest + robust baseline distance", "trained_samples": len(samples),
        "test_decision": round(test_value, 4), "threshold": round(novelty_threshold, 4),
        "robust_max_deviation": round(max_deviation, 2), "robust_threshold": 3.5,
        "most_changed_features": evidence,
        "message": "Novelty đa biến dựa trên IsolationForest kết hợp khoảng cách robust so với baseline khỏe mạnh.",
    }

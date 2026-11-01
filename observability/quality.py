"""Transparent, deterministic DQ checks and drift detectors."""
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from .generator import EXPECTED_COLUMNS
from .settings import CHECK_LIMITS

PAYMENT_VALUES = ["card", "bank_transfer", "wallet"]


@dataclass
class CheckResult:
    code: str
    title: str
    passed: bool
    severity: str
    observed: float
    expected: str
    details: str

    def as_dict(self):
        return vars(self).copy()


def js_divergence(a, b):
    """Jensen-Shannon divergence (base 2), value between 0 and 1."""
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    if x.sum() == 0 or y.sum() == 0:
        return 0.0
    x, y = x / x.sum(), y / y.sum()
    m = (x + y) / 2.0

    def kl(p, q):
        mask = p > 0
        return float(np.sum(p[mask] * np.log2(p[mask] / q[mask])))
    return (kl(x, m) + kl(y, m)) / 2.0


def baseline_from_runs(healthy_runs):
    # Existing healthy runs only; never train baseline using known failures.
    if not healthy_runs:
        return {"count": 400.0, "amount_mean": 252.0, "payment": {"card": .5, "bank_transfer": .3, "wallet": .2}}
    fields = [r.metrics_json for r in healthy_runs]
    import json
    items = [json.loads(s) for s in fields]
    payment = {k: float(np.mean([m.get(f"payment_{k}", 0) for m in items])) for k in PAYMENT_VALUES}
    return {
        "count": float(np.median([m["row_count"] for m in items])),
        "amount_mean": float(np.median([m["amount_mean"] for m in items])),
        "payment": payment,
    }


def evaluate_quality(batch, baseline):
    df = batch.frame
    n = len(df)
    has = set(df.columns)
    schema_missing = [x for x in EXPECTED_COLUMNS if x not in has]
    schema_added = sorted(has - set(EXPECTED_COLUMNS))
    required = [x for x in ["order_id", "customer_id", "amount", "payment_method"] if x in has]
    completeness = 1.0 if not required else float(df[required].notna().values.mean())
    uniqueness = float(1 - df["order_id"].duplicated().sum() / max(1, n)) if "order_id" in has else 0.0
    validity = float(df["payment_method"].isin(PAYMENT_VALUES).mean()) if "payment_method" in has else 0.0
    schema_match = float(len(schema_missing) == 0 and len(schema_added) == 0)
    mean_amount = float(df["amount"].mean()) if "amount" in has else 0.0
    share = {x: float((df["payment_method"] == x).mean()) if "payment_method" in has else 0.0 for x in PAYMENT_VALUES}
    js = float(js_divergence([share[x] for x in PAYMENT_VALUES], [baseline["payment"][x] for x in PAYMENT_VALUES]))
    mean_ratio = mean_amount / max(baseline["amount_mean"], 0.01)
    vol_ratio = n / max(baseline["count"], 1)
    lag = batch.source_freshness_minutes

    checks = [
        CheckResult("freshness", "Tính mới của dữ liệu (Freshness)", lag <= CHECK_LIMITS["freshness_minutes"], "HIGH", round(lag, 3), "≤ 60 phút", "Độ trễ sự kiện mới nhất so với lúc chạy pipeline."),
        CheckResult("completeness", "Tính đầy đủ (Completeness)", completeness >= CHECK_LIMITS["completeness"], "HIGH", round(completeness, 4), "≥ 98%", "Tỷ lệ ô không NULL trong các trường quan trọng."),
        CheckResult("uniqueness", "Tính duy nhất (Uniqueness)", uniqueness >= CHECK_LIMITS["uniqueness"], "HIGH", round(uniqueness, 4), "≥ 99,5%", "Tỷ lệ order_id không trùng lặp."),
        CheckResult("schema", "Tương thích schema", schema_match == 1.0, "CRITICAL", schema_match, "Đủ các cột chuẩn", f"Cột bị thiếu: {schema_missing or 'không'}; cột mới: {schema_added or 'không'}."),
        CheckResult("validity", "Tính hợp lệ của phương thức thanh toán", validity >= CHECK_LIMITS["payment_validity"], "MEDIUM", round(validity, 4), "≥ 99%", "Kiểm tra payment_method thuộc tập giá trị chấp nhận."),
        CheckResult("distribution_drift", "Trôi phân phối (JS divergence)", js <= CHECK_LIMITS["distribution_js"], "MEDIUM", round(js, 4), "≤ 0,12", "So sánh phân phối phương thức thanh toán với baseline khỏe mạnh."),
        CheckResult("amount_drift", "Biến động giá trị đơn trung bình", (1/CHECK_LIMITS["amount_mean_ratio"]) <= mean_ratio <= CHECK_LIMITS["amount_mean_ratio"], "MEDIUM", round(mean_ratio, 4), "0,667–1,5 × baseline", "Tỷ số giá trị đơn hàng trung bình so với baseline."),
        CheckResult("volume", "Bất thường số bản ghi", CHECK_LIMITS["volume_ratio_low"] <= vol_ratio <= CHECK_LIMITS["volume_ratio_high"], "MEDIUM", round(vol_ratio, 4), "0,6–1,5 × baseline", "Kiểm tra tăng hoặc giảm đột biến khối lượng dữ liệu."),
    ]
    metrics = {
        "row_count": n, "freshness_minutes": round(lag, 2),
        "completeness": round(completeness, 4), "uniqueness": round(uniqueness, 4),
        "schema_match": schema_match, "validity": round(validity, 4),
        "distribution_js": round(js, 4), "amount_mean": round(mean_amount, 2),
        "amount_mean_ratio": round(mean_ratio, 4), "volume_ratio": round(vol_ratio, 4),
        **{f"payment_{key}": round(share[key], 4) for key in PAYMENT_VALUES},
    }
    score = round(100 * sum(x.passed for x in checks) / len(checks), 1)
    return checks, metrics, score

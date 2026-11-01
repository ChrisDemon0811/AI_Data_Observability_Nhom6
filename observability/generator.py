"""Synthetic e-commerce data and upstream telemetry. No production/customer data."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

SCENARIOS = {
    "normal": "Bình thường — không phát sinh lỗi",
    "late_data": "Dữ liệu đến trễ — vi phạm freshness/SLA",
    "null_values": "Thiếu dữ liệu — NULL ở cột quan trọng",
    "duplicate_keys": "Trùng khóa — duplicate order_id",
    "schema_change": "Thay đổi schema — mất cột customer_id",
    "distribution_drift": "Drift — phân phối thanh toán/giá trị đơn thay đổi",
    "upstream_failure": "Lỗi upstream — nguồn thanh toán bị gián đoạn",
    "volume_spike": "Bất thường khối lượng — đơn hàng tăng đột biến",
}

EXPECTED_COLUMNS = ["order_id", "customer_id", "amount", "payment_method", "order_status", "event_time"]


@dataclass
class GeneratedBatch:
    frame: pd.DataFrame
    upstream: dict
    source_freshness_minutes: float


def generate_batch(scenario="normal", seed=123, reference_time=None):
    if scenario not in SCENARIOS:
        raise ValueError(f"Kịch bản không hợp lệ: {scenario}")
    rng = np.random.default_rng(seed)
    at = reference_time or datetime.now(timezone.utc)
    n = 1200 if scenario == "volume_spike" else 400
    ages = rng.integers(3, 14, size=n)
    if scenario == "late_data":
        ages = rng.integers(180, 240, size=n)
    timestamps = [(at - timedelta(minutes=int(m))).isoformat() for m in ages]
    values = rng.lognormal(mean=5.5, sigma=0.25, size=n).round(2)
    pay = rng.choice(["card", "bank_transfer", "wallet"], size=n, p=[0.5, 0.3, 0.2])
    status = rng.choice(["completed", "pending", "cancelled"], size=n, p=[0.90, 0.08, 0.02])
    df = pd.DataFrame({
        "order_id": [f"ORD-{seed}-{i:05d}" for i in range(n)],
        "customer_id": rng.integers(1000, 9000, n).astype(object),
        "amount": values,
        "payment_method": pay,
        "order_status": status,
        "event_time": timestamps,
    })
    upstream = {
        "raw_orders": {"status": "OK", "delay_minutes": int(max(ages.min(), 0)), "error_count": 0, "rows": n},
        "raw_customers": {"status": "OK", "delay_minutes": 5, "error_count": 0, "rows": 8000},
        "raw_payments": {"status": "OK", "delay_minutes": 6, "error_count": 0, "rows": n},
    }
    if scenario == "null_values":
        df.loc[rng.choice(n, size=int(0.28*n), replace=False), "amount"] = np.nan
        df.loc[rng.choice(n, size=int(0.12*n), replace=False), "customer_id"] = None
        upstream["raw_orders"]["error_count"] = 3
    elif scenario == "duplicate_keys":
        duplicate = df.sample(n=65, random_state=seed)
        df = pd.concat([df, duplicate], ignore_index=True)
        upstream["raw_orders"]["rows"] = len(df)
    elif scenario == "schema_change":
        df = df.rename(columns={"customer_id": "customer_key"})
        upstream["raw_customers"]["status"] = "SCHEMA_CHANGED"
        upstream["raw_customers"]["error_count"] = 1
    elif scenario == "distribution_drift":
        df["amount"] = (df["amount"] * 3.0).round(2)
        df["payment_method"] = rng.choice(
            ["card", "bank_transfer", "wallet"], size=n, p=[0.04, 0.92, 0.04]
        )
    elif scenario == "upstream_failure":
        upstream["raw_payments"].update({"status": "ERROR", "delay_minutes": 150, "error_count": 46, "rows": 140})
        df.loc[rng.choice(n, size=int(0.48*n), replace=False), "amount"] = np.nan
        df.loc[rng.choice(n, size=int(0.30*n), replace=False), "payment_method"] = None
    return GeneratedBatch(df, upstream, float(ages.min()))

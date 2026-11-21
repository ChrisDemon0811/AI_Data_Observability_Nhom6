import os
import math
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./data/observability.db")
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CHECK_LIMITS = {
    "freshness_minutes": 60.0,
    "completeness": 0.98,
    "uniqueness": 0.995,
    "schema_match": 1.0,
    "payment_validity": 0.99,
    "distribution_js": 0.12,
    "amount_mean_ratio": 1.5,
    "volume_ratio_low": 0.6,
    "volume_ratio_high": 1.5,
}

# One configuration point; the same values drive comparisons and UI labels.
for _key, _value in CHECK_LIMITS.items():
    CHECK_LIMITS[_key] = float(os.getenv(f"DQ_{_key.upper()}", str(_value)))
if not all(math.isfinite(value) for value in CHECK_LIMITS.values()):
    raise ValueError("DQ thresholds must be finite")
for _key in ("completeness", "uniqueness", "schema_match", "payment_validity"):
    if not 0 <= CHECK_LIMITS[_key] <= 1:
        raise ValueError(f"DQ_{_key.upper()} must be between 0 and 1")
if CHECK_LIMITS["freshness_minutes"] < 0 or CHECK_LIMITS["amount_mean_ratio"] < 1:
    raise ValueError("Invalid DQ freshness or amount ratio")
if (CHECK_LIMITS["schema_match"] != 1 or not 0 <= CHECK_LIMITS["distribution_js"] <= 1
        or not 0 < CHECK_LIMITS["volume_ratio_low"] <= CHECK_LIMITS["volume_ratio_high"]):
    raise ValueError("Invalid DQ schema, JS or volume thresholds")

STATISTICAL_LIMITS = {
    "zscore_threshold": float(os.getenv("ZSCORE_THRESHOLD", "3")),
    "min_history": int(os.getenv("ZSCORE_MIN_HISTORY", "5")),
    "ks_alpha": float(os.getenv("KS_ALPHA", "0.05")),
    "ks_min_samples": int(os.getenv("KS_MIN_SAMPLES", "20")),
    "ks_effect_threshold": float(os.getenv("KS_EFFECT_THRESHOLD", "0.1")),
}
if (not all(math.isfinite(value) for value in STATISTICAL_LIMITS.values())
        or STATISTICAL_LIMITS["zscore_threshold"] <= 0 or STATISTICAL_LIMITS["min_history"] < 2
        or not 0 < STATISTICAL_LIMITS["ks_alpha"] < 1
        or STATISTICAL_LIMITS["ks_min_samples"] < 2
        or not 0 <= STATISTICAL_LIMITS["ks_effect_threshold"] <= 1):
    raise ValueError("Invalid statistical thresholds")

import os
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

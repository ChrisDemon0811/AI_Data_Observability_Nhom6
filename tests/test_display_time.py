from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import pytest
import requests
from streamlit.testing.v1 import AppTest

from observability import database, service
from observability.api import app
from observability.display import format_timestamp


@pytest.mark.parametrize("value,expected", [
    ("2026-11-27T12:59:26Z", "27/11/2026 19:59:26 (UTC+7)"),
    ("2026-11-27T20:00:00+00:00", "28/11/2026 03:00:00 (UTC+7)"),
    ("2026-11-27T19:59:26+07:00", "27/11/2026 19:59:26 (UTC+7)"),
    ("2026-11-27T12:59:26", "27/11/2026 19:59:26 (UTC+7)"),
    (datetime(2026, 11, 27, 12, 59, 26), "27/11/2026 19:59:26 (UTC+7)"),
    (datetime(2026, 11, 27, 14, 59, 26, tzinfo=timezone(timedelta(hours=2))), "27/11/2026 19:59:26 (UTC+7)"),
    (None, "—"), ("", "—"), ("invalid", "invalid"),
])
def test_format_timestamp(value, expected):
    assert format_timestamp(value) == expected


def test_dashboard_renders_vietnam_time_without_changing_api_or_db(isolated_db, monkeypatch):
    run = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
    with database.get_session() as session:
        session.get(database.PipelineRun, run["id"]).finished_at = datetime(2026, 11, 27, 12, 59, 26)
    before = service.get_run(run["id"])
    with TestClient(app) as client:
        def request(method, url, **kwargs):
            parsed = urlsplit(url)
            return client.request(method, parsed.path + ("?" + parsed.query if parsed.query else ""), json=kwargs.get("json"))
        monkeypatch.setattr(requests, "request", request)
        view = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard.py")).run(timeout=30)
        assert not list(view.exception), [exc.message for exc in view.exception]
        recent = next(table.value for table in view.tabs[0].dataframe if "Thời điểm" in table.value.columns)
        assert recent.iloc[0]["Thời điểm"] == "27/11/2026 19:59:26 (UTC+7)"
        checks = next(table.value for table in view.tabs[1].dataframe if "Thời điểm" in table.value.columns)
        assert set(checks["Thời điểm"]) == {"27/11/2026 19:59:26 (UTC+7)"}
        incident = service.get_incident(run["incident_id"])
        ai_captions = [item.value for item in view.tabs[4].caption]
        assert "Thời điểm phân tích: " + format_timestamp(incident["root_cause"]["analyzed_at"]) in ai_captions
        deadline = format_timestamp(incident["sla_deadline"])
        incidents = next(table.value for table in view.tabs[3].dataframe if "SLA" in table.value.columns)
        assert incidents.iloc[0]["SLA"] == deadline
        assert client.get(f"/api/runs/{run['id']}").json()["finished_at"] == "2026-11-27T12:59:26Z"
    assert service.get_run(run["id"]) == before

"""Verify quiet polling and manual refresh against isolated FastAPI/SQLite."""
from datetime import timedelta
from functools import wraps
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi.testclient import TestClient
import requests
import streamlit as st
from streamlit.testing.v1 import AppTest

from observability import database, service
from observability.api import app


def test_revision_tracks_runs_incident_actions_and_sla(isolated_db):
    with TestClient(app) as client:
        initial = client.get("/api/dashboard/revision").json()
        assert client.get("/api/dashboard/revision").json() == initial
        run = service.run_pipeline("upstream_failure", seed=23, export_csv=False)
        created = client.get("/api/dashboard/revision").json()
        assert created != initial
        assert created["runs"][0] == 1
        assert "root_cause" not in str(created) and "metrics" not in str(created)
        service.update_incident(run["incident_id"], "acknowledge")
        acknowledged = client.get("/api/dashboard/revision").json()
        assert acknowledged != created
        service.analyze_incident_rca(run["incident_id"])
        assert client.get("/api/dashboard/revision").json() != acknowledged
        with database.get_session() as session:
            session.get(database.Incident, run["incident_id"]).sla_deadline = database.now_utc() - timedelta(minutes=1)
        assert client.get("/api/dashboard/revision").json()["overdue_open"] == 1
        service.update_incident(run["incident_id"], "resolve")
        assert client.get("/api/dashboard/revision").json()["overdue_open"] == 0


def test_manual_default_and_timer_only_reloads_changed_data(isolated_db, monkeypatch):
    service.run_pipeline("normal", seed=23, export_csv=False)
    bridge = SimpleNamespace(tick=False, callback=None)
    monkeypatch.setitem(sys.modules, "dashboard_refresh_test_bridge", bridge)
    real_fragment = st.fragment

    def record_timer(**options):
        def decorate(function):
            fragment = real_fragment(**options)(function)
            @wraps(function)
            def invoke(*args, **kwargs):
                bridge.callback = lambda: function(*args, **kwargs)
                return fragment(*args, **kwargs)
            return invoke
        return decorate

    monkeypatch.setattr(st, "fragment", record_timer)
    calls = []
    failure = {"enabled": False}
    with TestClient(app) as client:
        def request(method, url, **kwargs):
            parsed = urlsplit(url)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            calls.append(path)
            if failure["enabled"] and parsed.path == "/api/dashboard/revision":
                raise requests.Timeout("test poll timeout")
            return client.request(method, path, json=kwargs.get("json"))
        monkeypatch.setattr(requests, "request", request)
        path = str(Path(__file__).resolve().parents[1] / "dashboard.py")
        script = f'''import runpy
import dashboard_refresh_test_bridge as bridge
if bridge.tick:
    bridge.tick = False
    bridge.callback()
else:
    runpy.run_path({path!r})
'''
        view = AppTest.from_string(script).run(timeout=30)
        assert not list(view.exception)
        assert view.checkbox(key="auto_refresh").value is False
        assert "/api/dashboard/revision" not in calls
        calls.clear()
        next(button for button in view.button if button.label == "↻ Làm mới dashboard").click().run(timeout=30)
        assert "/api/summary" in calls
        view.checkbox(key="auto_refresh").check().run(timeout=30)
        assert bridge.callback is not None and not list(view.exception)
        calls.clear()
        bridge.tick = True
        view.run(timeout=30)
        assert calls == ["/api/dashboard/revision"], "Idle tick must not reload/render dashboard data"
        assert not list(view.exception)
        failure["enabled"] = True
        calls.clear()
        bridge.tick = True
        view.run(timeout=30)
        assert calls == ["/api/dashboard/revision"] and not list(view.exception)
        failure["enabled"] = False
        service.run_pipeline("normal", seed=24, export_csv=False)
        calls.clear()
        bridge.tick = True
        view.run(timeout=30)
        assert "/api/summary" in calls and not list(view.exception)
        assert view.metric[0].value == "2"

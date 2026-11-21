"""Real 15-second scheduler demo with temporary DB, CSV, lock and logs."""
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from observability.api import app


def test_real_fifteen_second_schedule_visible_in_api(isolated_db, tmp_path):
    bootstrap = '''
from pathlib import Path
import sys
from threading import Thread
from apscheduler.events import EVENT_JOB_EXECUTED
from observability import settings
settings.PROJECT_ROOT = Path(sys.argv[1])
settings.DATA_DIR = settings.PROJECT_ROOT / "exports"
settings.DATA_DIR.mkdir()
from observability import scheduler
factory = scheduler.create_scheduler
def create(interval):
    worker = factory(interval)
    finished = []
    def completed(event):
        finished.append(event)
        if len(finished) == 2:
            Thread(target=lambda: worker.shutdown(wait=True)).start()
    worker.add_listener(completed, EVENT_JOB_EXECUTED)
    return worker
scheduler.create_scheduler = create
scheduler.main(["--interval", "15"])
'''
    environment = {**os.environ, "PYTHONUTF8": "1", "DATABASE_URL": "sqlite:///" + Path(isolated_db.url.database).as_posix(),
                   "LLM_MODE": "offline", "LLM_ALLOW_EXTERNAL": "false"}
    child = subprocess.run([sys.executable, "-c", bootstrap, str(tmp_path)], env=environment,
                           capture_output=True, text=True, encoding="utf-8", timeout=45)
    assert child.returncode == 0, child.stderr
    log = (tmp_path / "logs" / "scheduler.log").read_text(encoding="utf-8")
    assert log.count("Pipeline finish") == 2
    assert "interval_seconds=15" in log and "Worker stopped" in log
    with TestClient(app) as client:
        runs = client.get("/api/runs").json()
        assert len(runs) == 2
        from datetime import datetime
        interval = (datetime.fromisoformat(runs[0]["started_at"]) - datetime.fromisoformat(runs[1]["started_at"])).total_seconds()
        assert 13 <= interval <= 20, runs
        assert client.get("/api/summary").json()["runs_total"] == 2
        assert client.get(f"/api/runs/{runs[0]['id']}/sources").json()["stored_counts"]["raw_orders"] == 400

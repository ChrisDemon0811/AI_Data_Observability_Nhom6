from datetime import datetime, timedelta, timezone
import json
import logging
import os
from logging.handlers import RotatingFileHandler
import subprocess
import sys
from threading import Event
from unittest.mock import Mock

from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_MAX_INSTANCES
from apscheduler.executors.debug import DebugExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
import pandas as pd
import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from observability import database, scheduler, service
from observability.generator import GeneratedBatch, generate_batch
from observability.logging_config import configure_logging
from observability.quality import baseline_from_runs, evaluate_quality
from observability.sources import SOURCE_MODELS, get_sources
from observability.worker_lock import single_worker


def test_source_tables_and_postgres_ddl(isolated_db):
    assert set(SOURCE_MODELS) <= set(inspect(isolated_db).get_table_names())
    for model in SOURCE_MODELS.values():
        assert model.__table__.c.id.primary_key
        assert not inspect(isolated_db).get_unique_constraints(model.__tablename__)
        assert str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        assert list(model.__table__.c.run_id.foreign_keys)[0].target_fullname == "pipeline_runs.id"


def test_normal_pipeline_snapshots_and_history(isolated_db):
    first = service.run_pipeline("normal", seed=23, export_csv=False)
    second = service.run_pipeline("normal", seed=24, export_csv=False)
    assert first["status"] == "HEALTHY"
    assert first["quality_score"] == 100
    assert first["duration_ms"] > 0
    assert [run["id"] for run in service.list_runs()] == [second["id"], first["id"]]
    stored = get_sources(first["id"], limit=500)
    expected = json.loads(generate_batch("normal", seed=23).frame.to_json(orient="records"))
    # Event time depends on the clock; all business values must be identical.
    assert [{k: v for k, v in row.items() if k != "event_time"} for row in stored["rows"]] == [
        {k: v for k, v in row.items() if k != "event_time"} for row in expected]
    assert stored["stored_counts"]["raw_orders"] == 400
    assert stored["stored_counts"]["raw_payments"] == 400
    assert stored["stored_counts"]["raw_customers"] == len({r["customer_id"] for r in expected})
    assert get_sources(first["id"], limit=2, offset=5)["rows"] == stored["rows"][5:7]
    assert service.get_run(first["id"])["row_count"] == 400


@pytest.mark.parametrize("scenario,code", [
    ("null_values", "completeness"), ("duplicate_keys", "uniqueness"), ("schema_change", "schema"),
    ("upstream_failure", "validity"),
])
def test_faults_survive_source_storage(isolated_db, scenario, code):
    run = service.run_pipeline(scenario, seed=23, export_csv=False)
    data = get_sources(run["id"], limit=500)
    frame = pd.DataFrame(data["rows"], columns=data["order_columns"])
    replay = GeneratedBatch(frame, run["upstream"], run["metrics"]["freshness_minutes"])
    checks, _, _ = evaluate_quality(replay, baseline_from_runs([]))
    assert run["status"] == "FAILED"
    assert code in {c.code for c in checks if not c.passed}
    assert code in {c["code"] for c in service.get_run(run["id"])["checks"] if not c["passed"]}
    if scenario == "duplicate_keys":
        assert len(frame) == 465
        assert frame["order_id"].duplicated().sum() == 65
    if scenario == "schema_change":
        assert "customer_id" not in data["order_columns"]
        assert "customer_key" in data["order_columns"]
        assert all("customer_id" not in row and "customer_key" in row for row in data["rows"])
        assert "customer_id" not in get_sources(run["id"], "raw_customers")["rows"][0]
    if scenario == "null_values":
        assert sum(row["amount"] is None for row in data["rows"]) == 112
        assert sum(row["customer_id"] is None for row in data["rows"]) == 48
        with database.get_session() as session:
            assert session.scalar(select(func.count()).select_from(database.RawOrder)
                                  .where(database.RawOrder.amount.is_(None))) == 112
    if scenario == "upstream_failure":
        assert run["upstream"]["raw_payments"]["rows"] == 140
        assert data["stored_counts"]["raw_payments"] == 400
        assert data["upstream_telemetry_simulated"] is True


def test_additive_upgrade_preserves_legacy_run(isolated_db):
    run = service.run_pipeline("normal", seed=23, export_csv=False)
    before = service.get_run(run["id"])
    # Emulate the old five-table database, strictly on the temporary engine.
    for model in SOURCE_MODELS.values():
        model.__table__.drop(isolated_db)
    database.init_db()
    database.init_db()
    assert service.get_run(run["id"]) == before
    legacy = get_sources(run["id"])
    assert legacy["snapshot_available"] is False
    assert legacy["rows"] == []
    new = service.run_pipeline("normal", seed=24, export_csv=False)
    assert get_sources(new["id"])["stored_counts"]["raw_orders"] == 400


def test_snapshot_failure_rolls_back_transaction(isolated_db, monkeypatch, caplog):
    original = service.load_source_batch

    def fail(session, run_id, batch):
        original(session, run_id, batch)
        session.flush()
        raise RuntimeError("test ingestion failure")

    monkeypatch.setattr(service, "load_source_batch", fail)
    with pytest.raises(RuntimeError, match="test ingestion failure"):
        service.run_pipeline("normal", seed=23, export_csv=False)
    assert "Pipeline exception" in caplog.text
    assert "test ingestion failure" in caplog.text
    with database.get_session() as session:
        for model in (database.PipelineRun, *SOURCE_MODELS.values()):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_explicit_reset_and_non_destructive_seed(isolated_db):
    service.run_pipeline("normal", seed=23, export_csv=False)
    assert service.seed_demo()["created_runs"] == 0
    assert service.seed_demo(reset=True)["created_runs"] == 16
    assert service.summary()["runs_total"] == 16
    with database.get_session() as session:
        orphans = session.scalar(select(func.count()).select_from(database.RawOrder)
                                 .outerjoin(database.PipelineRun)
                                 .where(database.PipelineRun.id.is_(None)))
        assert orphans == 0


def test_source_api_preserves_existing_routes(isolated_db):
    from fastapi.testclient import TestClient
    from observability.api import app

    with TestClient(app) as client:
        run = client.post("/api/runs", json={"scenario": "schema_change"}).json()
        run_id = run["id"]
        for url in ("/api/health", "/api/summary", "/api/lineage", "/api/runs", f"/api/runs/{run_id}"):
            assert client.get(url).status_code == 200
        for source in SOURCE_MODELS:
            response = client.get(f"/api/runs/{run_id}/sources", params={"source": source, "limit": 2})
            assert response.status_code == 200
            assert len(response.json()["rows"]) == 2
        assert client.get(f"/api/runs/{run_id}/sources?source=bad").status_code == 400
        assert client.get("/api/runs/99999/sources").status_code == 404
        assert client.get(f"/api/runs/{run_id}/sources?limit=501").status_code == 422
        assert client.get(f"/api/runs/{run_id}/sources?offset=-1").status_code == 422


def fake_scheduler_clock(monkeypatch, initial):
    class Clock(datetime):
        current = initial

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr("apscheduler.schedulers.base.datetime", Clock)
    return Clock


def test_scheduler_real_dispatch_and_recovers_after_error(monkeypatch, isolated_db):
    t0 = datetime.now(timezone.utc)
    clock = fake_scheduler_clock(monkeypatch, t0)
    real_pipeline = service.run_pipeline
    attempt = 0

    def pipeline(scenario):
        nonlocal attempt
        attempt += 1
        if attempt == 2:
            raise RuntimeError("one failed scheduled attempt")
        return real_pipeline(scenario, seed=23 + attempt, export_csv=False)

    callback = Mock(side_effect=pipeline)
    monkeypatch.setattr(scheduler, "run_pipeline", callback)
    worker = scheduler.create_scheduler(15)
    assert isinstance(worker, BlockingScheduler)
    worker.configure(executors={"default": DebugExecutor()}, timezone=timezone.utc)
    job = worker.get_job("normal_pipeline")
    assert isinstance(job.trigger, IntervalTrigger)
    assert job.trigger.interval == timedelta(seconds=15)
    assert job.max_instances == 1 and job.coalesce
    assert abs((job.next_run_time - t0).total_seconds()) < 1
    job.modify(next_run_time=t0)
    events = []
    worker.add_listener(events.append, EVENT_JOB_EXECUTED)
    # Initialize the real BlockingScheduler; bypass only its blocking wait loop.
    monkeypatch.setattr(worker, "_main_loop", lambda: None)
    worker.start()
    try:
        worker._process_jobs()
        assert callback.call_count == 1
        clock.current = t0 + timedelta(seconds=14)
        worker._process_jobs()
        assert callback.call_count == 1
        clock.current = t0 + timedelta(seconds=15)
        worker._process_jobs()
        assert callback.call_count == 2
        clock.current = t0 + timedelta(seconds=30)
        worker._process_jobs()
        assert callback.call_count == 3
        assert len(events) == 3
        assert events[1].retval is None
        assert len(service.list_runs()) == 2
    finally:
        worker.shutdown()


def test_scheduler_skips_overlap(monkeypatch):
    t0 = datetime.now(timezone.utc)
    clock = fake_scheduler_clock(monkeypatch, t0)
    entered, release = Event(), Event()

    def blocking_pipeline(scenario):
        entered.set()
        assert release.wait(5), "test did not release pipeline"

    callback = Mock(side_effect=blocking_pipeline)
    monkeypatch.setattr(scheduler, "run_pipeline", callback)
    worker = scheduler.create_scheduler(15)
    worker.get_job("normal_pipeline").modify(next_run_time=t0)
    skipped = []
    worker.add_listener(skipped.append, EVENT_JOB_MAX_INSTANCES)
    monkeypatch.setattr(worker, "_main_loop", lambda: None)
    worker.start()
    try:
        worker._process_jobs()
        assert entered.wait(5)
        clock.current = t0 + timedelta(seconds=15)
        worker._process_jobs()
        assert len(skipped) == 1
        assert callback.call_count == 1
    finally:
        release.set()
        worker.shutdown(wait=True)


def test_worker_lock_rejects_second_process_and_releases(tmp_path):
    lock = tmp_path / "scheduler.lock"
    code = "from observability.worker_lock import single_worker; import sys\nwith single_worker(sys.argv[1]): pass"
    with single_worker(lock):
        child = subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True, text=True)
        assert child.returncode != 0
        assert "Scheduler already running" in child.stderr
    with single_worker(lock):
        pass


def test_logging_is_idempotent_and_contains_run_metadata(isolated_db, tmp_path, capsys):
    logger = logging.getLogger("observability")
    previous_handlers, previous_level, previous_propagate = logger.handlers[:], logger.level, logger.propagate
    logger.handlers = []
    try:
        path = tmp_path / "logs" / "scheduler.log"
        configure_logging(path)
        configure_logging(path)
        assert len(logger.handlers) == 2
        assert any(isinstance(h, RotatingFileHandler) for h in logger.handlers)
        run = service.run_pipeline("normal", seed=23, export_csv=False)
        content = path.read_text(encoding="utf-8")
        assert content.count("Pipeline finish") == 1
        for expected in (f"run_id={run['id']}", "status=HEALTHY", "rows=400", "duration_ms=", "started_at=", "finished_at="):
            assert expected in content
        assert "Pipeline finish" in capsys.readouterr().err
        assert "ORD-23" not in content
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers = previous_handlers
        logger.setLevel(previous_level)
        logger.propagate = previous_propagate


def test_scheduler_cli_env_override_and_graceful_shutdown(monkeypatch, tmp_path):
    worker = Mock()
    monkeypatch.setenv("SCHEDULER_INTERVAL_SECONDS", "15")
    monkeypatch.setattr(scheduler, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(scheduler, "configure_logging", Mock())
    factory = Mock(return_value=worker)
    monkeypatch.setattr(scheduler, "create_scheduler", factory)
    worker.start.side_effect = KeyboardInterrupt
    worker.running = True
    scheduler.main([])
    factory.assert_called_with(15)
    worker.shutdown.assert_called_once_with(wait=True)
    scheduler.main(["--interval", "30"])
    factory.assert_called_with(30)
    monkeypatch.delenv("SCHEDULER_INTERVAL_SECONDS")
    scheduler.main([])
    factory.assert_called_with(300)
    with pytest.raises(SystemExit) as error:
        scheduler.main(["--interval", "0"])
    assert error.value.code == 2


def test_real_worker_process_writes_history_and_logs(tmp_path):
    """Real BlockingScheduler loop, real database and log, two ticks in ~1 second."""
    bootstrap = '''
from pathlib import Path
import sys
from threading import Thread
from apscheduler.events import EVENT_JOB_EXECUTED
from observability import settings
root = Path(sys.argv[1])
settings.PROJECT_ROOT = root
settings.DATA_DIR = root / "data"
settings.DATA_DIR.mkdir()
from observability import scheduler
original = scheduler.create_scheduler
def factory(interval):
    worker = original(interval)
    calls = []
    def completed(event):
        calls.append(event)
        if len(calls) == 2:
            Thread(target=lambda: worker.shutdown(wait=True)).start()
    worker.add_listener(completed, EVENT_JOB_EXECUTED)
    return worker
scheduler.create_scheduler = factory
scheduler.main(["--interval", "1"])
'''
    env = {**os.environ, "PYTHONUTF8": "1", "DATABASE_URL": f"sqlite:///{(tmp_path / 'worker.db').as_posix()}"}
    child = subprocess.run([sys.executable, "-c", bootstrap, str(tmp_path)],
                           env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert child.returncode == 0, child.stderr
    log = (tmp_path / "logs" / "scheduler.log").read_text(encoding="utf-8")
    assert log.count("Pipeline finish") == 2
    assert "Worker stopped" in log
    assert "run_id=1 status=HEALTHY rows=400" in log
    assert "run_id=2 status=HEALTHY rows=400" in log
    assert "Pipeline finish" in child.stderr
    from sqlalchemy import create_engine
    engine = create_engine(env["DATABASE_URL"])
    try:
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(database.PipelineRun)) == 2
            assert connection.scalar(select(func.count()).select_from(database.RawOrder)) == 800
    finally:
        engine.dispose()

from contextlib import contextmanager
from datetime import datetime, timezone
import json

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func, select
from sqlalchemy.orm import declarative_base, sessionmaker

from .settings import DB_URL, PROJECT_ROOT

if DB_URL.startswith("sqlite:///"):
    db_path = DB_URL[len("sqlite:///") :]
    if not db_path.startswith("/"):
        db_path = str(PROJECT_ROOT / db_path)
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False}, hide_parameters=True)
else:
    engine = create_engine(DB_URL, pool_pre_ping=True, hide_parameters=True)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset = Column(String(80), nullable=False, default="fact_orders")
    scenario = Column(String(50), nullable=False)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=False)
    status = Column(String(20), nullable=False)
    row_count = Column(Integer, nullable=False)
    quality_score = Column(Float, nullable=False)
    ml_anomaly = Column(Boolean, nullable=False, default=False)
    ml_anomaly_score = Column(Float, nullable=False, default=0)
    metrics_json = Column(Text, nullable=False)
    schema_json = Column(Text, nullable=False)
    sources_json = Column(Text, nullable=False)
    preview_json = Column(Text, nullable=False)
    duration_ms = Column(Integer, nullable=False)


class RawSnapshot:
    """Technical keys only: business keys may be NULL or duplicated."""
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    payload_json = Column(Text, nullable=False)


class RawOrder(RawSnapshot, Base):
    __tablename__ = "raw_orders"
    order_id = Column(String(100), nullable=True)
    customer_id = Column(Integer, nullable=True)
    amount = Column(Float, nullable=True)
    payment_method = Column(String(80), nullable=True)
    order_status = Column(String(80), nullable=True)
    event_time = Column(Text, nullable=True)


class RawCustomer(RawSnapshot, Base):
    __tablename__ = "raw_customers"
    customer_id = Column(Integer, nullable=True)
    customer_key = Column(Integer, nullable=True)
    display_name = Column(String(100), nullable=True)


class RawPayment(RawSnapshot, Base):
    __tablename__ = "raw_payments"
    order_id = Column(String(100), nullable=True)
    amount = Column(Float, nullable=True)
    payment_method = Column(String(80), nullable=True)
    event_time = Column(Text, nullable=True)


class QualityCheck(Base):
    __tablename__ = "quality_checks"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    code = Column(String(80), nullable=False)
    title = Column(String(160), nullable=False)
    passed = Column(Boolean, nullable=False)
    severity = Column(String(20), nullable=False)
    observed = Column(Float, nullable=True)
    expected = Column(String(160), nullable=False)
    details = Column(Text, nullable=False)


class Incident(Base):
    __tablename__ = "incidents"
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("pipeline_runs.id"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    severity = Column(String(20), nullable=False)
    status = Column(String(30), nullable=False, default="OPEN")
    owner = Column(String(80), nullable=False)
    created_at = Column(DateTime, nullable=False)
    sla_deadline = Column(DateTime, nullable=False)
    resolved_at = Column(DateTime, nullable=True)
    root_cause_json = Column(Text, nullable=False)


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False)
    event_type = Column(String(60), nullable=False)
    message = Column(Text, nullable=False)
    actor = Column(String(80), nullable=False, default="system")


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_id = Column(Integer, ForeignKey("incidents.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False)
    severity = Column(String(20), nullable=False)
    message = Column(Text, nullable=False)
    channel = Column(String(30), nullable=False, default="dashboard")
    delivery_status = Column(String(30), nullable=False, default="DEMO_ONLY")


def init_db():
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def iso(value):
    return value.isoformat(timespec="seconds") + "Z" if value else None


def decode(value, fallback=None):
    return json.loads(value) if value else fallback


def run_to_dict(item):
    return {
        "id": item.id, "dataset": item.dataset, "scenario": item.scenario,
        "started_at": iso(item.started_at), "finished_at": iso(item.finished_at),
        "status": item.status, "row_count": item.row_count,
        "quality_score": item.quality_score, "ml_anomaly": bool(item.ml_anomaly),
        "ml_anomaly_score": item.ml_anomaly_score, "duration_ms": item.duration_ms,
        "metrics": decode(item.metrics_json, {}), "schema": decode(item.schema_json, []),
        "upstream": decode(item.sources_json, {}),
    }


def check_to_dict(item):
    return {
        "id": item.id, "run_id": item.run_id, "code": item.code,
        "title": item.title, "passed": bool(item.passed),
        "severity": item.severity, "observed": item.observed,
        "expected": item.expected, "details": item.details,
    }


def incident_to_dict(item):
    at = now_utc()
    return {
        "id": item.id, "run_id": item.run_id, "title": item.title,
        "severity": item.severity, "status": item.status, "owner": item.owner,
        "created_at": iso(item.created_at), "sla_deadline": iso(item.sla_deadline),
        "resolved_at": iso(item.resolved_at),
        "sla_breached": item.status != "RESOLVED" and at > item.sla_deadline,
        "root_cause": decode(item.root_cause_json, {}),
    }

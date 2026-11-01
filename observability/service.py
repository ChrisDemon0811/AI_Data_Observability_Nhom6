"""Pipeline execution, storage, lifecycle, baseline, and dashboard queries."""
from datetime import timedelta
import json
import logging
import time

import numpy as np
from sqlalchemy import delete, desc, func, select

from .anomaly import ml_score
from .database import (
    Alert, Base, Incident, IncidentEvent, PipelineRun, QualityCheck, RawOrder, RawCustomer, RawPayment,
    check_to_dict, decode, engine, get_session, incident_to_dict,
    init_db, iso, now_utc, run_to_dict,
)
from .generator import SCENARIOS, generate_batch
from .quality import baseline_from_runs, evaluate_quality
from .rca import analyze_root_cause
from .settings import DATA_DIR
from .sources import load_source_batch

logger = logging.getLogger(__name__)

LINEAGE = {
    "nodes": [
        {"id": "raw_orders", "layer": "Raw / Bronze", "owner": "Data Engineering"},
        {"id": "raw_customers", "layer": "Raw / Bronze", "owner": "Data Engineering"},
        {"id": "raw_payments", "layer": "Raw / Bronze", "owner": "Data Engineering"},
        {"id": "stg_orders", "layer": "Staging / Silver", "owner": "Analytics Engineering"},
        {"id": "fact_orders", "layer": "Data Mart / Gold", "owner": "BI / Analytics"},
        {"id": "dashboard_sales", "layer": "BI Consumer", "owner": "Business"},
    ],
    "edges": [
        {"source": "raw_orders", "target": "stg_orders"},
        {"source": "raw_customers", "target": "stg_orders"},
        {"source": "raw_payments", "target": "stg_orders"},
        {"source": "stg_orders", "target": "fact_orders"},
        {"source": "fact_orders", "target": "dashboard_sales"},
    ],
}


def healthy_history(session, count=40):
    return list(reversed(session.execute(
        select(PipelineRun).where(PipelineRun.status == "HEALTHY").order_by(desc(PipelineRun.id)).limit(count)
    ).scalars().all()))


def run_pipeline(scenario="normal", seed=None, export_csv=True):
    start = time.perf_counter()
    began = iso(now_utc())
    execution = {"run_id": None, "rows": None, "committed": False}
    logger.info("Pipeline start started_at=%s scenario=%s", began, scenario)
    try:
        result = _run_pipeline(scenario, seed, export_csv, execution)
    except Exception:
        logger.exception("Pipeline exception started_at=%s finished_at=%s scenario=%s run_id=%s status=ERROR rows=%s duration_ms=%s committed=%s",
                         began, iso(now_utc()), scenario, execution["run_id"], execution["rows"],
                         round((time.perf_counter() - start) * 1000), execution["committed"])
        raise
    logger.info("Pipeline finish started_at=%s finished_at=%s run_id=%s status=%s rows=%s duration_ms=%s",
                result["started_at"], result["finished_at"], result["id"], result["status"],
                result["row_count"], result["duration_ms"])
    return result


def _run_pipeline(scenario, seed, export_csv, execution):
    if scenario not in SCENARIOS:
        raise ValueError(f"Kịch bản không được hỗ trợ: {scenario}")
    init_db()
    start = time.perf_counter()
    began = now_utc()
    with get_session() as session:
        run_number = int(session.scalar(select(func.count()).select_from(PipelineRun)) or 0)
        training = healthy_history(session)
        batch = generate_batch(scenario, seed=seed if seed is not None else 1500 + run_number * 97)
        execution["rows"] = len(batch.frame)
        checks, metrics, quality_score = evaluate_quality(batch, baseline_from_runs(training))
        is_outlier, ml_value, ml_info = ml_score(metrics, training)
        # The actual check thresholds, not the scenario name, determine incidents.
        failed = [c for c in checks if not c.passed]
        status = "FAILED" if failed or is_outlier else "HEALTHY"
        completed = now_utc()
        duration_ms = max(1, round((time.perf_counter() - start) * 1000))
        preview = json.loads(batch.frame.head(12).to_json(orient="records", date_format="iso"))
        record = PipelineRun(
            dataset="fact_orders", scenario=scenario, started_at=began,
            finished_at=completed, status=status, row_count=len(batch.frame),
            quality_score=quality_score, ml_anomaly=is_outlier,
            ml_anomaly_score=ml_value,
            metrics_json=json.dumps({**metrics, "ml": ml_info}, ensure_ascii=False),
            schema_json=json.dumps(list(batch.frame.columns), ensure_ascii=False),
            sources_json=json.dumps(batch.upstream, ensure_ascii=False),
            preview_json=json.dumps(preview, ensure_ascii=False), duration_ms=duration_ms,
        )
        session.add(record)
        session.flush()
        execution["run_id"] = record.id
        load_source_batch(session, record.id, batch)
        for c in checks:
            session.add(QualityCheck(run_id=record.id, **c.as_dict()))
        incident_id = None
        if status == "FAILED":
            critical = ("schema" in {c.code for c in failed}) or ("completeness" in {c.code for c in failed} and metrics["completeness"] < .85)
            severity = "P1 - CRITICAL" if critical else ("P2 - HIGH" if any(x.severity == "HIGH" for x in failed) or len(failed) >= 2 else "P3 - MEDIUM")
            minutes = 30 if critical else (120 if severity.startswith("P2") else 480)
            owner = "Data Engineering" if any(x.code in {"freshness", "schema", "uniqueness", "completeness"} for x in failed) else "Data Analytics"
            reason = analyze_root_cause(checks, batch.upstream, metrics, ml_info)
            title = reason["conclusion"]
            incident = Incident(
                run_id=record.id, title=title, severity=severity, status="OPEN",
                owner=owner, created_at=completed,
                sla_deadline=completed + timedelta(minutes=minutes),
                root_cause_json=json.dumps(reason, ensure_ascii=False),
            )
            session.add(incident)
            session.flush()
            incident_id = incident.id
            for event_type, message in [
                ("PIPELINE_FAILED", f"Run #{record.id} phát hiện {len(failed)} rule lỗi và ML anomaly={is_outlier}."),
                ("ALERT_TRIGGERED", f"Cảnh báo {severity} gửi đến dashboard, chủ sở hữu {owner}."),
                ("AI_RCA", f"Phân tích bằng chứng: {title}. Cần con người kiểm chứng."),
            ]:
                session.add(IncidentEvent(incident_id=incident.id, timestamp=now_utc(), event_type=event_type, message=message, actor="system"))
            session.add(Alert(
                incident_id=incident.id, created_at=now_utc(), severity=severity,
                message=f"Incident #{incident.id}: {title}; run #{record.id}",
                channel="dashboard", delivery_status="DEMO_ONLY",
            ))
        session.flush()
        record.finished_at = now_utc()
        record.duration_ms = max(1, round((time.perf_counter() - start) * 1000))
        result = run_to_dict(record)
        result["checks"] = [c.as_dict() for c in checks]
        result["incident_id"] = incident_id
    execution["committed"] = True
    if export_csv:
        # Synthetic sample export, useful for showing input data to lecturer.
        batch.frame.to_csv(DATA_DIR / "latest_orders.csv", index=False, encoding="utf-8-sig")
    return result


def seed_demo(reset=False):
    init_db()
    with get_session() as session:
        if reset:
            for model in [Alert, IncidentEvent, QualityCheck, Incident, RawPayment, RawCustomer, RawOrder, PipelineRun]:
                session.execute(delete(model))
        elif (session.scalar(select(func.count()).select_from(PipelineRun)) or 0) > 0:
            return {"message": "Có dữ liệu demo rồi. Dùng reset nếu muốn khởi tạo lại.", "created_runs": 0}
    # Healthy baseline before training IsolationForest. 3 varied failures for a lively dashboard.
    plan = ["normal"] * 10 + ["late_data", "normal", "null_values", "normal", "distribution_drift", "duplicate_keys"]
    outcomes = [run_pipeline(scenario, seed=100 + n * 11) for n, scenario in enumerate(plan)]
    return {"message": "Đã tạo dữ liệu minh họa", "created_runs": len(outcomes), "incidents": sum(x["incident_id"] is not None for x in outcomes)}


def list_runs(limit=50):
    with get_session() as s:
        rows = s.execute(select(PipelineRun).order_by(desc(PipelineRun.id)).limit(limit)).scalars().all()
        return [run_to_dict(x) for x in rows]


def get_run(run_id):
    with get_session() as s:
        rec = s.get(PipelineRun, run_id)
        if not rec:
            return None
        result = run_to_dict(rec)
        result["checks"] = [check_to_dict(x) for x in s.execute(select(QualityCheck).where(QualityCheck.run_id == run_id)).scalars()]
        result["preview"] = decode(rec.preview_json, [])
        return result


def list_incidents(limit=100):
    with get_session() as s:
        rows = s.execute(select(Incident).order_by(desc(Incident.id)).limit(limit)).scalars().all()
        return [incident_to_dict(row) for row in rows]


def get_incident(incident_id):
    with get_session() as s:
        item = s.get(Incident, incident_id)
        if not item:
            return None
        result = incident_to_dict(item)
        result["events"] = [{"timestamp": iso(evt.timestamp), "type": evt.event_type, "message": evt.message, "actor": evt.actor} for evt in s.execute(select(IncidentEvent).where(IncidentEvent.incident_id == incident_id).order_by(IncidentEvent.id)).scalars()]
        return result


def update_incident(incident_id, action):
    if action not in ("acknowledge", "resolve"):
        raise ValueError("Action không hợp lệ")
    with get_session() as s:
        item = s.get(Incident, incident_id)
        if item is None:
            return None
        if item.status == "RESOLVED":
            return incident_to_dict(item)
        if action == "acknowledge":
            item.status = "ACKNOWLEDGED"
            desc_text = "Đã tiếp nhận incident để điều tra."
            event_type = "ACKNOWLEDGED"
        else:
            item.status = "RESOLVED"
            item.resolved_at = now_utc()
            desc_text = "Đã xác nhận xử lý và đóng incident (thao tác DEMO)."
            event_type = "RESOLVED"
        s.add(IncidentEvent(incident_id=item.id, timestamp=now_utc(), event_type=event_type, message=desc_text, actor="demo-user"))
        s.flush()
        return incident_to_dict(item)


def list_alerts(limit=100):
    with get_session() as s:
        rows = s.execute(select(Alert).order_by(desc(Alert.id)).limit(limit)).scalars().all()
        return [{"id": x.id, "incident_id": x.incident_id, "created_at": iso(x.created_at), "severity": x.severity, "message": x.message, "channel": x.channel, "delivery_status": x.delivery_status} for x in rows]


def summary():
    with get_session() as s:
        runs = s.execute(select(PipelineRun).order_by(desc(PipelineRun.id)).limit(100)).scalars().all()
        active = s.scalar(select(func.count()).select_from(Incident).where(Incident.status != "RESOLVED")) or 0
        incidents_total = s.scalar(select(func.count()).select_from(Incident)) or 0
        healthy = sum(x.status == "HEALTHY" for x in runs)
        recent = runs[0] if runs else None
        return {
            "runs_total": int(s.scalar(select(func.count()).select_from(PipelineRun)) or 0),
            "runs_window": len(runs), "healthy_window": healthy,
            "success_rate": round(100 * healthy / len(runs), 1) if runs else 0,
            "active_incidents": int(active), "incidents_total": int(incidents_total),
            "latest_run": run_to_dict(recent) if recent else None,
            "quality_average": round(float(np.mean([x.quality_score for x in runs])), 1) if runs else 0,
        }


def trend(limit=100):
    runs = list(reversed(list_runs(limit)))
    return [{"run_id": run["id"], "time": run["finished_at"], "scenario": run["scenario"], "status": run["status"], "quality_score": run["quality_score"], "anomaly_score": run["ml_anomaly_score"], **{key: value for key, value in run["metrics"].items() if key != "ml"}} for run in runs]

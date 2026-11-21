"""Persist mock notifications locally; no external delivery is performed."""
import json

from sqlalchemy import select

from .database import Alert, IncidentEvent, iso, now_utc


def create_mock_notifications(session, incident, run, checks):
    """Return newly created Alert objects in email_mock, slack_mock order.

    Pass ORM Incident/PipelineRun records and an iterable of QualityCheck or
    CheckResult objects. Records must belong to the caller's session. Pending
    records are flushed to obtain IDs. Existing incident/channel pairs are
    skipped, so a repeated call returns an empty list. Inserts are flushed, but
    the caller owns commit/rollback for both alerts and their timeline events.
    Deduplication covers sequential retries; concurrent callers require external
    serialization because the existing schema has no unique channel constraint.
    """
    session.flush()
    channels = ("email_mock", "slack_mock")
    existing = set(session.scalars(select(Alert.channel).where(
        Alert.incident_id == incident.id,
        Alert.channel.in_(channels),
    )))
    missing = [channel for channel in channels if channel not in existing]
    if not missing:
        return []

    failed_codes = ", ".join(sorted({check.code for check in checks if not check.passed})) or "none"
    message = (
        f"Incident #{incident.id}: {incident.title}\n"
        f"Pipeline: {run.dataset}; run #{run.id}; status: {run.status}\n"
        f"Failed check codes: {failed_codes}\n"
        f"ML anomaly: {bool(run.ml_anomaly)}; Z-score: "
        f"{json.loads(run.metrics_json).get('statistics', {}).get('zscore', {}).get('status', 'UNAVAILABLE')}\n"
        f"Severity: {incident.severity}\n"
        f"Owner: {incident.owner}\n"
        f"SLA deadline: {iso(incident.sla_deadline)}\n"
        f"Incident status: {incident.status}"
    )
    created = []
    timestamp = now_utc()
    for channel in missing:
        alert = Alert(
            incident_id=incident.id, created_at=timestamp,
            severity=incident.severity, message=message,
            channel=channel, delivery_status="MOCK_SENT",
        )
        session.add_all([alert, IncidentEvent(
            incident_id=incident.id, timestamp=timestamp,
            event_type="NOTIFICATION_MOCK",
            message=f"{channel}: MOCK_SENT (mock notification).\n{message}",
            actor="system",
        )])
        created.append(alert)
    session.flush()
    return created

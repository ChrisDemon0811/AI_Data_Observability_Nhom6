"""Mock notification tests use only the isolated temporary database."""
import socket

import pytest
from sqlalchemy import event, select

from observability import database, service
from observability.notifications import create_mock_notifications
from observability.quality import CheckResult


@pytest.fixture
def notification_case(isolated_db, monkeypatch):
    with monkeypatch.context() as setup_patch:
        setup_patch.setattr(service, "create_mock_notifications", lambda *args: [], raising=False)
        result = service.run_pipeline("schema_change", seed=23, export_csv=False)
    with database.get_session() as session:
        incident = session.get(database.Incident, result["incident_id"])
        run = session.get(database.PipelineRun, result["id"])
        checks = list(session.scalars(select(database.QualityCheck).where(
            database.QualityCheck.run_id == run.id,
        )))
        yield session, incident, run, checks


@pytest.fixture
def automatic_notification_case(isolated_db):
    return service.run_pipeline("schema_change", seed=23, export_csv=False)


def test_pipeline_notifications_and_incident_lifecycle(automatic_notification_case):
    result = automatic_notification_case
    assert result["status"] == "FAILED"
    incident_id = result["incident_id"]
    assert incident_id is not None

    alerts = [alert for alert in service.list_alerts() if alert["incident_id"] == incident_id]
    assert len(alerts) == 3
    assert {alert["channel"] for alert in alerts} == {"dashboard", "email_mock", "slack_mock"}
    mocks = [alert for alert in alerts if alert["channel"] != "dashboard"]
    initial = service.get_incident(incident_id)
    assert initial["status"] == "OPEN"
    mock_events = [event for event in initial["events"] if event["type"] == "NOTIFICATION_MOCK"]
    assert len(mock_events) == 2
    assert {event["type"] for event in initial["events"]} >= {
        "PIPELINE_FAILED", "ALERT_TRIGGERED", "AI_RCA",
    }
    for alert in mocks:
        assert alert["delivery_status"] == "MOCK_SENT"
        assert f"Incident #{incident_id}" in alert["message"]
        assert f"run #{result['id']}" in alert["message"]
        assert result["dataset"] in alert["message"]
        assert "schema" in alert["message"]
        assert initial["owner"] in alert["message"]
        assert initial["sla_deadline"] in alert["message"]
        assert "Incident status: OPEN" in alert["message"]
        matching = [event for event in mock_events if alert["channel"] in event["message"]]
        assert len(matching) == 1
        assert "MOCK_SENT" in matching[0]["message"]
        assert alert["message"] in matching[0]["message"]
        assert matching[0]["timestamp"] == alert["created_at"]
        assert matching[0]["actor"] == "system"

    acknowledged = service.update_incident(incident_id, "acknowledge")
    assert acknowledged["status"] == "ACKNOWLEDGED"
    after_ack = service.get_incident(incident_id)
    assert after_ack["events"][:-1] == initial["events"]
    assert after_ack["events"][-1]["type"] == "ACKNOWLEDGED"

    resolved = service.update_incident(incident_id, "resolve")
    assert resolved["status"] == "RESOLVED"
    assert resolved["resolved_at"] is not None
    after_resolve = service.get_incident(incident_id)
    assert after_resolve["events"][:-1] == after_ack["events"]
    assert after_resolve["events"][-1]["type"] == "RESOLVED"
    assert [alert for alert in service.list_alerts() if alert["incident_id"] == incident_id] == alerts


def test_creates_mock_alerts_and_matching_events(notification_case, monkeypatch):
    session, incident, run, checks = notification_case

    def reject_network(*args, **kwargs):
        pytest.fail("Mock notifications must not access the network")

    monkeypatch.setattr(socket, "socket", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    baseline_events = list(session.scalars(select(database.IncidentEvent)))
    alerts = create_mock_notifications(session, incident, run, iter(checks))

    assert [alert.channel for alert in alerts] == ["email_mock", "slack_mock"]
    assert all(alert.id is not None for alert in alerts)
    failed = sorted(check.code for check in checks if not check.passed)
    for alert in alerts:
        assert alert.incident_id == incident.id
        assert alert.delivery_status == "MOCK_SENT"
        assert alert.severity == incident.severity
        for expected in (
            f"Incident #{incident.id}", run.dataset, f"run #{run.id}",
            f"status: {run.status}", f"Failed check codes: {', '.join(failed)}",
            incident.owner, database.iso(incident.sla_deadline),
            f"Incident status: {incident.status}",
        ):
            assert expected in alert.message

    events = list(session.scalars(select(database.IncidentEvent).where(
        database.IncidentEvent.event_type == "NOTIFICATION_MOCK",
    ).order_by(database.IncidentEvent.id)))
    assert len(events) == 2
    for alert, timeline in zip(alerts, events):
        assert timeline.incident_id == incident.id
        assert timeline.timestamp == alert.created_at
        assert timeline.actor == "system"
        assert alert.channel in timeline.message
        assert "MOCK_SENT" in timeline.message
        assert alert.message in timeline.message
    assert len(list(session.scalars(select(database.IncidentEvent)))) == len(baseline_events) + 2
    dashboard = session.scalar(select(database.Alert).where(database.Alert.channel == "dashboard"))
    assert dashboard.delivery_status == "DEMO_ONLY"


def test_retries_skip_existing_channels_before_and_after_commit(notification_case):
    session, incident, run, checks = notification_case
    alerts = create_mock_notifications(session, incident, run, checks)
    alert_ids = [alert.id for alert in alerts]
    assert create_mock_notifications(session, incident, run, checks) == []
    session.commit()

    with database.get_session() as retry_session:
        retry_incident = retry_session.get(database.Incident, incident.id)
        retry_run = retry_session.get(database.PipelineRun, run.id)
        assert create_mock_notifications(retry_session, retry_incident, retry_run, checks) == []
        stored = list(retry_session.scalars(select(database.Alert).where(
            database.Alert.channel.in_(("email_mock", "slack_mock")),
        ).order_by(database.Alert.id)))
        assert [alert.id for alert in stored] == alert_ids
        events = list(retry_session.scalars(select(database.IncidentEvent).where(
            database.IncidentEvent.event_type == "NOTIFICATION_MOCK",
        )))
        assert len(events) == 2


@pytest.mark.parametrize("existing_channel", ["email_mock", "slack_mock"])
def test_only_creates_missing_channel(notification_case, existing_channel):
    session, incident, run, checks = notification_case
    existing = database.Alert(
        incident_id=incident.id, created_at=database.now_utc(),
        severity=incident.severity, message="Previously recorded",
        channel=existing_channel, delivery_status="MOCK_SENT",
    )
    session.add(existing)
    # Pending duplicates must also be recognized when autoflush is disabled.
    with session.no_autoflush:
        alerts = create_mock_notifications(session, incident, run, checks)
    assert len(alerts) == 1
    assert alerts[0].channel == ("slack_mock" if existing_channel == "email_mock" else "email_mock")
    assert existing.message == "Previously recorded"
    assert create_mock_notifications(session, incident, run, checks) == []
    events = list(session.scalars(select(database.IncidentEvent).where(
        database.IncidentEvent.event_type == "NOTIFICATION_MOCK",
    )))
    assert len(events) == 1
    assert alerts[0].channel in events[0].message


def test_deduplication_is_scoped_to_incident(notification_case):
    session, incident, run, checks = notification_case
    create_mock_notifications(session, incident, run, checks)
    another = database.Incident(
        run_id=run.id, title="Another incident", severity=incident.severity,
        status="ACKNOWLEDGED", owner="Data Analytics",
        created_at=database.now_utc(), sla_deadline=incident.sla_deadline,
        root_cause_json="{}",
    )
    session.add(another)
    alerts = create_mock_notifications(session, another, run, checks)
    assert len(alerts) == 2
    assert all(alert.incident_id == another.id for alert in alerts)
    assert all("Incident status: ACKNOWLEDGED" in alert.message for alert in alerts)


@pytest.mark.parametrize("checks", [[], [CheckResult(
    code="schema", title="Schema", passed=True, severity="HIGH",
    observed=1, expected="1", details="Passed",
)]])
def test_no_failed_checks_is_readable(notification_case, checks):
    session, incident, run, _ = notification_case
    alerts = create_mock_notifications(session, incident, run, checks)
    assert len(alerts) == 2
    assert all("Failed check codes: none\n" in alert.message for alert in alerts)


def test_check_results_include_only_failed_codes(notification_case):
    session, incident, run, checks = notification_case
    results = [CheckResult(**{
        field: getattr(check, field) for field in CheckResult.__dataclass_fields__
    }) for check in checks]
    alerts = create_mock_notifications(session, incident, run, results)
    expected = ", ".join(sorted(check.code for check in results if not check.passed))
    assert all(f"Failed check codes: {expected}\n" in alert.message for alert in alerts)


def test_caller_rollback_removes_alerts_and_events(notification_case):
    session, incident, run, checks = notification_case
    create_mock_notifications(session, incident, run, checks)
    session.rollback()
    assert list(session.scalars(select(database.Alert.channel))) == ["dashboard"]
    assert not list(session.scalars(select(database.IncidentEvent).where(
        database.IncidentEvent.event_type == "NOTIFICATION_MOCK",
    )))
    assert session.get(database.Incident, incident.id) is not None


def test_event_insert_failure_rolls_back_both_channels(notification_case):
    session, incident, run, checks = notification_case

    def fail_event_insert(mapper, connection, target):
        if target.event_type == "NOTIFICATION_MOCK":
            raise RuntimeError("Simulated timeline storage failure")

    event.listen(database.IncidentEvent, "before_insert", fail_event_insert)
    try:
        with pytest.raises(RuntimeError, match="timeline storage failure"):
            create_mock_notifications(session, incident, run, checks)
        session.rollback()
    finally:
        event.remove(database.IncidentEvent, "before_insert", fail_event_insert)

    assert list(session.scalars(select(database.Alert.channel))) == ["dashboard"]
    assert not list(session.scalars(select(database.IncidentEvent).where(
        database.IncidentEvent.event_type == "NOTIFICATION_MOCK",
    )))

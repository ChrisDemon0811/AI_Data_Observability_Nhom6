"""Presentation-only timestamps. Database/API and raw evidence remain UTC."""
from datetime import datetime, timedelta, timezone

VIETNAM_TIME = timezone(timedelta(hours=7))


def format_timestamp(value):
    """Render API ISO timestamps in Vietnam time, independent of host timezone."""
    if value is None or value == "":
        return "—"
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # Legacy database datetimes are UTC without a timezone marker.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(VIETNAM_TIME).strftime("%d/%m/%Y %H:%M:%S") + " (UTC+7)"
    except (ValueError, TypeError, AttributeError):
        return str(value)

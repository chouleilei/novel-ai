from datetime import datetime, timezone


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None

    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        normalized = value.replace(tzinfo=timezone.utc)
    else:
        normalized = value.astimezone(timezone.utc)

    return normalized.isoformat()

"""UTC time helpers. Every timestamp the desk stores is an ISO-8601 UTC string, so they sort as text."""

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def parse_iso(text: str) -> datetime:
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)

"""Helpers for UTC timestamps stored in the existing naive DateTime columns."""
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time without tzinfo for the existing DB schema."""
    return datetime.now(UTC).replace(tzinfo=None)

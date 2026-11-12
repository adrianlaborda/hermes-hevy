"""Calendar helpers. Dates are the user's local calendar days, inclusive at both ends."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Iterator, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ArgError(ValueError):
    """A tool argument the model passed that cannot be used; the message says how to fix it."""


def known_zone(name: str | None) -> bool:
    """Whether ``name`` is an IANA zone this system knows."""
    if not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def zone(name: str | None) -> tzinfo:
    """The named IANA zone, or the server's local zone when the name is empty or unknown."""
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def parse_day(value: Any, today: date) -> date:
    text = str(value).strip().lower()
    if text == "today":
        return today
    if text == "yesterday":
        return today - timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ArgError(f"Invalid date {value!r}: use YYYY-MM-DD, 'today' or 'yesterday'.") from None


def resolve_range(args: Mapping[str, Any], today: date, *, default_days: int = 1,
                  max_days: int = 93) -> tuple[date, date]:
    """``(start, end)`` from ``start_date``/``end_date``, ``days`` (ending today) or ``date``.

    The end is clamped to today: Hevy has nothing logged in the future."""
    if args.get("start_date") or args.get("end_date"):
        end = parse_day(args["end_date"], today) if args.get("end_date") else today
        start = parse_day(args["start_date"], today) if args.get("start_date") else end
    elif args.get("days") not in (None, ""):
        try:
            days = int(args["days"])
        except (TypeError, ValueError):
            raise ArgError(f"Invalid days {args['days']!r}: use a whole number.") from None
        if days < 1:
            raise ArgError("days must be at least 1.")
        end, start = today, today - timedelta(days=days - 1)
    elif args.get("date"):
        start = end = parse_day(args["date"], today)
    else:
        end, start = today, today - timedelta(days=default_days - 1)
    end = min(end, today)
    if start > end:
        raise ArgError(f"start_date {start} is after end_date {end}.")
    span = (end - start).days + 1
    if span > max_days:
        raise ArgError(f"The range covers {span} days; this tool accepts at most {max_days}.")
    return start, end


def chunks(start: date, end: date, max_days: int) -> Iterator[tuple[date, date]]:
    """Consecutive inclusive windows of at most ``max_days`` covering ``start..end``."""
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=max_days - 1), end)
        yield cursor, stop
        cursor = stop + timedelta(days=1)



def parse_instant(value: Any) -> Optional[datetime]:
    """An aware UTC datetime from Hevy's ISO 8601 text ('Z', offsets, fractions); None if empty or invalid."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        moment = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def iso_utc(moment: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ``, the form Hevy accepts; a naive datetime is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_local_time(value: Any, tz: tzinfo) -> datetime:
    """A time the model passed: ISO with an offset or 'Z', or local ``YYYY-MM-DD HH:MM`` in ``tz``."""
    text = str(value or "").strip()
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ArgError(f"Invalid time {value!r}: use 'YYYY-MM-DD HH:MM' (local time) or ISO 8601.") from None
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment

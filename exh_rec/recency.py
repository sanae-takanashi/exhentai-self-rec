"""Publication-date boundary for live collection and recommendation queues."""
import calendar
from datetime import date, datetime, timedelta, timezone


LOCAL_TIMEZONE = timezone(timedelta(hours=8))


def recent_gallery_cutoff(today: date | None = None) -> str:
    today = today or datetime.now(LOCAL_TIMEZONE).date()
    month_index = today.year * 12 + today.month - 1 - 3
    year, month = divmod(month_index, 12)
    month += 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def gallery_posted_date(value: str | None) -> str | None:
    text = str(value or "").strip()[:10]
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return text if parsed.isoformat() == text else None

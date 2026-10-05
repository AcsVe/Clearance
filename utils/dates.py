from datetime import datetime, timezone, date
from zoneinfo import ZoneInfo

from flask import current_app


def to_local(dt):
    if dt is None:
        return None
    if isinstance(dt, date) and not isinstance(dt, datetime):
        return dt
    tz = ZoneInfo(current_app.config.get('TIMEZONE', 'Asia/Amman'))
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def fmt_date(value, raw=None):
    """Gregorian YYYY-MM-DD in local time; falls back to the original sheet
    text when only that exists (imported rows)."""
    if value is None:
        return raw or ''
    v = to_local(value)
    return v.strftime('%Y-%m-%d')


def fmt_datetime(value, raw=None):
    if value is None:
        return raw or ''
    return to_local(value).strftime('%Y-%m-%d %H:%M')


def register_filters(app):
    app.jinja_env.filters['date'] = fmt_date
    app.jinja_env.filters['datetime'] = fmt_datetime

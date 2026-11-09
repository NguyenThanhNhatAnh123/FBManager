"""Pure read-only calendar keys for schedule-list SQL filters."""
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SORTS = frozenset(('recent', 'scheduled_asc', 'scheduled_desc'))


def validate_calendar(value):
    if not isinstance(value, str) or len(value) > 10: raise ValueError('Invalid calendar')
    if value == '': return value
    if not re.fullmatch(r'[1-9][0-9]{3}(?:-[0-9]{2}(?:-[0-9]{2})?)?', value):
        raise ValueError('Invalid calendar')
    parts = [int(part) for part in value.split('-')]
    date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    return value


def validate_sort(value):
    if not isinstance(value, str) or value not in SORTS: raise ValueError('Invalid sort')
    return value


def local_date_key(value, zone):
    """SQLite UDF: invalid stored rows never match a requested local calendar."""
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 64 or not isinstance(zone, str) or not 0 < len(zone) <= 120:
            return None
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if instant.tzinfo is None or instant.utcoffset() is None: return None
        local = instant.astimezone(ZoneInfo(zone))
        return f'{local.year:04}-{local.month:02}-{local.day:02}'
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return None

"""Calendar predicates for local library creation dates, before SQL paging."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import AppError
from .reel_calendar import validate_calendar, local_date_key


def validate_library_calendar(calendar='', timezone='America/Los_Angeles'):
    try:
        validate_calendar(calendar)
        if not isinstance(timezone, str) or not 0 < len(timezone) <= 100:
            raise ValueError()
        ZoneInfo(timezone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise AppError('Chọn ngày, tháng, năm hoặc múi giờ hợp lệ.', 'invalid_library_calendar', 422) from None


def calendar_predicate(db, column, calendar='', timezone='America/Los_Angeles'):
    validate_library_calendar(calendar, timezone)
    if column not in ('i.created_at', 'a.created_at'):
        raise ValueError('Fixed library timestamp column required')
    if not calendar:
        return None, []
    db.create_function('library_local_date', 2, local_date_key, deterministic=True)
    return f'substr(library_local_date({column},?),1,?)=?', [timezone, len(calendar), calendar]

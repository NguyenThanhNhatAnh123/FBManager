"""Visible Facebook dates and URLs. No cookies, tokens or private API calls."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit, urlencode
from pydantic import Field, field_validator, model_validator
from app.core.schemas import Ids


def facebook_url(value):
    """Allow only Facebook profile/Page URLs, without tracking parameters."""
    p = urlsplit(value)
    if p.scheme != 'https' or p.hostname not in ('facebook.com', 'www.facebook.com', 'm.facebook.com') or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Use an https://www.facebook.com/ Page URL.')
    parts = [part for part in p.path.split('/') if part]
    query = parse_qs(p.query)
    if parts == ['profile.php'] and re.fullmatch(r'\d+', query.get('id', [''])[0]):
        key = query['id'][0]
        return key, 'https://www.facebook.com/profile.php?' + urlencode({'id': key})
    if len(parts) == 1 and re.fullmatch(r'[\w.-]+', parts[0], flags=re.ASCII) and parts[0].lower() not in {
        'me', 'profile.php', 'home.php', 'login', 'login.php', 'checkpoint', 'pages', 'watch', 'reels', 'reel', 'friends', 'groups',
        'marketplace', 'notifications', 'settings', 'help', 'privacy', 'business', 'messages', 'gaming', 'events', 'stories',
        'recover', 'logout.php', 'photo.php', 'photos', 'permalink.php', 'story.php', 'share', 'sharer.php', 'search',
    }:
        return parts[0].lower(), 'https://www.facebook.com/' + parts[0]
    # Legacy Page links: /pages/name/123 or /people/name/123.
    if len(parts) == 3 and parts[0] in ('pages', 'people') and parts[2].isdigit():
        return parts[2], 'https://www.facebook.com/profile.php?id=' + parts[2]
    raise ValueError('A Facebook Page/profile link is required.')


def utc(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('A UTC offset is required.')
    return result.astimezone(timezone.utc)


class FacebookCheck(Ids):
    # The browser's Intl timezone database supplies DST-aware day boundaries.
    # This works on Windows without an additional Python timezone installation.
    timezone: str = Field(min_length=1, max_length=100)
    check_date: str
    day_start: str
    day_end: str
    names_only: bool = False

    @field_validator('check_date')
    @classmethod
    def valid_date(cls, value):
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()

    @model_validator(mode='after')
    def valid_window(self):
        start, end = utc(self.day_start), utc(self.day_end)
        if not timedelta(hours=23) <= end - start <= timedelta(hours=25):
            raise ValueError('Invalid daily time window.')
        if not self.names_only and not start <= datetime.now(timezone.utc) < end:
            raise ValueError('The selected day has changed. Refresh and check again.')
        self.day_start, self.day_end = start.isoformat(), end.isoformat()
        return self


MONTHS = {name: index for index, names in enumerate([
    ('january', 'jan'), ('february', 'feb'), ('march', 'mar'), ('april', 'apr'), ('may',), ('june', 'jun'),
    ('july', 'jul'), ('august', 'aug'), ('september', 'sep', 'sept'), ('october', 'oct'), ('november', 'nov'), ('december', 'dec')
], 1) for name in names}


def visible_post_date(value, observed_at, window):
    """Return a local calendar date only when the visible timestamp is understood."""
    text = re.sub(r'\s+', ' ', str(value or '')).strip().lower().replace('\u200e', '')
    if not text:
        return None
    start, end = utc(window.day_start), utc(window.day_end)
    target = datetime.fromisoformat(window.check_date).date()
    def date_at(instant):
        if start <= instant < end:
            return target
        return target + timedelta(days=-1 if instant < start else 1)
    if re.fullmatch(r'\d{10}(?:\.\d+)?', text):
        try:
            return date_at(datetime.fromtimestamp(float(text), timezone.utc))
        except (ValueError, OverflowError, OSError):
            return None
    if re.match(r'^\d{4}-\d\d-\d\d', text):
        try:
            parsed = datetime.fromisoformat(text.replace('z', '+00:00'))
            return date_at(parsed) if parsed.tzinfo else parsed.date()
        except ValueError:
            pass
    if re.fullmatch(r'(just now|now|vừa xong|vừa đăng|a moment ago)', text):
        return date_at(observed_at)
    relative = re.fullmatch(r'(\d+)\s*(s|sec(?:ond)?s?|giây|m|min(?:ute)?s?|phút|h|hr?s?|hours?|giờ|d|days?|ngày)(?:\s+ago|\s+trước)?', text)
    if relative:
        n, unit = int(relative[1]), relative[2]
        seconds = 86400 if unit in ('d', 'day', 'days', 'ngày') else 3600 if unit in ('h', 'hr', 'hrs', 'hour', 'hours', 'giờ') else 60 if unit.startswith(('m', 'phút')) else 1
        return date_at(observed_at - timedelta(seconds=n * seconds))
    if re.match(r'^(today|hôm nay)(\b|\s)', text):
        return date_at(observed_at)
    if re.match(r'^(yesterday|hôm qua)(\b|\s)', text):
        return date_at(observed_at) - timedelta(days=1)
    vi = re.search(r'(\d{1,2})\s+tháng\s+(\d{1,2})(?:,?\s+(\d{4}))?', text)
    en = re.search(r'\b(' + '|'.join(MONTHS) + r')\s+(\d{1,2})(?!\d)(?:,?\s+(\d{4}))?', text)
    if not en:
        reverse = re.search(r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')(?:,?\s+(\d{4}))?', text)
        en = (None, reverse[2], reverse[1], reverse[3]) if reverse else None
    try:
        if vi or en:
            day, month, year = (int(vi[1]), int(vi[2]), vi[3]) if vi else (int(en[2]), MONTHS[en[1]], en[3])
            date = target.replace(year=int(year or target.year), month=month, day=day)
            if not year and date > target:
                date = date.replace(year=date.year - 1)
            return date
    except ValueError:
        pass
    return None


def current_window(row, at=None):
    try:
        return utc(row.day_start) <= (at or datetime.now(timezone.utc)) < utc(row.day_end)
    except (ValueError, TypeError, AttributeError):
        return False

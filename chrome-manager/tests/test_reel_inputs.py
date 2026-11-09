from datetime import datetime,timedelta,timezone

import pytest
from pydantic import ValidationError

from app.core.reel_inputs import StartReel,VerifyReelLink


def inputs(**changes):
    return {'profile_id':3,'page_url':'https://m.facebook.com/Fixture.Page?ref=tracking','asset_id':'a'*32,
            'caption':'Caption\n#fixture','scheduled_at':'2026-11-01T10:00:00+07:00'}|changes


def test_aware_schedule_normalizes_to_utc_and_page_tracking_is_removed():
    value=StartReel(**inputs())
    assert value.page_url=='https://www.facebook.com/Fixture.Page'
    assert value.scheduled_at==datetime(2026,11,1,3,tzinfo=timezone.utc)
    assert value.scheduled_at.utcoffset()==timedelta(0) and value.timezone=='UTC'
    assert value.caption=='Caption\n#fixture'


def test_local_schedule_stays_naive_for_trusted_manager_resolver_without_tzdata():
    value=StartReel(**inputs(scheduled_at=None,scheduled_local='2026-11-01T01:30:00',timezone='America/Los_Angeles',fold=1))
    assert value.scheduled_at is None and value.scheduled_local==datetime(2026,11,1,1,30)
    assert value.timezone=='America/Los_Angeles' and value.fold==1
    assert StartReel(**inputs(timezone='NotAReal/ButSyntacticallyValid')).timezone=='NotAReal/ButSyntacticallyValid'


@pytest.mark.parametrize('changes',[
    {'scheduled_at':None}, {'scheduled_local':'2026-11-01T10:00:00'},
    {'scheduled_at':None,'scheduled_local':'2026-11-01'},
    {'scheduled_at':'2026-11-01T10:00:00'}, {'scheduled_at':None,'scheduled_local':'2026-11-01T10:00:00Z'},
    {'scheduled_at':12345678}, {'profile_id':0}, {'asset_id':'../../private.mp4'},
    {'asset_id':'A'*32}, {'page_url':'https://facebook.com.evil.test/Page'}, {'page_url':'https://www.facebook.com/reels'},
    {'caption':'a'*5001}, {'caption':'bad\x00caption'}, {'page_name':'bad\nname'},
    {'timezone':'../America/Los_Angeles'}, {'timezone':'America/Los Angeles'}, {'timezone':'America/../New_York'},
    {'fold':2}, {'fold':True}, {'title':'unsupported'}, {'path':'C:/private/video.mp4'},
])
def test_inputs_reject_ambiguous_times_paths_unknown_fields_and_invalid_values(changes):
    with pytest.raises(ValidationError):StartReel(**inputs(**changes))


@pytest.mark.parametrize('url',[
    'https://www.facebook.com/reel/123456',
    'https://m.facebook.com/reels/123456/?ref=tracking#fragment',
    'https://facebook.com:443/reel/123456?tracking=1',
    'HTTPS://WWW.FACEBOOK.COM/reels/123456',
])
def test_verification_link_accepts_only_official_numeric_permalink_and_strips_tracking(url):
    assert VerifyReelLink(reel_url=url).reel_url=='https://www.facebook.com/reel/123456'


@pytest.mark.parametrize('url',[
    'http://www.facebook.com/reel/123456','https://facebook.com.evil.test/reel/123456',
    'https://user:password@www.facebook.com/reel/123456','https://www.facebook.com:444/reel/123456',
    'https://www.facebook.com:/reel/123456','//www.facebook.com/reel/123456',
    'https://www.facebooK.com/reel/123456',
    'https://www.facebook.com/profile.php?id=123456','https://www.facebook.com/reels/create/',
    'https://www.facebook.com/reel/１２３４５６','https://www.facebook.com/reel/123456/extra',
    'https://www.facebook.com/reel/%31%32%33','https://www.facebook.com/reel/123456\n',
    'https://www.facebook.com/\treel/123456','https://www.facebook.com\\@evil.test/reel/123456',
])
def test_verification_link_rejects_external_routes_credentials_ports_and_nonascii_ids(url):
    with pytest.raises(ValidationError):VerifyReelLink(reel_url=url)


def test_verification_link_body_rejects_unknown_fields():
    with pytest.raises(ValidationError):VerifyReelLink(reel_url='https://www.facebook.com/reel/123456',publish=True)

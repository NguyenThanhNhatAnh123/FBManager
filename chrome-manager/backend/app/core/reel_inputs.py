"""Inputs for local Reel scheduling; timezone resolution belongs to the manager."""
from datetime import datetime,timezone as utc_timezone
import re
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field,field_validator,model_validator

from app.core.facebook import facebook_url
from app.core.schemas import Strict


def canonical_reel_url(value):
    """Accept only public Facebook Reel permalinks, never arbitrary navigation."""
    error='Dùng link Reel HTTPS chính thức của Facebook có ID bằng số.'
    if not isinstance(value,str) or re.search(r'[\x00-\x20\x7f]',value):raise ValueError(error)
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or not re.fullmatch(r'(?:www\.|m\.)?facebook\.com(?::443)?',parsed.netloc,flags=re.IGNORECASE|re.ASCII):raise ValueError(error)
        match=re.fullmatch(r'/reels?/([0-9]+)/?',parsed.path)
        if match is None:raise ValueError(error)
        return 'https://www.facebook.com/reel/'+match[1]
    except (TypeError,ValueError):raise ValueError(error) from None


class VerifyReelLink(Strict):
    reel_url:str=Field(min_length=1,max_length=2048)

    @field_validator('reel_url')
    @classmethod
    def canonical_link(cls,value):return canonical_reel_url(value)


class StartReel(Strict):
    request_id: str | None = None
    profile_id:int=Field(gt=0)
    page_url:str=Field(min_length=1,max_length=2048)
    page_name:str|None=Field(default=None,max_length=160)
    asset_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    caption:str=Field(default='',max_length=5000)
    scheduled_at:datetime|None=None
    scheduled_local:datetime|None=None
    timezone:str=Field(default='UTC',min_length=1,max_length=120)
    fold:int|None=Field(default=None,strict=True,ge=0,le=1)
    approval_policy:Literal['remind_until_decision_v1']|None='remind_until_decision_v1'

    @field_validator('request_id')
    @classmethod
    def canonical_request_id(cls,value):
        if value is None:return None
        try:
            if str(UUID(value))!=value:raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise ValueError('request_id phải là UUID viết thường chuẩn.') from None
        return value

    @field_validator('page_url')
    @classmethod
    def canonical_page(cls,value):
        return facebook_url(value.strip())[1]

    @field_validator('page_name')
    @classmethod
    def clean_page_name(cls,value):
        if value is not None and re.search(r'[\x00-\x1f\x7f]',value):raise ValueError('Tên Page không hợp lệ.')
        return value.strip() or None if value is not None else None

    @field_validator('caption')
    @classmethod
    def valid_caption(cls,value):
        if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff]',value):raise ValueError('Caption chứa ký tự không hợp lệ.')
        return value.replace('\r\n','\n').replace('\r','\n')

    @field_validator('scheduled_at','scheduled_local',mode='before')
    @classmethod
    def datetime_input(cls,value):
        if value is not None and not isinstance(value,(str,datetime)):raise ValueError('Dùng ngày giờ ISO hợp lệ.')
        return value

    @field_validator('scheduled_at')
    @classmethod
    def aware_utc(cls,value):
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:raise ValueError('Giờ đăng UTC phải có múi giờ.')
            return value.astimezone(utc_timezone.utc)
        return None

    @field_validator('scheduled_local')
    @classmethod
    def naive_local(cls,value):
        if value is not None and value.tzinfo is not None:raise ValueError('Giờ địa phương không kèm UTC offset; chọn múi giờ riêng.')
        return value

    @field_validator('scheduled_local',mode='before')
    @classmethod
    def local_has_time(cls,value):
        if isinstance(value,str) and not re.search(r'[T ]\d{2}:\d{2}',value):
            raise ValueError('Giờ địa phương phải có cả ngày và giờ theo ISO.')
        return value

    @field_validator('timezone')
    @classmethod
    def timezone_shape(cls,value):
        value=value.strip()
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+.-]+)*',value) or any(part in ('.','..') for part in value.split('/')):
            raise ValueError('Tên múi giờ không hợp lệ.')
        return value

    @model_validator(mode='after')
    def exactly_one_schedule(self):
        if (self.scheduled_at is None)==(self.scheduled_local is None):
            raise ValueError('Chọn đúng một giờ UTC hoặc giờ địa phương để lên lịch.')
        return self

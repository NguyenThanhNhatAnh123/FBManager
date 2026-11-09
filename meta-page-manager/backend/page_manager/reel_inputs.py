"""Independent token Reel inputs and DST-safe local schedule conversion."""
from datetime import datetime, timezone
import re
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from .errors import AppError

DEFAULT_TIMEZONE='America/Los_Angeles'


def resolve_schedule(scheduled_local, timezone_name, fold=None):
    if fold is not None and (type(fold) is not int or fold not in (0,1)):
        raise AppError('Chọn lần xuất hiện đầu hoặc sau của giờ lặp.','invalid_fold',422)
    if not isinstance(scheduled_local,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?',scheduled_local):
        raise AppError('Chọn ngày và giờ đăng theo giờ địa phương.','invalid_schedule',422)
    try:
        local=datetime.fromisoformat(scheduled_local)
        if not isinstance(timezone_name,str) or len(timezone_name)>100:raise ValueError()
        zone=ZoneInfo(timezone_name)
    except (ValueError,ZoneInfoNotFoundError,TypeError):
        raise AppError('Ngày giờ hoặc múi giờ không hợp lệ.','invalid_schedule',422) from None
    candidates={}
    for candidate_fold in (0,1):
        instant=local.replace(tzinfo=zone,fold=candidate_fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None)==local:
            candidates.setdefault(instant,candidate_fold)
    if not candidates:raise AppError('Giờ này không tồn tại do chuyển giờ mùa hè. Chọn giờ khác.','nonexistent_time',422)
    if len(candidates)>1 and fold is None:
        raise AppError('Giờ này xuất hiện hai lần do chuyển giờ mùa hè. Chọn lần đầu hoặc lần sau.','ambiguous_time',422)
    chosen=next(iter(candidates)) if len(candidates)==1 else next(value for value,which in candidates.items() if which==fold)
    return {'scheduled_at':chosen.isoformat(),'scheduled_local':local.isoformat(timespec='seconds'),
            'timezone':timezone_name,'fold':candidates[chosen]}


class ReviewBatchInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(min_length=36,max_length=36)
    expected_count:StrictInt=Field(ge=2,le=20)

    @field_validator('id')
    @classmethod
    def canonical_id(cls,value):
        parsed=UUID(value)
        if str(parsed)!=value or parsed.int==0:raise ValueError('Mã nhóm duyệt không hợp lệ.')
        return value


class TokenReelInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_id:UUID
    page_id:str=Field(pattern=r'^[0-9]{1,32}$')
    connection_id:StrictInt=Field(gt=0)
    asset_id:str=Field(pattern=r'^[a-f0-9]{32}$')
    caption:str=Field(default='',max_length=2200)
    scheduled_local:str|None=Field(default=None,max_length=32)
    timezone:str=Field(default=DEFAULT_TIMEZONE,min_length=1,max_length=100)
    fold:StrictInt|None=Field(default=None,ge=0,le=1)
    publish_mode:Literal['scheduled','on_approval']='scheduled'
    approve_by:str|None=Field(default=None,min_length=1,max_length=80)
    review_batch:ReviewBatchInput|None=None
    approval_policy:Literal['remind_until_decision_v1']|None='remind_until_decision_v1'

    @field_validator('approve_by')
    @classmethod
    def approval_deadline(cls,value):
        if value is None:return None
        instant=datetime.fromisoformat(value.replace('Z','+00:00'))
        if instant.tzinfo is None or instant.utcoffset() is None:raise ValueError('Hạn duyệt phải có múi giờ.')
        return instant.astimezone(timezone.utc).isoformat()

    @model_validator(mode='after')
    def mode_fields(self):
        if self.publish_mode=='on_approval':
            if self.scheduled_local is not None or self.fold is not None or self.approval_policy is None and self.approve_by is None:
                raise ValueError('Đăng sau khi duyệt không kèm giờ hẹn hoặc fold; chế độ hạn cũ cần hạn duyệt riêng.')
            # Preserve the raw cutoff until the API can distinguish a replay
            # of an old request from a new contradictory request.
            if 'approval_policy' in self.model_fields_set and self.approval_policy is not None and self.approve_by is not None:
                raise ValueError('Chế độ nhắc đến khi quyết định không đặt hạn duyệt.')
        elif self.scheduled_local is None or self.approve_by is not None:
            raise ValueError('Đặt lịch cần ngày giờ địa phương; không kèm hạn duyệt riêng.')
        return self

    @field_validator('caption')
    @classmethod
    def clean_caption(cls,value):
        if any(ord(char)<32 and char not in '\n\r\t' or ord(char)==127 or 0xD800<=ord(char)<=0xDFFF for char in value):
            raise ValueError('Caption có ký tự điều khiển không hợp lệ.')
        return value

    def schedule(self,*,now=None):
        if self.publish_mode=='scheduled':return resolve_schedule(self.scheduled_local,self.timezone,self.fold)
        instant=now or datetime.now(timezone.utc)
        try:
            if instant.tzinfo is None or instant.utcoffset() is None:raise ValueError()
            local=instant.astimezone(ZoneInfo(self.timezone))
        except (ValueError,ZoneInfoNotFoundError,TypeError):
            raise AppError('Múi giờ đăng chưa hợp lệ.','invalid_schedule',422) from None
        return {'scheduled_at':instant.astimezone(timezone.utc).isoformat(),
                'scheduled_local':local.replace(tzinfo=None).isoformat(timespec='seconds'),
                'timezone':self.timezone,'fold':local.fold}

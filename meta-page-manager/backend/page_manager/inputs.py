import re
from typing import Literal
from uuid import UUID
from urllib.parse import urlsplit
from pydantic import BaseModel,ConfigDict,Field,SecretStr,field_validator,model_validator


class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')


class ConnectionInput(Strict):
    label:str=Field(min_length=1,max_length=100)
    token_type:Literal['user','system_user','page']
    token:SecretStr
    app_id:str|None=None
    business_id:str|None=None

    @field_validator('label')
    @classmethod
    def label_valid(cls,value):
        if not value.strip() or re.search(r'[\x00-\x1f\x7f]',value):raise ValueError('Tên kết nối không hợp lệ.')
        return value.strip()

    @field_validator('app_id','business_id')
    @classmethod
    def numeric_id(cls,value):
        if value in (None,''):return None
        if not re.fullmatch(r'[0-9]{1,32}',value):raise ValueError('ID Meta phải là dãy số.')
        return value

    @field_validator('token')
    @classmethod
    def token_valid(cls,value):
        if value is None:return None
        raw=value.get_secret_value().strip()
        if '|' in raw:raise ValueError('App token không dùng để quản lý Page. Hãy nhập User, System User hoặc Page access token.')
        if not 8<=len(raw)<=8192 or re.search(r'[\s\x00-\x1f\x7f]',raw):raise ValueError('Access token không hợp lệ.')
        return SecretStr(raw)


class ConnectionUpdate(ConnectionInput):
    token:SecretStr|None=None
    revision:int=Field(gt=0,strict=True)


class PageRefresh(Strict):
    connection_id:int|None=Field(default=None,gt=0)
    include_posts:bool=True
    limit:int=Field(default=25,ge=1,le=25,strict=True)


class PageEditSource(Strict):
    connection_id:int=Field(gt=0,strict=True)


class PageLocation(Strict):
    street:str|None=Field(default=None,max_length=200)
    city:str|None=Field(default=None,max_length=100)
    state:str|None=Field(default=None,max_length=100)
    country:str|None=Field(default=None,max_length=100)
    zip:str|None=Field(default=None,max_length=32)

    @field_validator('*')
    @classmethod
    def location_text(cls,value):
        if value is None:raise ValueError('Không dùng null để xóa địa chỉ.')
        if re.search(r'[\x00-\x1f\x7f]',value):raise ValueError('Địa chỉ không hợp lệ.')
        return value.strip()

    @model_validator(mode='after')
    def has_fields(self):
        if not self.model_fields_set:raise ValueError('Nhập ít nhất một trường địa chỉ.')
        return self


class PageEdit(PageEditSource):
    request_id:UUID
    bio:str|None=Field(default=None,max_length=101)
    location:PageLocation|None=None
    category_ids:list[str]|None=Field(default=None,min_length=1,max_length=3)
    website:str|None=Field(default=None,max_length=2048)
    is_always_open:bool|None=Field(default=None,strict=True)

    @field_validator('bio')
    @classmethod
    def bio_text(cls,value):
        if value is None or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]',value):raise ValueError('Tiểu sử không hợp lệ.')
        return value.strip()

    @field_validator('location','category_ids','is_always_open')
    @classmethod
    def explicit_value(cls,value):
        if value is None:raise ValueError('Không dùng null để xóa thông tin Page.')
        return value

    @field_validator('website')
    @classmethod
    def website_url(cls,value):
        if value is None or re.search(r'[\x00-\x1f\x7f\ud800-\udfff]',value):raise ValueError('Website không hợp lệ.')
        value=value.strip()
        if re.search(r'\s',value):raise ValueError('Website không hợp lệ.')
        if value=='':return value
        try:
            parsed=urlsplit(value)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.port not in (None,80,443):raise ValueError()
        except ValueError:raise ValueError('Website phải là địa chỉ HTTP hoặc HTTPS, không kèm thông tin đăng nhập.') from None
        return value

    @field_validator('category_ids')
    @classmethod
    def categories(cls,values):
        if any(not re.fullmatch(r'[0-9]{1,32}',value) for value in values) or len(set(values))!=len(values):
            raise ValueError('Danh mục phải là ID Meta khác nhau.')
        return values

    @model_validator(mode='after')
    def has_changes(self):
        if not self.model_fields_set.intersection({'bio','location','category_ids','website','is_always_open'}):raise ValueError('Chọn thông tin cần thay đổi.')
        return self

    def graph_fields(self):
        values=self.model_dump(include={'bio','location','category_ids','website','is_always_open'},exclude_unset=True)
        if 'category_ids' in values:values['category_list']=values.pop('category_ids')
        return values

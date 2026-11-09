import re
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

def origin(url: str) -> str:
    try:
        p = urlsplit(url)
        if p.scheme not in ('http', 'https') or not p.hostname or p.username or p.password:
            raise ValueError()
        port = p.port
        host = p.hostname.lower()
        if ':' in host:
            host = f'[{host}]'
        return f'{p.scheme}://{host}' + (f':{port}' if port and port != (443 if p.scheme == 'https' else 80) else '')
    except (ValueError, TypeError):
        raise ValueError('Use an http(s) URL without embedded credentials.') from None

class BmPortfolioInput(Strict):
    bm_id: str = Field(pattern=r'^[1-9][0-9]{0,29}$')
    name: str = Field(default='', max_length=120)
    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        if re.search(r'[\x00-\x1f]',value):raise ValueError('Tên BM không hợp lệ.')
        return value.strip()

class BmProfileInput(Strict):
    portfolios: list[BmPortfolioInput] = Field(default_factory=list, max_length=1000)
    note: str = Field(default='', max_length=4000)
    @model_validator(mode='after')
    def unique_business_ids(self):
        ids=[value.bm_id for value in self.portfolios]
        if len(ids)!=len(set(ids)):raise ValueError('BM ID bị trùng trong profile.')
        self.note=self.note.strip()
        return self

class BmOpenInput(Strict):
    bm_id: str | None = Field(default=None, pattern=r'^[1-9][0-9]{0,29}$')

class BmMobileInput(Strict):
    enabled: bool

class BmProxyInput(Strict):
    content: str = Field(min_length=1,max_length=4096)
    default_protocol: Literal['http','https','socks5'] = 'http'
    check_after_save: bool = True

class ProfileInput(Strict):
    revision: int | None = Field(default=None, strict=True, ge=1, exclude=True)
    name: str = Field(min_length=1, max_length=80)
    proxy_id: int | None = Field(default=None, gt=0)
    bm: BmProfileInput | None = None
    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip() or re.search(r'[\x00-\x1f]', value):
            raise ValueError('Invalid profile name.')
        return value.strip()

class BulkCreate(Strict):
    count: int = Field(default=100, ge=1, le=1000)

class Ids(Strict):
    profile_ids: list[int] = Field(min_length=1, max_length=1000)
    @field_validator('profile_ids')
    @classmethod
    def unique_ids(cls, values):
        if any(v <= 0 for v in values):
            raise ValueError('IDs must be positive.')
        return list(dict.fromkeys(values))

class ProxyImport(Strict):
    content: str = Field(min_length=1, max_length=2_000_000)
    assign: bool = True
    default_protocol: Literal['http', 'https', 'socks5'] = 'http'
    check_after_import: bool = False

class ProxyCheck(Strict):
    proxy_ids: list[int] | None = Field(default=None, max_length=1000)

class Settings(Strict):
    revision: int | None = Field(default=None, strict=True, ge=1, exclude=True)
    max_browsers: int = Field(default=5, ge=1, le=100)
    max_memory_percent: int = Field(default=85, ge=20, le=98)
    max_cpu_percent: int = Field(default=95, ge=20, le=100)
    min_available_memory_mb: int = Field(default=1024, ge=128, le=131072)
    action_delay_ms: int = Field(default=1000, ge=500, le=60000)
    launch_timeout_seconds: int = Field(default=45, ge=10, le=180)
    task_timeout_seconds: int = Field(default=300, ge=10, le=3600)
    require_proxy: bool = True
    facebook_auto_refresh: bool = True
    proxy_ip_version: Literal['auto', 'ipv4', 'ipv6'] = 'auto'
    proxy_routing: Literal['all', 'facebook_only'] = 'all'
    allowed_origins: list[str] = Field(default_factory=lambda: ['https://example.com'], max_length=200)
    @field_validator('allowed_origins')
    @classmethod
    def valid_origins(cls, values):
        return list(dict.fromkeys(origin(v) for v in values))

class Action(Strict):
    type: Literal['open_url', 'wait_load', 'read_title', 'screenshot', 'click', 'fill', 'read_text', 'wait_selector', 'scroll', 'close_tab']
    url: str | None = Field(default=None, max_length=2048)
    selector: str | None = Field(default=None, max_length=500)
    value: str | None = Field(default=None, max_length=10000)
    pixels: int = Field(default=700, ge=-10000, le=10000)
    timeout_ms: int = Field(default=15000, ge=100, le=60000)
    @model_validator(mode='after')
    def validate_action(self):
        if self.type == 'open_url':
            if not self.url:
                raise ValueError('Open URL needs a URL.')
            origin(self.url)
        if self.type in ('click', 'fill', 'read_text', 'wait_selector') and not self.selector:
            raise ValueError('This action needs a selector.')
        if self.type == 'fill' and self.value is None:
            raise ValueError('Fill needs a value.')
        return self

class TaskInput(Strict):
    name: str = Field(min_length=1, max_length=120)
    authorized: bool
    actions: list[Action] = Field(min_length=1, max_length=50)
    close_browser: bool = True
    @model_validator(mode='after')
    def valid_task(self):
        if not self.authorized:
            raise ValueError('Confirm ownership or permission to test the sites.')
        if self.actions[0].type != 'open_url':
            raise ValueError('The first action must open a URL.')
        return self

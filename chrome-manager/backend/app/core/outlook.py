"""Inputs for assisted Outlook sign-in. Passwords and codes never enter SQLite."""
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit
from pydantic import Field, SecretStr, field_validator, model_validator
from app.core.schemas import Strict


def email_address(value):
    value = value.strip()
    if len(value) > 254 or not re.fullmatch(r'[^\s@|<>]+@(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,63}', value):
        raise ValueError('Email không hợp lệ.')
    return value


def reader_url(value):
    if not value:
        return ''
    if re.search(r'[\x00-\x20]', value):
        raise ValueError('Link web lấy mã không hợp lệ.')
    try:
        url = urlsplit(value.replace('{email}', 'mailbox'))
        host = (url.hostname or '').lower().rstrip('.')
        if url.scheme != 'https' or not host or url.username or url.password or url.port not in (None,443):
            raise ValueError()
        if host in ('localhost','localhost.localdomain') or host.endswith(('.localhost','.local','.internal')) or '.' not in host:
            raise ValueError()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Dùng link HTTPS tới website đọc mail, không dùng địa chỉ nội bộ hoặc link chứa username/password.') from None
    return value


READERS_PATH = Path(__file__).resolve().parents[3] / 'shared' / 'recovery_readers.json'


def reader_provider(email):
    domain = email.strip().rsplit('@',1)[-1].lower()
    return next((provider for provider in json.loads(READERS_PATH.read_text(encoding='utf-8'))
                 if domain in provider['domains']), None)


def suggested_reader(email):
    provider = reader_provider(email)
    return provider['url'] if provider else ''


class OutlookStart(Strict):
    profile_id: int = Field(gt=0)
    email: str
    password: SecretStr
    recovery_email: str
    recovery_url: str = Field(default='', max_length=2048)
    keep_signed_in: bool = True

    @field_validator('email','recovery_email')
    @classmethod
    def valid_email(cls,value):
        return email_address(value)

    @field_validator('password')
    @classmethod
    def valid_password(cls,value):
        if not 1 <= len(value.get_secret_value()) <= 2048:
            raise ValueError('Nhập mật khẩu Outlook.')
        return value

    @field_validator('recovery_url')
    @classmethod
    def valid_reader(cls,value):
        return reader_url(value.strip())

    @model_validator(mode='after')
    def complete_reader(self):
        if self.email.casefold() == self.recovery_email.casefold():
            raise ValueError('Email khôi phục phải khác email Outlook đang đăng nhập.')
        self.recovery_url = self.recovery_url or suggested_reader(self.recovery_email)
        return self

    def inbox_url(self):
        return self.recovery_url.replace('{email}',quote(self.recovery_email,safe=''))


class OutlookCode(Strict):
    code: SecretStr

    @field_validator('code')
    @classmethod
    def valid_code(cls,value):
        if not re.fullmatch(r'[0-9]{6,8}',value.get_secret_value().strip()):
            raise ValueError('Mã cần có 6–8 chữ số.')
        return SecretStr(value.get_secret_value().strip())

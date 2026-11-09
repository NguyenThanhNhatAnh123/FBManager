"""Validated recipient workflow inputs. Codes/passwords are never persisted."""
from pathlib import Path
import re
from pydantic import Field,field_validator,model_validator
from app.core.schemas import Strict
from app.core.facebook import facebook_url
from app.core.outlook import email_address


class PageAddress(Strict):
    street:str=Field(default='',max_length=500)
    city:str=Field(default='',max_length=160)
    postal_code:str=Field(default='',max_length=32)
    country:str=Field(default='',max_length=120)
    @field_validator('street','city','postal_code','country')
    @classmethod
    def trim(cls,value):return value.strip()


class PageSetup(Strict):
    avatar_path:str|None=Field(default=None,max_length=4096)
    cover_path:str|None=Field(default=None,max_length=4096)
    bio:str|None=Field(default=None,max_length=1000)
    address:PageAddress|None=None
    categories:list[str]=Field(default_factory=list,max_length=10)
    @field_validator('avatar_path','cover_path','bio')
    @classmethod
    def trim_optional(cls,value):return value.strip() or None if value is not None else None
    @field_validator('categories')
    @classmethod
    def clean_categories(cls,values):
        values=list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value)>120 or re.search(r'[\x00-\x1f]',value) for value in values):
            raise ValueError('Danh mục không hợp lệ.')
        return values
    def requested(self):
        result={key:value for key,value in self.model_dump(exclude_none=True).items() if value}
        if 'address' in result and not any(result['address'].values()):result.pop('address')
        return result


class PageRecipientStart(Strict):
    profile_id:int=Field(gt=0)
    expected_page_url:str|None=Field(default=None,max_length=2048)
    expected_page_name:str|None=Field(default=None,max_length=160)
    outlook_email:str
    code_wait_seconds:int=Field(default=300,ge=30,le=300)
    setup:PageSetup=Field(default_factory=PageSetup)
    accept_only:bool=False
    @field_validator('outlook_email')
    @classmethod
    def valid_mail(cls,value):return email_address(value)
    @field_validator('expected_page_url')
    @classmethod
    def page_url(cls,value):return facebook_url(value.strip())[1] if value and value.strip() else None
    @field_validator('expected_page_name')
    @classmethod
    def page_name(cls,value):return value.strip() or None if value is not None else None
    @model_validator(mode='after')
    def explicit_setup(self):
        if not self.setup.requested():self.accept_only=True
        return self


def asset_path(value,root):
    source=Path(value)
    path=source.resolve()
    source_root=Path(root)
    root=source_root.resolve()
    if source_root.is_symlink() or source_root.is_junction() or source.is_symlink() or path==root or not path.is_relative_to(root) or not path.is_file():
        raise ValueError('Ảnh phải là file có sẵn trong thư mục page-assets của ứng dụng.')
    if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp') or not 0<path.stat().st_size<=25*1024*1024:
        raise ValueError('Dùng ảnh PNG, JPG hoặc WebP không quá 25 MB.')
    with path.open('rb') as stream:head=stream.read(16)
    if not (head.startswith(b'\x89PNG\r\n\x1a\n') or head.startswith(b'\xff\xd8\xff') or head[:4]==b'RIFF' and head[8:12]==b'WEBP'):
        raise ValueError('File ảnh không có định dạng hợp lệ.')
    return path

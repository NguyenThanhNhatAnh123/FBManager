"""Defence in depth after per-tool explicit projections."""
import re

BLOCKED={'token','access_token','page_access_token','encrypted_token','encrypted_page_token','encrypted_credentials',
         'password','secret','app_secret','client_secret','authorization','cookie','cookies','csrf_token',
         'profile_path','path','asset_root','user_data_dir','recovery_email','recovery_password','proxy_username','proxy_password'}


def protect(value,depth=0):
    if depth>20:return '[giới hạn dữ liệu]'
    if isinstance(value,dict):
        return {str(key):protect(item,depth+1) for key,item in list(value.items())[:1000]
                if str(key).lower() not in BLOCKED and not any(part in str(key).lower() for part in ('encrypted_','password','secret','access_token'))}
    if isinstance(value,list):return [protect(item,depth+1) for item in value[:1000]]
    if isinstance(value,str):
        text=re.sub(r'\b[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[email đã ẩn]',value)
        text=re.sub(r'\bEAA[A-Za-z0-9_-]{20,}\b','[token đã ẩn]',text)
        text=re.sub(r'(?<![A-Za-z0-9_-])via_pat_[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])','[token đã ẩn]',text)
        text=re.sub(r'(?i)\b(Bearer|OAuth)\s+[^\s"<>]+',r'\1 [đã ẩn]',text)
        text=re.sub(r'(?i)(https?://)[^/@\s]+:[^/@\s]+@',r'\1[đã ẩn]@',text)
        return ''.join(char for char in text if not 0xD800<=ord(char)<=0xDFFF)[:24000]
    return value if value is None or type(value) in (int,float,bool) else str(value)[:200]

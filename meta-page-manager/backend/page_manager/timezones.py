"""IANA dates via tzdata, with an independent trusted Node Intl fallback."""
from datetime import datetime,timezone,timedelta,time
import json
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError
from .errors import AppError


def parse_time(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError,ValueError,AttributeError):return None


def date_keys(values,zone):
    if not isinstance(zone,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_+.-]*(?:/[A-Za-z0-9_+.-]+)*',zone) or len(zone)>120:raise AppError('Múi giờ IANA không hợp lệ.','invalid_timezone')
    parsed=[parse_time(value) for value in values]
    try:
        selected=ZoneInfo(zone)
        return [value.astimezone(selected).date().isoformat() if value else None for value in parsed]
    except ZoneInfoNotFoundError:pass
    node=shutil.which('node')
    if not node:raise AppError('Cài tzdata để dùng múi giờ IANA này.','timezone_unavailable')
    script="const fs=require('fs');const x=JSON.parse(fs.readFileSync(0,'utf8'));const f=new Intl.DateTimeFormat('en-CA',{timeZone:x.zone,year:'numeric',month:'2-digit',day:'2-digit'});process.stdout.write(JSON.stringify(x.values.map(v=>v?f.format(new Date(v)):null)));"
    try:
        result=subprocess.run([node,'-e',script],input=json.dumps({'zone':zone,'values':[value.isoformat() if value else None for value in parsed]}),text=True,capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output=json.loads(result.stdout) if result.returncode==0 else None
        if not isinstance(output,list) or len(output)!=len(values):raise ValueError()
        if any(value is not None and not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value) for value in output):raise ValueError()
        return output
    except Exception:raise AppError('Múi giờ IANA không hợp lệ hoặc chưa sẵn sàng.','invalid_timezone') from None


def day_bounds(value, zone):
    """UTC half-open bounds for one IANA local day, including DST transitions."""
    local_date = date_keys([value], zone)[0]
    if local_date is None:raise AppError('Thời điểm tính ngày không hợp lệ.','invalid_timezone')
    date = datetime.fromisoformat(local_date).date()
    try:
        selected = ZoneInfo(zone)
        return (datetime.combine(date, time.min, selected).astimezone(timezone.utc).isoformat(),
                datetime.combine(date + timedelta(days=1), time.min, selected).astimezone(timezone.utc).isoformat())
    except ZoneInfoNotFoundError:pass
    node = shutil.which('node')
    if not node:raise AppError('Cài tzdata để dùng múi giờ IANA này.','timezone_unavailable')
    script = "const fs=require('fs');const x=JSON.parse(fs.readFileSync(0,'utf8'));const f=new Intl.DateTimeFormat('en-CA',{timeZone:x.zone,year:'numeric',month:'2-digit',day:'2-digit'});const key=t=>{const p=f.formatToParts(new Date(t));return ['year','month','day'].map(k=>p.find(v=>v.type===k).value).join('-')};const start=s=>{const c=Date.parse(s+'T12:00:00Z');let l=c-172800000,r=c+172800000;while(r-l>1){const m=Math.floor((l+r)/2);if(key(m)<s)l=m;else r=m}return new Date(r).toISOString()};process.stdout.write(JSON.stringify([start(x.day),start(x.next)]));"
    try:
        result = subprocess.run([node, '-e', script], input=json.dumps({'zone':zone,'day':local_date,'next':(date+timedelta(days=1)).isoformat()}),text=True,capture_output=True,timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        output = json.loads(result.stdout) if result.returncode == 0 else None
        if not isinstance(output,list) or len(output)!=2 or any(parse_time(item) is None for item in output):raise ValueError()
        return tuple(output)
    except Exception:raise AppError('Múi giờ IANA không hợp lệ hoặc chưa sẵn sàng.','invalid_timezone') from None

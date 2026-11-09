"""Use the existing Node/ICU runtime to resolve IANA wall-clock times on Windows."""
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess

SCRIPT=Path(__file__).resolve().parents[3]/'scripts'/'resolve_reel_time.cjs'
ERRORS={
    'invalid_local':'Ngày hoặc giờ đăng không hợp lệ.',
    'invalid_timezone':'Múi giờ không hợp lệ.',
    'nonexistent_local':'Giờ này không tồn tại do đổi giờ mùa hè. Chọn giờ khác.',
    'ambiguous_local':'Giờ này xuất hiện hai lần do đổi giờ mùa hè. Chọn lần thứ nhất hoặc thứ hai.',
}

def _resolve(payload):
    node=shutil.which('node.exe') or shutil.which('node')
    if not node:raise ValueError('Cần Node.js đã cấu hình trong start.bat để tính giờ đăng.')
    try:
        result=subprocess.run([node,str(SCRIPT)],input=json.dumps(payload),text=True,capture_output=True,
                              timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=json.loads(result.stdout)
    except Exception:raise ValueError('Chưa tính được giờ đăng. Kiểm tra Node.js trong ứng dụng.') from None
    if result.returncode or value.get('error'):raise ValueError(ERRORS.get(value.get('error'),'Chưa tính được giờ đăng.'))
    return value

def resolve_schedule(body):
    zone=body.timezone or 'UTC'
    if body.scheduled_at is not None:
        normalized=_resolve({'timezone':zone,'validate_only':True})['timezone']
        stamp=body.scheduled_at
        if not isinstance(stamp,datetime):stamp=datetime.fromisoformat(stamp.replace('Z','+00:00'))
    else:
        local=body.scheduled_local.isoformat(timespec='seconds') if isinstance(body.scheduled_local,datetime) else body.scheduled_local
        value=_resolve({'timezone':zone,'local':local,'fold':body.fold})
        stamp=datetime.fromisoformat(value['scheduled_at'].replace('Z','+00:00'));normalized=value['timezone']
    if stamp.tzinfo is None:raise ValueError('Giờ đăng cần có múi giờ.')
    return stamp.astimezone(timezone.utc),normalized

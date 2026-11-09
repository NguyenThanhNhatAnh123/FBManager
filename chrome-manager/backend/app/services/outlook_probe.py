"""Read-only Outlook extraction probe for an existing recipient checkpoint."""
import asyncio
from datetime import datetime
import json
import math
from pathlib import Path
import re
import time

from app.core.page_recipient import PageRecipientStart
from app.models.entities import BmProfile,Profile
from app.services.outlook import OUTLOOK_URL
from app.services.outlook_inbox import DIAGNOSTIC_FLAGS,OutlookInboxReader,OutlookInboxNeedsLogin,OutlookInboxWrongAccount


STRUCTURE_PATH=Path(__file__).with_name('outlook_inbox_structure.js')
STRUCTURE_ROLES=('listbox','grid','list','option','row','treeitem','document','main')
STRUCTURE_CANDIDATES=('reading_pane_id','reading_pane_testid','message_reading_pane_testid','mail_read_compose','reading_pane_aria','message_pane_aria','message_list_aria','message_list_testid','message_body_aria','message_body_testid','navigation','tree','item_id','conversation_id','iframe','folder_inbox_label_contains','folder_inbox_label_match','folder_junk_label_contains','folder_junk_label_match')
STRUCTURE_WORDS=set('reading read pane container id mail message messages list folder folders main root view content body region panel scroll group section document row grid item items tree nav navigation wrapper header focus focused other junk search host app component div inbox layout slot surface details control display thread conversation threaded element area'.split())
STRUCTURE_TAGS=('div','main','section','article','nav','header','aside','ul','ol','li','iframe','form','input','button','a','span','table','tr','td','tbody','svg','script','style','other')


def _id_shape(value):
    if not isinstance(value,str):return ''
    value=re.sub(r'([A-Z]+)([A-Z][a-z])',r'\1-\2',value)
    value=re.sub(r'([a-z])([A-Z])',r'\1-\2',value)
    parts=re.findall(r'[A-Za-z]+|[0-9]+|#',value)[:20]
    return '-'.join('#' if part.isdigit() or part=='#' else part.lower() if part.lower() in STRUCTURE_WORDS else '[redacted]' for part in parts)


def _count(value):
    return max(0,min(10000,value)) if type(value) is int else 0


def _structure_safe(data):
    if not isinstance(data,dict):return None
    roles=data.get('roles') if isinstance(data.get('roles'),dict) else {}
    candidates=data.get('candidates') if isinstance(data.get('candidates'),dict) else {}
    ids=[]
    for row in (data.get('visible_id_shapes') or [])[:150]:
        if isinstance(row,dict):ids.append({'id_shape':_id_shape(row.get('id_shape')),'count':_count(row.get('count'))})
    frames=[]
    for row in (data.get('iframes') or [])[:20]:
        if not isinstance(row,dict):continue
        hostname=row.get('origin_hostname')
        hostname=hostname.lower() if isinstance(hostname,str) and re.fullmatch(r'[A-Za-z0-9.-]{1,253}',hostname) else ''
        # Host-only metadata must not carry numeric IDs or opaque subdomain keys.
        if hostname:
            hostname='.'.join('[redacted]' if len(label)>32 or re.search(r'[0-9]{6,}',label) else label for label in hostname.split('.'))
        frame_roles=row.get('roles') if isinstance(row.get('roles'),dict) else {}
        frame_tags=row.get('body_children') if isinstance(row.get('body_children'),dict) else {}
        frame_shadows=row.get('shadow_roles') if isinstance(row.get('shadow_roles'),dict) else {}
        frames.append({'id_shape':_id_shape(row.get('id_shape')),'origin_hostname':hostname,'readable':row.get('readable') is True,
                       'roles':{key:_count(frame_roles.get(key)) for key in STRUCTURE_ROLES},
                       'body_children':{key:_count(frame_tags.get(key)) for key in STRUCTURE_TAGS if key in frame_tags},
                       'open_shadow_root_count':_count(row.get('open_shadow_root_count')),
                       'shadow_roles':{key:_count(frame_shadows.get(key)) for key in STRUCTURE_ROLES}})
    tags=data.get('body_children') if isinstance(data.get('body_children'),dict) else {}
    shadows=data.get('shadow_roles') if isinstance(data.get('shadow_roles'),dict) else {}
    return {'version':1,'roles':{key:_count(roles.get(key)) for key in STRUCTURE_ROLES},
            'candidates':{key:_count(candidates.get(key)) for key in STRUCTURE_CANDIDATES},'visible_id_shapes':ids,'iframes':frames,
            'body_children':{key:_count(tags.get(key)) for key in STRUCTURE_TAGS if key in tags},
            'open_shadow_root_count':_count(data.get('open_shadow_root_count')),
            'shadow_roles':{key:_count(shadows.get(key)) for key in STRUCTURE_ROLES}}


async def capture_outlook_structure(manager,pid,page):
    """Save bounded structural evidence only, never message contents."""
    try:
        data=_structure_safe(await page.evaluate(STRUCTURE_PATH.read_text(encoding='utf-8')))
        if data is None:return False
        root=Path(manager.config.root).resolve()
        folder=root/'.runtime'/'outlook-inbox'
        target=folder/f'structure_{int(pid)}.json'
        if not folder.resolve().is_relative_to(root) or folder.is_symlink() or folder.is_junction() or target.is_symlink():return False
        folder.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return True
    except Exception:
        return False  # Structural diagnostics cannot change the probe outcome.


def _empty_flags():
    return dict.fromkeys(DIAGNOSTIC_FLAGS,False)


async def probe_outlook(manager,pid,timeout_seconds=30):
    """Return booleans only; never alter a recipient job or submit a code."""
    browsers=manager.browsers
    busy_sets=(manager.jobs,manager.tasks.profile_jobs,browsers.recipient_profiles,
               browsers.extension_profiles,browsers.facebook_profiles,browsers.outlook_profiles)
    if browsers.status(pid)!='Stopped' or any(pid in values for values in busy_sets):
        raise ValueError('Dừng Chrome và các tác vụ của profile trước khi kiểm tra đọc mã Outlook.')
    with manager.db.session() as session:
        if session.get(Profile,pid) is None:raise ValueError('Profile không tồn tại.')
        if session.get(BmProfile,pid) is not None:raise ValueError('Chỉ kiểm tra hộp thư của profile nhận Page thông thường.')
    row=manager.row(pid)
    if row is None:raise ValueError('Profile chưa có cấu hình nhận Page để kiểm tra Outlook.')
    try:
        body=PageRecipientStart.model_validate_json(row.configuration)
        checkpoint=json.loads(row.checkpoint)
        since=datetime.fromisoformat(checkpoint['code_requested_at'].replace('Z','+00:00'))
        if body.profile_id!=pid or since.tzinfo is None:raise ValueError()
    except Exception:
        raise ValueError('Cần cấu hình email và mốc yêu cầu mã đã lưu trước khi kiểm tra Outlook.') from None
    try:budget=float(timeout_seconds)
    except (TypeError,ValueError):raise ValueError('Thời gian kiểm tra Outlook không hợp lệ.') from None
    if not math.isfinite(budget) or budget<=0:raise ValueError('Thời gian kiểm tra Outlook không hợp lệ.')
    budget=min(30,budget)
    browsers.validate_automation_start(pid,for_recipient=True)
    reader=page=None
    result={'found':False,'message':'Chưa tự đọc được thư mã phù hợp trong thời gian kiểm tra. Xem các cờ chẩn đoán.','diagnostics':_empty_flags()}
    browsers.recipient_profiles.add(pid)
    try:
        deadline=time.monotonic()+budget
        async with asyncio.timeout(budget):
            context=await browsers.ensure_open(pid,for_automation=True,for_recipient=True)
            page=await context.new_page()
            reader=OutlookInboxReader(page,body.outlook_email,require_account=False)
            await page.goto(OUTLOOK_URL,wait_until='commit',timeout=max(1,(deadline-time.monotonic())*1000))
            await reader.wait_for_code(since,timeout_seconds=max(0,deadline-time.monotonic()))
            result['found']=True
            result['message']='Đã tự đọc được thư mã khớp người nhận và thời điểm yêu cầu. Không hiển thị hoặc lưu mã.'
    except OutlookInboxWrongAccount:
        result['message']='Outlook đang hiển thị hộp thư khác. Kiểm tra đúng tài khoản rồi thử lại.'
    except OutlookInboxNeedsLogin:
        result['message']='Outlook chưa sẵn sàng hoặc cần đăng nhập đúng hộp thư để kiểm tra.'
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        raise
    except Exception:
        result['message']='Không hoàn tất kiểm tra Outlook. Kiểm tra kết nối và phiên Chrome rồi thử lại.'
    finally:
        if reader is not None:
            flags=getattr(reader,'diagnostics',{})
            result['diagnostics']={key:bool(flags.get(key,False)) for key in DIAGNOSTIC_FLAGS}
        if not result['found'] and page is not None:
            await capture_outlook_structure(manager,pid,page)
        try:await browsers.stop(pid)
        except Exception:
            result['message']='Kiểm tra Outlook đã kết thúc nhưng Chrome chưa đóng xong. Hãy dừng profile.'
        finally:browsers.recipient_profiles.discard(pid)
    return result

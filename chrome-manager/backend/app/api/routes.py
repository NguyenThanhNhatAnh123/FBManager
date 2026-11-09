import json
import re
import shutil
import uuid
import asyncio
import psutil
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from filelock import FileLock, Timeout
from app.automation.manager import encode_task, decode_task, ACTIVE
from app.core.schemas import ProfileInput, BulkCreate, Ids, ProxyImport, ProxyCheck, Settings, TaskInput, BmProfileInput, BmOpenInput, BmProxyInput, BmMobileInput
from app.models.entities import Profile, Proxy, Task, TaskRun, Event, BmProfile, BmProxy
from app.services.proxies import public_proxy
from app.services.facebook import public_facebook
from app.core.facebook import FacebookCheck
from app.core.outlook import OutlookStart, OutlookCode
from app.core.page_recipient import PageRecipientStart
from app.core.reel_inputs import StartReel, VerifyReelLink
from app.services.reel_assets import ReelAssetTooLarge
from urllib.parse import unquote
from app.services.outlook import public_outlook, OUTLOOK_URL
from app.services.bm_profiles import public_bm_profiles,save_bm_profile,save_bm_mobile
from app.core.proxy_parser import parse_import
from app.core.security import encrypt_credentials,decrypt_credentials
from app.services.state import RevisionConflict
from app.services.reel_scheduler import ReelRequestConflict

router = APIRouter(prefix='/api')

def services(request):
    return request.app.state.services

def get_row(session, model, row_id):
    row = session.get(model, row_id)
    if row is None:
        raise HTTPException(404, 'Record not found.')
    return row


def assert_recipient_available(svc,profile_id):
    if profile_id in getattr(svc.browsers,'reel_profiles',set()):
        raise HTTPException(409,'Profile đang được lịch đăng Reel sử dụng. Dừng lượt đăng trước khi thực hiện thao tác này.')
    if profile_id in getattr(svc.browsers,'recipient_profiles',set()):
        raise HTTPException(409,'Dừng lượt nhận Page đang sử dụng profile trước khi thực hiện thao tác này.')


async def stop_recipient_if_present(svc,profile_id):
    if hasattr(svc,'reels'):await svc.reels.stop_profile(profile_id)
    recipient=getattr(svc,'recipient',None)
    if recipient is None:
        return
    row=getattr(recipient,'row',None)
    if (profile_id in getattr(svc.browsers,'recipient_profiles',set()) or
            profile_id in getattr(recipient,'jobs',{}) or callable(row) and row(profile_id) is not None):
        await recipient.stop(profile_id)

def serialize_run(run):
    return {k: getattr(run, k) for k in ('id', 'task_id', 'profile_id', 'profile_name', 'status', 'error_message', 'progress', 'created_at', 'started_at', 'finished_at')} | {'result': json.loads(run.result or '[]')}

def profile_rows(svc, session):
    proxies = {p.id: public_proxy(p)|{'checking':p.id in svc.proxies.checking} for p in session.scalars(select(Proxy))}
    facebook = public_facebook(session)
    outlook = public_outlook(session)
    bm = public_bm_profiles(session)
    latest = {}
    for run in session.scalars(select(TaskRun).order_by(TaskRun.id)):
        latest[run.profile_id] = {'id': run.id, 'status': run.status, 'progress': run.progress}
    return [{
        'id': p.id, 'name': p.name, 'profile_path': p.profile_path, 'proxy_id': p.proxy_id,
        'proxy': proxies.get(p.proxy_id), 'browser_status': svc.browsers.status(p.id),
        'browser_error': svc.browsers.errors.get(p.id), 'browser_mode': svc.browsers.mode(p.id), 'task': latest.get(p.id),
        'created_at': p.created_at, 'updated_at': p.updated_at, 'revision':p.revision,
        'facebook': facebook.get(p.id),
        'outlook': outlook.get(p.id),
        'bm': bm.get(p.id),
    } for p in session.scalars(select(Profile).order_by(Profile.id))]

def snapshot(svc):
    with svc.db.session() as session:
        profiles = profile_rows(svc, session)
        proxies = list(session.scalars(select(Proxy).where(Proxy.id.not_in(select(BmProxy.proxy_id)))))
        statuses = list(session.scalars(select(TaskRun.status).where(TaskRun.status.in_(ACTIVE))))
    return {'profiles': profiles, 'system': svc.resources.current | {
        'total_profiles': len(profiles), 'running_browsers': len(svc.browsers.contexts),
        'queued_browsers': len(svc.browsers.launches), 'starting_browsers': len(svc.browsers.reserved),
        'stopped_browsers': sum(p['browser_status'] in ('Stopped', 'Error') for p in profiles),
        'working_proxies': sum(p.status == 'Working' for p in proxies),
        'failed_proxies': sum(p.status in ('Failed', 'Timeout') for p in proxies),
        'total_proxies': len(proxies), 'running_tasks': statuses.count('Running'), 'pending_tasks': statuses.count('Pending'),
        'resource_warning': svc.resources.blocked_reason(svc.state.settings),
        'max_browsers': svc.state.settings.max_browsers, 'checking_proxies': sum(p.id in svc.proxies.checking for p in proxies),
        'proxy_routing': svc.state.settings.proxy_routing,
    }}


def system_snapshot(svc):
    """Aggregate status without constructing any profile/Page/post/run snapshots."""
    with svc.db.session() as session:
        total=session.scalar(select(func.count()).select_from(Profile)) or 0
        proxy_counts=dict(session.execute(select(Proxy.status,func.count()).where(Proxy.id.not_in(select(BmProxy.proxy_id))).group_by(Proxy.status)).all())
        task_counts=dict(session.execute(select(TaskRun.status,func.count()).where(TaskRun.status.in_(ACTIVE)).group_by(TaskRun.status)).all())
        proxy_count=sum(proxy_counts.values())
    busy_ids=set(svc.browsers.contexts)|set(svc.browsers.reserved)|set(svc.browsers.launches)
    return svc.resources.current|{'total_profiles':total,'running_browsers':len(svc.browsers.contexts),'queued_browsers':len(svc.browsers.launches),'starting_browsers':len(svc.browsers.reserved),
        'stopped_browsers':max(0,total-len(busy_ids)),'working_proxies':proxy_counts.get('Working',0),'failed_proxies':proxy_counts.get('Failed',0)+proxy_counts.get('Timeout',0),
        'total_proxies':proxy_count,'running_tasks':task_counts.get('Running',0),'pending_tasks':task_counts.get('Pending',0),
        'resource_warning':svc.resources.blocked_reason(svc.state.settings),'max_browsers':svc.state.settings.max_browsers,
        'checking_proxies':len(svc.proxies.checking),'proxy_routing':svc.state.settings.proxy_routing}

@router.get('/profiles')
async def list_profiles(request: Request):
    svc = services(request)
    with svc.db.session() as session:
        return profile_rows(svc, session)

@router.post('/profiles', status_code=201)
async def create_profile(body: ProfileInput, request: Request):
    svc = services(request)
    path = svc.config.profiles_dir / ('Profile_' + uuid.uuid4().hex[:12])
    if body.bm is not None and body.proxy_id:
        raise HTTPException(409,'Tạo profile BM trước, rồi thêm proxy riêng trong trang BM Profiles.')
    try:
        with svc.db.session.begin() as session:
            if body.proxy_id:
                get_row(session, Proxy, body.proxy_id)
                if session.get(BmProxy,body.proxy_id):
                    raise HTTPException(409,'Proxy BM riêng không dùng cho profile thường.')
            row = Profile(name=body.name, profile_path=str(path.resolve()), proxy_id=body.proxy_id)
            session.add(row)
            session.flush()
            path.mkdir(parents=True, exist_ok=False)
            row_id = row.id
            if body.bm is not None:
                save_bm_profile(session,row_id,body.bm)
    except IntegrityError:
        raise HTTPException(409, 'Profile name or assigned proxy is already in use.') from None
    svc.state.log('Profile created.', row_id)
    return {'id': row_id}

@router.post('/profiles/bulk-create')
async def bulk_create(body: BulkCreate, request: Request):
    svc = services(request)
    with svc.db.session.begin() as session:
        names = set(session.scalars(select(Profile.name)))
        paths = set(session.scalars(select(Profile.profile_path)))
        created = []
        for i in range(1, body.count + 1):
            name = f'Chrome_{i:03d}'
            path = str((svc.config.profiles_dir / name).resolve())
            if name in names or path in paths:
                continue
            Path(path).mkdir(parents=True, exist_ok=True)
            row = Profile(name=name, profile_path=path)
            session.add(row)
            session.flush()
            created.append(row.id)
        used = select(Profile.proxy_id).where(Profile.proxy_id != None)
        available = list(session.scalars(select(Proxy).where(Proxy.id.not_in(used),Proxy.id.not_in(select(BmProxy.proxy_id))).order_by(Proxy.id)))
        unassigned = list(session.scalars(select(Profile).where(Profile.proxy_id == None,Profile.id.not_in(select(BmProfile.profile_id))).order_by(Profile.id)))
        unassigned = [p for p in unassigned if not svc.proxies.is_busy(p.id)]
        for profile, proxy in zip(unassigned, available):
            profile.proxy_id = proxy.id
    svc.state.log(f'Created {len(created)} persistent profiles.')
    return {'created': len(created), 'ids': created}

@router.patch('/profiles/{profile_id}')
async def update_profile(profile_id: int, body: ProfileInput, request: Request):
    svc = services(request)
    assert_recipient_available(svc,profile_id)
    if svc.browsers.status(profile_id) in ('Running', 'Starting', 'Queued') or profile_id in svc.tasks.profile_jobs or profile_id in svc.browsers.extension_profiles or profile_id in svc.browsers.facebook_profiles or profile_id in svc.browsers.outlook_profiles:
        raise HTTPException(409, 'Stop this browser and its task before editing.')
    try:
        with svc.db.session.begin() as session:
            row = get_row(session, Profile, profile_id)
            if body.revision is None:raise HTTPException(428,'Tải lại profile để lấy revision trước khi lưu.')
            if body.revision!=row.revision:raise HTTPException(409,'Profile đã được người khác thay đổi. Đóng hộp sửa và mở lại trước khi lưu.')
            bm_role=session.get(BmProfile,profile_id)
            if bm_role is not None and body.proxy_id!=row.proxy_id:
                raise HTTPException(409,'Thay hoặc gỡ proxy riêng tại trang BM Profiles.')
            if body.proxy_id:
                get_row(session, Proxy, body.proxy_id)
                scope=session.get(BmProxy,body.proxy_id)
                if scope is not None and (bm_role is None or scope.profile_id!=profile_id):
                    raise HTTPException(409,'Proxy BM riêng không dùng cho profile thường hoặc profile khác.')
            row.name, row.proxy_id = body.name, body.proxy_id
            if body.bm is not None:
                save_bm_profile(session,profile_id,body.bm)
    except IntegrityError:
        raise HTTPException(409, 'Name or proxy is already assigned to another profile.') from None
    except StaleDataError:
        raise HTTPException(409,'Profile đã được người khác thay đổi. Đóng hộp sửa và mở lại trước khi lưu.') from None
    svc.state.log('Profile updated; its data directory was retained.', profile_id)
    return {'ok': True}


def assert_bm_profile_idle(svc,profile_id,detail):
    assert_recipient_available(svc,profile_id)
    browsers=svc.browsers
    if (browsers.status(profile_id) in ('Running','Starting','Queued')
            or profile_id in svc.tasks.profile_jobs
            or profile_id in browsers.extension_profiles
            or profile_id in browsers.facebook_profiles
            or profile_id in browsers.outlook_profiles):
        raise HTTPException(409,detail)


@router.put('/bm-profiles/{profile_id}')
async def assign_bm_profile(profile_id:int,body:BmProfileInput,request:Request):
    svc=services(request)
    with svc.db.session.begin() as session:
        get_row(session,Profile,profile_id)
        if session.get(BmProfile,profile_id) is None:
            assert_bm_profile_idle(svc,profile_id,'Dừng Chrome và các tác vụ trước khi chuyển profile sang BM Profiles.')
        else:
            assert_recipient_available(svc,profile_id)
        save_bm_profile(session,profile_id,body)
    svc.state.log('Profile assigned to BM Profiles; its existing browser data was retained.',profile_id)
    return {'ok':True}


@router.delete('/bm-profiles/{profile_id}')
async def unassign_bm_profile(profile_id:int,request:Request):
    svc=services(request)
    assert_bm_profile_idle(svc,profile_id,'Dừng Chrome và các tác vụ trước khi chuyển profile về Chrome Profiles.')
    with svc.db.session.begin() as session:
        profile=get_row(session,Profile,profile_id)
        if profile.proxy_id and session.get(BmProxy,profile.proxy_id):
            raise HTTPException(409,'Gỡ proxy BM riêng khỏi profile trước khi chuyển về Chrome Profiles.')
        row=session.get(BmProfile,profile_id)
        if row is not None:session.delete(row)
    svc.state.log('Profile returned to Chrome Profiles; only its BM classification was removed.',profile_id)
    return {'ok':True}


@router.put('/bm-profiles/{profile_id}/mobile')
async def set_bm_mobile(profile_id:int,body:BmMobileInput,request:Request):
    svc=services(request)
    assert_bm_profile_idle(svc,profile_id,'Dừng Chrome và tác vụ của profile trước khi đổi chế độ điện thoại.')
    with svc.db.session.begin() as session:
        get_row(session,Profile,profile_id)
        get_row(session,BmProfile,profile_id)
        save_bm_mobile(session,profile_id,body.enabled)
    svc.state.log('Saved automatic mobile browsing for this BM profile.' if body.enabled else 'Disabled automatic mobile browsing for this BM profile.',profile_id)
    return {'enabled':body.enabled}


@router.put('/bm-profiles/{profile_id}/proxy')
async def save_bm_proxy(profile_id:int,body:BmProxyInput,request:Request):
    svc=services(request)
    assert_bm_profile_idle(svc,profile_id,'Dừng Chrome và các tác vụ trước khi thay hoặc gỡ proxy BM.')
    parsed,errors=parse_import(body.content,body.default_protocol)
    if errors or len(parsed)!=1:
        raise HTTPException(400,'Nhập đúng một proxy hợp lệ cho profile BM.')
    value=parsed[0][1]
    with svc.db.session.begin() as session:
        profile=get_row(session,Profile,profile_id)
        get_row(session,BmProfile,profile_id)
        if profile.proxy_id in svc.proxies.checking:
            raise HTTPException(409,'Đợi kiểm tra proxy hiện tại hoàn tất trước khi thay.')
        proxy=session.scalar(select(Proxy).where(Proxy.identity==value.identity))
        if proxy is not None:
            assigned=session.scalar(select(Profile.id).where(Profile.proxy_id==proxy.id,Profile.id!=profile_id))
            scope=session.get(BmProxy,proxy.id)
            if assigned is not None or scope is not None and scope.profile_id not in (None,profile_id):
                raise HTTPException(409,'Proxy này đang được gán cho profile khác.')
            if proxy.id in svc.proxies.checking:
                raise HTTPException(409,'Đợi kiểm tra proxy này hoàn tất trước khi gán.')
            # Endpoint identity excludes the password, so saving it can rotate credentials.
            try:credentials_changed=decrypt_credentials(proxy.encrypted_credentials)!=(value.username,value.password)
            except Exception:credentials_changed=True
            if credentials_changed:
                proxy.encrypted_credentials=encrypt_credentials(value.username,value.password)
                proxy.status='Unchecked'
                proxy.ip_address=proxy.latency_ms=proxy.error_message=proxy.last_checked=None
        else:
            proxy=Proxy(protocol=value.protocol,host=value.host,port=value.port,identity=value.identity,
                        encrypted_credentials=encrypt_credentials(value.username,value.password))
            session.add(proxy);session.flush()
        previous=session.scalar(select(BmProxy).where(BmProxy.profile_id==profile_id))
        if previous is not None and previous.proxy_id!=proxy.id:
            previous.profile_id=None
            session.flush()
        scope=session.get(BmProxy,proxy.id)
        if scope is None:
            session.add(BmProxy(proxy_id=proxy.id,profile_id=profile_id))
        else:scope.profile_id=profile_id
        profile.proxy_id=proxy.id
        proxy_id=proxy.id
    checking=svc.proxies.schedule_checks([proxy_id]) if body.check_after_save else []
    svc.state.log('Saved a dedicated BM proxy; it is excluded from the ordinary proxy pool.',profile_id)
    return {'proxy_id':proxy_id,'checking':checking}


@router.post('/bm-profiles/{profile_id}/proxy/check',status_code=202)
async def check_bm_proxy(profile_id:int,request:Request):
    svc=services(request)
    with svc.db.session() as session:
        profile=get_row(session,Profile,profile_id)
        get_row(session,BmProfile,profile_id)
        scope=session.get(BmProxy,profile.proxy_id) if profile.proxy_id else None
        if scope is None or scope.profile_id!=profile_id:
            raise HTTPException(404,'Profile BM chưa có proxy riêng.')
        proxy_id=profile.proxy_id
    return {'accepted':svc.proxies.schedule_checks([proxy_id])}


@router.delete('/bm-profiles/{profile_id}/proxy')
async def remove_bm_proxy(profile_id:int,request:Request):
    svc=services(request)
    assert_bm_profile_idle(svc,profile_id,'Dừng Chrome và các tác vụ trước khi thay hoặc gỡ proxy BM.')
    with svc.db.session.begin() as session:
        profile=get_row(session,Profile,profile_id)
        get_row(session,BmProfile,profile_id)
        if profile.proxy_id in svc.proxies.checking:
            raise HTTPException(409,'Đợi kiểm tra proxy hoàn tất trước khi gỡ.')
        scope=session.get(BmProxy,profile.proxy_id) if profile.proxy_id else None
        if scope is not None:scope.profile_id=None
        profile.proxy_id=None
    svc.state.log('Detached the BM proxy from this profile; it remains outside the ordinary proxy pool.',profile_id)
    return {'ok':True}


@router.post('/bm-profiles/{profile_id}/open',status_code=202)
async def open_bm_profile(profile_id:int,request:Request,body:BmOpenInput|None=None):
    svc=services(request)
    assert_recipient_available(svc,profile_id)
    with svc.db.session() as session:
        get_row(session,Profile,profile_id)
        row=get_row(session,BmProfile,profile_id)
        portfolios=json.loads(row.portfolios)
    bm_id=body.bm_id if body else None
    if bm_id and not any(item['bm_id']==bm_id for item in portfolios):
        raise HTTPException(404,'BM ID chưa được lưu trong profile này.')
    if bm_id is None:
        if len(portfolios)>1:
            raise HTTPException(422,'Profile có nhiều BM. Hãy chọn BM cụ thể để mở.')
        if portfolios:
            bm_id=portfolios[0]['bm_id']
    browsers=svc.browsers
    if profile_id in svc.tasks.profile_jobs or profile_id in browsers.extension_profiles or profile_id in browsers.facebook_profiles or profile_id in browsers.outlook_profiles or browsers.mode(profile_id)=='automation':
        raise HTTPException(409,'Dừng tác vụ đang sử dụng profile trước khi mở BM thủ công.')
    context=await browsers.ensure_open(profile_id)
    # Opening a queued Chrome can take time. Keep the selected BM bound to
    # this profile if another dashboard edits the portfolio list meanwhile.
    with svc.db.session() as session:
        current=json.loads(get_row(session,BmProfile,profile_id).portfolios)
    if (bm_id is None and current) or (bm_id is not None and not any(item['bm_id']==bm_id for item in current)):
        raise HTTPException(409,'Danh sách BM đã thay đổi. Hãy tải lại profile và chọn BM để mở.')
    target='https://business.facebook.com/settings/'+('?business_id='+bm_id if bm_id else '')
    if getattr(context,'is_manual',False):
        await context.open_url(target)
    else:
        page=await context.new_page()
        await page.goto(target,wait_until='commit',timeout=45000)
        await page.bring_to_front()
    return {'profile_id':profile_id,'status':'opened'}

@router.post('/profiles/start-selected', status_code=202)
async def start_selected(body: Ids, request: Request):
    svc = services(request)
    accepted, errors = [], []
    for pid in body.profile_ids:
        try:
            svc.browsers.request_start(pid)
            accepted.append(pid)
        except ValueError as exc:
            errors.append({'profile_id': pid, 'message': str(exc)})
    return {'accepted': accepted, 'errors': errors}

@router.post('/profiles/stop-selected')
async def stop_selected(body: Ids, request: Request):
    accepted, errors = [], []
    for pid in body.profile_ids:
        try:
            await stop_profile(pid, request)
            accepted.append(pid)
        except (ValueError, HTTPException):
            errors.append({'profile_id': pid, 'message': 'Browser stop failed or profile does not exist.'})
    return {'accepted': accepted, 'errors': errors}

@router.post('/profiles/{profile_id}/start', status_code=202)
async def start_profile(profile_id: int, request: Request):
    svc = services(request)
    return {'status': svc.browsers.request_start(profile_id)}

@router.post('/profiles/{profile_id}/stop')
async def stop_profile(profile_id: int, request: Request):
    svc = services(request)
    with svc.db.session() as session:
        get_row(session, Profile, profile_id)
    await stop_recipient_if_present(svc,profile_id)
    if hasattr(svc, 'facebook'):
        await svc.facebook.stop(profile_id)
    if hasattr(svc, 'outlook'):
        await svc.outlook.stop(profile_id)
    await svc.extensions.stop(profile_id)
    await svc.tasks.stop_profile(profile_id)
    await svc.browsers.stop(profile_id)
    return {'status': 'Stopped'}

@router.delete('/profiles/{profile_id}')
async def delete_profile(profile_id: int, request: Request, confirm: bool = False):
    svc = services(request)
    if not confirm:
        raise HTTPException(400, 'Confirm deletion with confirm=true.')
    assert_recipient_available(svc,profile_id)
    if svc.browsers.status(profile_id) in ('Running', 'Starting', 'Queued') or profile_id in svc.tasks.profile_jobs or profile_id in svc.browsers.extension_profiles or profile_id in svc.browsers.facebook_profiles or profile_id in svc.browsers.outlook_profiles:
        raise HTTPException(409, 'Stop this browser and its task before deleting.')
    with svc.db.session.begin() as session:
        row = get_row(session, Profile, profile_id)
        source = Path(row.profile_path)
        path, root = source.resolve(), svc.config.profiles_dir.resolve()
        if source.is_symlink() or not path.is_relative_to(root) or path == root:
            raise HTTPException(409, 'Unsafe profile path; deletion refused.')
        for process in psutil.process_iter(['name', 'cmdline']):
            try:
                if (process.info.get('name') or '').lower() not in ('chrome.exe', 'chromium.exe'):
                    continue
                for argument in process.info.get('cmdline') or []:
                    if argument.startswith('--user-data-dir=') and Path(argument.split('=', 1)[1].strip('"')).resolve() == path:
                        raise HTTPException(409, 'Chrome still uses this folder. Close that profile window before deleting.')
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        try:
            if path.exists():
                profile_lock = FileLock(str(path / '.manager.lock'))
                with profile_lock.acquire(timeout=0):
                    for child in path.iterdir():
                        if child.name == '.manager.lock':
                            continue
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                (path / '.manager.lock').unlink(missing_ok=True)
                path.rmdir()
            session.delete(row)
        except (Timeout, OSError):
            raise HTTPException(409, 'Profile is locked or its data cannot be removed.') from None
    svc.browsers.errors.pop(profile_id, None)
    svc.state.log('Profile and its local browser data were deleted.', profile_id)
    return {'ok': True}

@router.get('/proxies')
async def list_proxies(request: Request):
    svc = services(request)
    with svc.db.session() as session:
        assignments = {p.proxy_id: p.name for p in session.scalars(select(Profile)) if p.proxy_id}
        return [public_proxy(p) | {'profile_name': assignments.get(p.id), 'checking': p.id in svc.proxies.checking}
                for p in session.scalars(select(Proxy).where(Proxy.id.not_in(select(BmProxy.proxy_id))).order_by(Proxy.id))]

@router.post('/proxies/import')
async def import_proxies(body: ProxyImport, request: Request):
    svc=services(request)
    result=svc.proxies.import_proxies(body.content, body.assign, body.default_protocol)
    result['checking']=svc.proxies.schedule_checks(result['proxy_ids']) if body.check_after_import and result['proxy_ids'] else []
    return result

@router.post('/proxies/check', status_code=202)
async def check_proxies(body: ProxyCheck, request: Request):
    svc = services(request)
    with svc.db.session() as session:
        ids = list(session.scalars(select(Proxy.id).where(Proxy.id.not_in(select(BmProxy.proxy_id))))) if body.proxy_ids is None else body.proxy_ids
        for pid in ids:
            get_row(session, Proxy, pid)
            if session.get(BmProxy,pid):
                raise HTTPException(409,'Kiểm tra proxy BM riêng trong trang BM Profiles.')
    return {'accepted': svc.proxies.schedule_checks(ids)}

@router.delete('/proxies/{proxy_id}')
async def delete_proxy(proxy_id: int, request: Request):
    svc = services(request)
    if proxy_id in svc.proxies.checking:
        raise HTTPException(409, 'Wait for the proxy check to finish.')
    with svc.db.session.begin() as session:
        row = get_row(session, Proxy, proxy_id)
        if session.get(BmProxy,proxy_id):
            raise HTTPException(409,'Quản lý proxy BM riêng trong trang BM Profiles.')
        if session.scalar(select(Profile.id).where(Profile.proxy_id == proxy_id)):
            raise HTTPException(409, 'Unassign this proxy before deleting it.')
        session.delete(row)
    svc.state.emit()
    return {'ok': True}

@router.get('/tasks')
async def list_tasks(request: Request):
    svc = services(request)
    with svc.db.session() as session:
        tasks = list(session.scalars(select(Task).order_by(Task.id.desc())))
        runs = list(session.scalars(select(TaskRun)))
        return [{'id': t.id, 'name': t.name, 'status': t.status, 'created_at': t.created_at,
                 'actions': [a.type for a in decode_task(t.configuration).actions], 'runs': sum(r.task_id == t.id for r in runs),
                 'active_runs': sum(r.task_id == t.id and r.status in ACTIVE for r in runs)} for t in tasks]

@router.post('/tasks', status_code=201)
async def create_task(body: TaskInput, request: Request):
    svc = services(request)
    svc.tasks.validate_origins(body)
    with svc.db.session.begin() as session:
        task = Task(name=body.name, configuration=encode_task(body))
        session.add(task)
        session.flush()
        task_id = task.id
    svc.state.log(f'Task #{task_id} created.')
    return {'id': task_id}

@router.get('/tasks/history')
async def task_history(request: Request, limit: int = 100, offset: int = 0, status: str | None = None, q: str = ''):
    svc = services(request)
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(400, 'Invalid pagination.')
    if status is not None and status not in ('Pending', 'Running', 'Completed', 'Failed', 'Cancelled'):
        raise HTTPException(400, 'Invalid run status.')
    if len(q) > 120:
        raise HTTPException(400, 'Search must be at most 120 characters.')
    filters = []
    if status:
        filters.append(TaskRun.status == status)
    search = q.strip()
    if search:
        profile_match = TaskRun.profile_name.icontains(search, autoescape=True)
        filters.append(or_(profile_match, TaskRun.id == int(search)) if search.isdecimal() else profile_match)
    with svc.db.session() as session:
        total = session.scalar(select(func.count(TaskRun.id)).where(*filters))
        rows = list(session.scalars(select(TaskRun).where(*filters).order_by(TaskRun.id.desc()).offset(offset).limit(limit)))
        return {'total': total, 'items': [serialize_run(r) for r in rows]}

@router.post('/tasks/{task_id}/run', status_code=202)
async def run_task(task_id: int, body: Ids, request: Request):
    return {'run_ids': services(request).tasks.enqueue(task_id, body.profile_ids)}

@router.post('/tasks/{task_id}/stop')
async def stop_task(task_id: int, request: Request):
    svc = services(request)
    with svc.db.session() as session:
        get_row(session, Task, task_id)
    return {'cancelled': await svc.tasks.stop_task(task_id)}

@router.get('/system/status')
async def system_status(request: Request):
    return system_snapshot(services(request))

@router.get('/settings')
async def settings(request: Request):
    return services(request).state.public_settings()

@router.put('/settings')
async def update_settings(body: Settings, request: Request):
    svc = services(request)
    if body.proxy_routing != svc.state.settings.proxy_routing:
        if svc.browsers.contexts or svc.browsers.launches or svc.browsers.reserved or svc.tasks.jobs or svc.browsers.extension_profiles or svc.browsers.facebook_profiles or svc.browsers.outlook_profiles or getattr(svc.browsers,'recipient_profiles',set()) or getattr(svc.browsers,'reel_profiles',set()):
            raise HTTPException(409, 'Stop all browsers and tasks before changing proxy routing.')
    if body.revision is None:raise HTTPException(428,'Tải lại cài đặt để lấy revision trước khi lưu.')
    try:svc.state.save_settings(body,expected_revision=body.revision)
    except (RevisionConflict,StaleDataError):
        raise HTTPException(409,'Cài đặt đã được người khác thay đổi. Tải lại trước khi lưu.') from None
    await svc.browsers.wake()
    return svc.state.public_settings()

@router.get('/logs')
async def logs(request: Request, profile_id: int | None = None, limit: int = 100):
    if not 1 <= limit <= 500:
        raise HTTPException(400, 'Limit must be between 1 and 500.')
    svc = services(request)
    with svc.db.session() as session:
        query = select(Event)
        if profile_id:
            query = query.where(Event.profile_id == profile_id)
        return [{k: getattr(e, k) for k in ('id', 'profile_id', 'level', 'message', 'created_at')}
                for e in session.scalars(query.order_by(Event.id.desc()).limit(limit))]

@router.get('/screenshots/{filename}')
async def screenshot(filename: str, request: Request):
    if not re.fullmatch(r'run_\d+_step_\d+\.png', filename):
        raise HTTPException(404, 'Screenshot not found.')
    path = services(request).config.screenshots_dir / filename
    if not path.is_file():
        raise HTTPException(404, 'Screenshot not found.')
    return FileResponse(path, media_type='image/png')


@router.get('/extensions/authenticator')
async def authenticator_status(request: Request):
    from app.services.extensions import EXTENSION_ID, STORE_URL
    svc = services(request)
    return {'extension_id': EXTENSION_ID, 'store_url': STORE_URL, 'scope': 'manager_profiles_only',
            'require_proxy': svc.state.settings.require_proxy, 'auto_enabled': bool(svc.config.authenticator_deployment),
            'browser_engine': 'Private Chromium' if svc.config.authenticator_deployment else 'Google Chrome' if svc.config.channel else 'Chromium',
            'profiles': svc.extensions.rows()}


@router.post('/extensions/authenticator/setup', status_code=202)
async def setup_authenticator(body: Ids, request: Request):
    svc = services(request)
    accepted, errors = [], []
    for pid in body.profile_ids:
        try:
            svc.extensions.request_setup(pid)
            accepted.append(pid)
        except ValueError as error:
            errors.append({'profile_id': pid, 'message': str(error)})
    return {'accepted': accepted, 'errors': errors}


@router.post('/facebook/check', status_code=202)
async def check_facebook(body: FacebookCheck, request: Request):
    return services(request).facebook.enqueue(body)


@router.get('/facebook/refresh-status')
async def facebook_refresh_status(request: Request, include_profiles: bool | None = None):
    if include_profiles is None:include_profiles=not request.url.path.startswith('/api/v1/')
    scheduler=services(request).facebook_refresh
    return scheduler.status() if include_profiles else scheduler.status(include_profiles=False)


@router.post('/facebook/{profile_id}/stop')
async def stop_facebook(profile_id: int, request: Request):
    svc = services(request)
    with svc.db.session() as session:
        get_row(session, Profile, profile_id)
    await svc.facebook.stop(profile_id)
    return {'ok': True}


@router.post('/outlook/start', status_code=202)
async def start_outlook(body: OutlookStart, request: Request):
    return services(request).outlook.start(body)


@router.post('/outlook/{profile_id}/code', status_code=202)
async def outlook_code(profile_id: int, body: OutlookCode, request: Request):
    services(request).outlook.provide_code(profile_id,body.code.get_secret_value())
    return {'ok': True}


@router.post('/outlook/{profile_id}/stop')
async def stop_outlook(profile_id: int, request: Request):
    svc=services(request)
    with svc.db.session() as session:
        get_row(session,Profile,profile_id)
    await svc.outlook.stop(profile_id)
    return {'ok': True}


@router.post('/outlook/{profile_id}/open', status_code=202)
async def open_outlook(profile_id: int, request: Request):
    svc=services(request)
    assert_recipient_available(svc,profile_id)
    if hasattr(svc,'outlook'):
        await svc.outlook.wait_for_saved_session(profile_id)
    if profile_id in svc.tasks.profile_jobs or profile_id in svc.browsers.extension_profiles or svc.browsers.mode(profile_id)=='automation':
        raise HTTPException(409,'Dừng tác vụ trước khi mở Outlook thủ công.')
    context=await svc.browsers.ensure_open(profile_id)
    if getattr(context,'is_manual',False):
        await context.open_url(OUTLOOK_URL)
    else:
        raise HTTPException(409,'Dừng browser automation trước khi mở Outlook thủ công.')
    return {'ok': True}


@router.get('/page-recipient/config')
async def page_recipient_config(request:Request):
    return {'asset_root':str(services(request).recipient.asset_root)}


@router.get('/page-recipient/jobs')
async def page_recipient_jobs(request:Request):
    return services(request).recipient.list()


@router.post('/page-recipient/start',status_code=202)
async def start_page_recipient(body:PageRecipientStart,request:Request):
    return services(request).recipient.start(body)


@router.post('/page-recipient/{profile_id}/stop')
async def stop_page_recipient(profile_id:int,request:Request):
    svc=services(request)
    with svc.db.session() as session:
        get_row(session,Profile,profile_id)
    await svc.recipient.stop(profile_id)
    return {'ok':True}


@router.post('/page-recipient/{profile_id}/resume',status_code=202)
async def resume_page_recipient(profile_id:int,request:Request):
    return services(request).recipient.resume(profile_id)


@router.post('/page-recipient/{profile_id}/code',status_code=202)
async def page_recipient_code(profile_id:int,body:OutlookCode,request:Request):
    return services(request).recipient.submit_code(profile_id,body.code.get_secret_value())


@router.post('/page-recipient/{profile_id}/check-mail')
async def check_page_recipient_mail(profile_id:int,request:Request):
    from app.services.outlook_probe import probe_outlook
    return await probe_outlook(services(request).recipient,profile_id)


@router.get('/reels/config')
async def reel_config(request:Request):return services(request).reels.config_public()


@router.get('/reels/pages')
async def reel_pages(profile_id:int,request:Request,posting_eligible_only:bool=False):
    if posting_eligible_only:return services(request).reels.page_options(profile_id,posting_eligible_only=True)
    return services(request).reels.page_options(profile_id)


@router.get('/reels/page-eligibility')
async def reel_page_eligibility(request:Request,page_url:str=Query(min_length=1,max_length=2048)):
    return services(request).reels.posting_eligibility(page_url)


@router.get('/reels/assets')
async def reel_assets(request:Request):return services(request).reels.list_assets()


@router.post('/reels/assets',status_code=201)
async def upload_reel_asset(request:Request):
    scheduler=services(request).reels
    try:
        length=request.headers.get('content-length')
        metadata=await scheduler.assets.ingest(request.stream(),unquote(request.headers.get('x-filename','video.mp4')),
                                               content_length=int(length) if length is not None else None)
    except ReelAssetTooLarge as error:raise HTTPException(413,str(error)) from None
    try:return scheduler.add_asset(metadata)
    except Exception:
        path=Path(metadata['path'])
        if path.resolve().parent==scheduler.assets.root and path.name.startswith(metadata['id']):path.unlink(missing_ok=True)
        raise


@router.get('/reels/assets/{asset_id}/video')
async def reel_video(asset_id:str,request:Request):
    scheduler=services(request).reels;asset=scheduler.asset(asset_id)
    path=await asyncio.to_thread(scheduler.assets.resolve,asset.id,asset.path,asset.sha256,asset.size)
    return FileResponse(path,media_type=asset.mime,filename=asset.name,content_disposition_type='inline')


@router.get('/reels')
async def reel_jobs(request:Request,include_archived:bool=False):return services(request).reels.list(include_archived=include_archived)


@router.post('/reels',status_code=201)
async def create_reel(body:StartReel,request:Request):
    if body.request_id is None:raise HTTPException(428,'Cần request_id UUID để tạo lịch và thử lại an toàn.')
    try:return services(request).reels.create(body)
    except ReelRequestConflict as error:raise HTTPException(409,str(error)) from None


@router.post('/reels/{job_id}/cancel')
async def cancel_reel(job_id:int,request:Request):return await services(request).reels.cancel(job_id)


@router.post('/reels/{job_id}/resume',status_code=202)
async def resume_reel(job_id:int,request:Request):return services(request).reels.resume(job_id)


@router.post('/reels/{job_id}/verify-link',status_code=202)
async def verify_reel_link(job_id:int,body:VerifyReelLink,request:Request):return services(request).reels.verify_link(job_id,body.reel_url)


@router.post('/reels/{job_id}/archive')
async def archive_reel(job_id:int,request:Request):return services(request).reels.archive(job_id)


@router.post('/reels/{job_id}/restore')
async def restore_reel(job_id:int,request:Request):return services(request).reels.restore(job_id)

import asyncio
import contextlib
import hashlib
import ipaddress
import os
import re
import secrets
import sqlite3
import sys
from pathlib import Path
from contextlib import asynccontextmanager
from types import SimpleNamespace
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from filelock import FileLock
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.core.config import Config
from app.database.session import Database
from app.services.state import State
from app.services.resources import Resources
from app.services.browsers import BrowserManager
from app.services.proxies import ProxyService
from app.services.extensions import ExtensionManager
from app.services.facebook import FacebookManager
from app.services.facebook_refresh import FacebookRefreshScheduler
from app.services.outlook import OutlookManager
from app.services.page_recipient import PageRecipientManager
from app.services.reel_scheduler import ReelScheduler
from app.services.reel_assets import MAX_VIDEO_BYTES
from app.services.page_workspace import PageWorkspace
from app.services.bm_profiles import adopt_existing_bm_proxies
from app.automation.manager import TaskManager
from app.api.routes import router, snapshot, system_snapshot
from app.api.workspace import router as workspace_router
from app.core.scheduler_health import PeriodicSupervisor, shutdown_services

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'via-auth'))
from via_auth import IdentityStore, IdentityGate, workspace_host
from via_auth.middleware import CHROME_UI_PATHS
from via_auth.rest import install_rest_v1, is_rest_v1, legacy_api_path, problem
from via_auth.store import _assert_private_path


def _workspace_identity(config, identity_store):
    local_auth_dir = config.root.parent / 'via-auth' / 'data'
    owner = os.environ.get('VIA_WORKSPACE_USER_ID')
    central = os.environ.get('VIA_WORKSPACE_AUTH_DIR')
    primary = os.environ.get('VIA_WORKSPACE_PRIMARY')
    if owner is None and central is None and primary is None:
        return identity_store or IdentityStore(local_auth_dir), None, False
    if not owner or not re.fullmatch(r'[1-9][0-9]{0,18}',owner) or not central or primary not in ('0','1'):
        raise ValueError('Incomplete Via workspace binding')
    user_id = int(owner)
    workspace_host(user_id,config.port)
    central_dir = Path(central)
    if not central_dir.is_absolute() or '..' in central_dir.parts:
        raise ValueError('Via workspace identity directory must be absolute')
    _assert_private_path(central_dir/'identity.sqlite3')
    _assert_private_path(local_auth_dir,directory=True,missing=True)
    if (central_dir.resolve()==local_auth_dir.resolve()) != (primary=='1'):
        raise ValueError('Via workspace service directory does not match its primary designation')
    for directory in (config.data_dir,config.profiles_dir,config.screenshots_dir,config.root/'logs',
                      config.root/'reel-assets',config.root/'page-assets'):
        _assert_private_path(directory,directory=True,missing=True)
        if not directory.resolve().is_relative_to(config.root.resolve()) or directory.resolve()==config.root.resolve():
            raise ValueError('Via workspace storage must be inside its Chrome Manager root')
    for name in ('manager.sqlite3','manager.sqlite3-wal','manager.sqlite3-shm','manager.sqlite3-journal','manager.lock'):
        _assert_private_path(config.data_dir/name,missing=True)
    store = identity_store or IdentityStore(central_dir,service_directory=local_auth_dir)
    if store.directory!=central_dir.resolve() or store.service_directory!=local_auth_dir.resolve():
        raise ValueError('Via workspace identity store does not match the configured binding')
    return store, user_id, primary=='0'


def create_app(config=None, *, identity_store=None):
    config = config or Config()
    identity_store,workspace_user_id,secondary_workspace = _workspace_identity(config,identity_store)
    if workspace_user_id is None:
        page_workspace=PageWorkspace(config.root,config.port)
    else:
        page_workspace=PageWorkspace(config.root,config.port,identity_store=identity_store,
            workspace_user_id=workspace_user_id,secondary_workspace=secondary_workspace)
    token = secrets.token_urlsafe(32)
    origins = {f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (config.port, 5173)}

    @asynccontextmanager
    async def lifespan(app):
        config.prepare()
        lock = FileLock(str(config.data_dir / 'manager.lock'))
        lock.acquire(timeout=0)
        db = monitor = facebook_refresh = None
        try:
            if workspace_user_id is not None:
                identity_store.ensure_service_key()
            db = Database(config.data_dir / 'manager.sqlite3')
            adopt_existing_bm_proxies(db)
            state, resources = State(db), Resources()
            browsers = BrowserManager(db, state, config, resources)
            proxies = ProxyService(db, state, config)
            tasks = TaskManager(db, state, browsers, config)
            extensions = ExtensionManager(db, state, config, browsers, tasks)
            facebook = FacebookManager(db, state, browsers, tasks)
            outlook = OutlookManager(db, state, browsers, tasks)
            recipient = PageRecipientManager(db, state, browsers, tasks, config,posting_policy=page_workspace)
            reels = ReelScheduler(db,state,browsers,tasks,config,posting_policy=page_workspace)
            facebook_refresh = FacebookRefreshScheduler(db,state,browsers,tasks,facebook)
            page_workspace.start()
            app.state.services = SimpleNamespace(db=db, config=config, state=state, resources=resources,
                                                 browsers=browsers, proxies=proxies, tasks=tasks, extensions=extensions, facebook=facebook, outlook=outlook, recipient=recipient,reels=reels,facebook_refresh=facebook_refresh)
            proxies.is_busy = lambda pid: pid in tasks.profile_jobs or pid in browsers.extension_profiles or pid in browsers.facebook_profiles or pid in browsers.outlook_profiles or pid in getattr(browsers, 'recipient_profiles', set()) or pid in getattr(browsers,'reel_profiles',set()) or browsers.status(pid) in ('Running', 'Starting', 'Queued')
            tasks.recover()
            await browsers.initialize()
            supervisor = PeriodicSupervisor([('resources', resources.sample), ('state', state.emit),
                ('browser', browsers.wake), ('chrome_reels', reels.tick),
                ('facebook_refresh', facebook_refresh.tick), ('pages', page_workspace.tick)])
            app.state.scheduler_supervisor = supervisor
            monitor = asyncio.create_task(supervisor.run())
            state.log('Manager started. Listening on loopback only.')
            proxies.schedule_pending_checks()
            yield
        finally:
            async def stop_monitor():
                if monitor:
                    monitor.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await monitor
            cleanups = [('monitor', stop_monitor), ('pages', page_workspace.shutdown), ('pages_close', page_workspace.close)]
            if facebook_refresh is not None:
                cleanups.append(('facebook_refresh', facebook_refresh.shutdown))
            if hasattr(app.state, 'services'):
                svc = app.state.services
                cleanups.extend((name, getattr(svc, name).shutdown) for name in
                                ('reels', 'recipient', 'outlook', 'facebook', 'extensions', 'tasks', 'proxies', 'browsers'))
            if db:
                cleanups.append(('database', db.engine.dispose))
            cleanups.append(('lock', lock.release))
            app.state.shutdown_failures = await shutdown_services(cleanups)

    app = FastAPI(title='Chrome Multi-Profile Manager', version='1.0.0', lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    trusted_hosts = ['127.0.0.1', 'localhost']
    if workspace_user_id is not None:
        trusted_hosts.append(workspace_host(workspace_user_id,config.port).split(':',1)[0])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
    app.state.identity_store = identity_store
    app.state.workspace_user_id = workspace_user_id

    @app.middleware('http')
    async def local_auth(request: Request, call_next):
        def reject(detail,status):
            return problem(status,detail) if is_rest_v1(request.url.path) else JSONResponse({'detail':detail},status)
        try:
            local = ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not local:
            return reject('Only loopback clients are allowed.',403)
        # The HTML shell is safe to open from an external link or desktop app.
        # Protect authenticated API calls; blocking the initial document prevents
        # the dashboard from ever establishing its same-origin local session.
        is_api_request = request.url.path.startswith('/api/')
        request_origin = request.headers.get('origin')
        if is_api_request and request_origin and request_origin not in origins:
            return reject('Untrusted origin.',403)
        if is_api_request and request.headers.get('sec-fetch-site') == 'cross-site':
            return reject('Cross-site requests are blocked.',403)
        if is_api_request and request.url.path not in ('/api/session','/api/v1/openapi.json'):
            if not secrets.compare_digest(request.cookies.get('cm_session', ''), token):
                return reject('Initialize a local session first.',401)
        try:
            path=legacy_api_path(request.url.path)
            video_upload=request.method=='POST' and path in ('/api/reels/assets','/pages/api/reels/assets')
            page_image=request.method=='POST' and re.fullmatch(r'/pages/api/pages/[0-9]{1,32}/image',path)
            idea_image=request.method=='POST' and path=='/pages/api/idea-pipeline/images'
            limit=MAX_VIDEO_BYTES if video_upload else 25*1024*1024 if idea_image else 8*1024*1024 if page_image else 2_500_000
            if int(request.headers.get('content-length', '0') or 0) > limit:
                return reject('Request is too large.',413)
        except ValueError:
            return reject('Invalid Content-Length.',400)
        try:
            response = await call_next(request)
        except Exception:
            return reject('Internal operation failed. Check local file permissions and dependencies.',500)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        response.headers.setdefault('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws://127.0.0.1:* ws://localhost:*; frame-ancestors 'none'")
        return response

    # Authentication wraps both dashboards, their API/media routes and WebSocket.
    workspace_paths = ('/_via/workspace',) if workspace_user_id is not None else ()
    app.add_middleware(IdentityGate, store=identity_store, port=config.port,
        workspace_user_id=workspace_user_id,workspace_owner=workspace_user_id is not None,public_paths=workspace_paths)

    if workspace_user_id is not None:
        @app.get('/_via/workspace')
        async def workspace_identity():
            return JSONResponse({'workspace_user_id':workspace_user_id,'pid':os.getpid()},headers={'Cache-Control':'no-store'})

    @app.get('/api/health/ready')
    async def ready():
        supervisor = getattr(app.state, 'scheduler_supervisor', None)
        host = supervisor.snapshot() if supervisor else {'ready': False}
        pages = page_workspace.scheduler_health()
        healthy = bool(host.get('ready') and pages.get('ready'))
        payload={'ready':healthy,'host':host,'pages':pages}
        if workspace_user_id is not None:payload['workspace_user_id']=workspace_user_id
        return JSONResponse(payload, 200 if healthy else 503)

    @app.exception_handler(ValueError)
    async def invalid_value(request, exc):
        if is_rest_v1(request.url.path):return problem(400,str(exc))
        return JSONResponse({'detail': str(exc)}, 400)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        if is_rest_v1(request.url.path):return problem(422,'Dữ liệu hoặc bộ lọc không hợp lệ.')
        return JSONResponse({'detail': [{'loc': e['loc'], 'msg': e['msg'], 'type': e['type']} for e in exc.errors()]}, 422)

    @app.get('/api/session')
    async def session():
        response = JSONResponse({'ok': True,'rest_api_version':1})
        response.set_cookie('cm_session', token, httponly=True, samesite='strict')
        return response

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        try:
            local = ipaddress.ip_address(ws.client.host).is_loopback
        except (ValueError, AttributeError):
            local = False
        if not local or ws.headers.get('origin') not in origins or not secrets.compare_digest(ws.cookies.get('cm_session', ''), token):
            await ws.close(code=1008)
            return
        await ws.accept()
        svc = app.state.services
        queue = asyncio.Queue(maxsize=1)
        svc.state.subscribers.add(queue)
        invalidations=ws.query_params.get('mode')=='invalidate'
        reader=None
        try:
            if invalidations:
                with contextlib.suppress(sqlite3.Error):
                    reader=sqlite3.connect((config.data_dir/'manager.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
            while True:
                gate = ws.scope.get('via.identity_gate')
                if gate is not None:
                    actor = gate.live_actor(ws.scope)
                    if not actor or actor.get('must_change_password'):
                        await ws.close(code=1008)
                        return
                # New dashboard clients invalidate only their current SQL page.
                # Keep the historical snapshot protocol for legacy/MCP clients.
                if invalidations:
                    payload={'kind':'changed','system':system_snapshot(svc)}
                    if reader is not None:
                        with contextlib.suppress(sqlite3.Error):
                            database_version=reader.execute('PRAGMA data_version').fetchone()[0]
                            browser=svc.browsers
                            active=set(browser.contexts)|set(browser.launches)|set(browser.reserved)|set(browser.errors)
                            runtime=tuple((pid,browser.status(pid),browser.mode(pid)) for pid in sorted(active))
                            state=(database_version,runtime,tuple(sorted(svc.proxies.checking)))
                            payload['workspace_revision']=hashlib.blake2s(repr(state).encode(),digest_size=8).hexdigest()
                    await ws.send_json(payload)
                else:
                    await ws.send_json(snapshot(svc))
                try:
                    await asyncio.wait_for(queue.get(), 5)
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            svc.state.subscribers.discard(queue)
            if reader is not None:reader.close()

    app.include_router(router)
    app.include_router(workspace_router)
    install_rest_v1(app,('profiles','bm-profiles','proxies','tasks','reels','settings','page-recipient',
                         'system','facebook','outlook','extensions','screenshots'),
                    source_routes=router.routes,read_routes=workspace_router.routes,
                    read_aliases={'reels/assets':'reel-assets','page-recipient/jobs':'recipient-jobs'})
    app.mount('/pages',page_workspace)
    app.state.page_workspace=page_workspace
    dist = config.root / 'frontend' / 'dist'
    if (dist / 'assets').exists():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    async def index():
        if not (dist / 'index.html').exists():
            return JSONResponse({'detail': 'Build the frontend with npm run build inside frontend/.'}, 503)
        return FileResponse(dist / 'index.html')
    for path in sorted(CHROME_UI_PATHS):
        app.add_api_route(path,index,methods=['GET'],include_in_schema=False)
    return app

app = create_app()

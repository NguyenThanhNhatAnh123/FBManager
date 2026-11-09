"""Standalone loopback HTTP application; no Chrome Manager dependencies."""
import ipaddress
import asyncio
from contextlib import asynccontextmanager,suppress
from datetime import datetime,timezone
from http.cookies import SimpleCookie,CookieError
from pathlib import Path
import secrets
import re
import sys
from typing import Annotated,Literal
from uuid import UUID
from urllib.parse import unquote
from fastapi import APIRouter,FastAPI,Request,Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse,FileResponse,Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,ConfigDict,Field,StrictStr,StrictBool,StrictInt
from .errors import AppError
from .graph import DEFAULT_VERSION,graph_version
from .inputs import ConnectionInput,ConnectionUpdate,PageRefresh,PageEditSource,PageEdit
from .service import PageManager
from .reel_assets import TokenReelAssets,ReelAssetError,ReelAssetTooLarge,MAX_VIDEO_BYTES
from .reel_inputs import TokenReelInput,DEFAULT_TIMEZONE
from .reel_scheduler import TokenReelScheduler
from .video_source import FolderVideoSource,VideoSourceSettings
from .idea_pipeline import IdeaPipeline,MAX_IMAGE_BYTES as IDEA_IMAGE_LIMIT
from .idea_workspace import IdeaWorkspace
from .idea_views import IdeaViews
from .idea_images import IdeaImages
from .idea_runs import IdeaRuns
from .idea_run_results import IdeaRunResults
from .avatars import PageAvatars,AvatarUnavailable
from .podsach_bridge import PodsachBridge,ShopSessionCheckError
from .uploader_connection import UploaderConnection
from .producer_diagnostics import ProducerDiagnostics
from .project_assets import project_assets
from .publication_approvals import PublicationApprovals
from .telegram_approval import TelegramApproval
from .scheduler_health import PeriodicSupervisor, shutdown_services

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'via-auth'))
from via_auth import IdentityStore, IdentityGate
from via_auth.rest import install_rest_v1,is_rest_v1,legacy_api_path

VERSION='1.0.0'
BODY_LIMIT=128_000
REST_PAGE_SIZE=25


class UploaderLogin(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username:StrictStr=Field(min_length=1,max_length=256)
    password:StrictStr=Field(min_length=1,max_length=1024,repr=False)


class UploaderSessionCheck(BaseModel):
    model_config=ConfigDict(extra='forbid')
    provider:Literal['local','web']


class ProducerCheck(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    provider:Literal['codex','claude','all']='all'


class AutomationSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    enabled:StrictBool
    revision:StrictInt=Field(ge=1)


class IdeaIterationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id:StrictStr=Field(min_length=36,max_length=36,pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    expected_source_sha256:StrictStr=Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')
    variant_count:StrictInt=Field(ge=1,le=14)


class ManualProductionItem(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    idea_id:StrictStr=Field(min_length=36,max_length=36,pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    expected_source_sha256:StrictStr=Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')
    repeat:StrictBool=False


class ManualProductionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id:StrictStr=Field(min_length=36,max_length=36,pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    items:list[ManualProductionItem]=Field(min_length=1,max_length=14)
    page_count:StrictInt=Field(default=14,ge=14,le=14)


class TelegramSettings(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    revision:StrictInt=Field(ge=1)
    enabled:StrictBool
    bot_token:StrictStr|None=Field(default=None,min_length=1,max_length=256,repr=False)
    chat_id:StrictStr|None=None
    allowed_user_ids:list[StrictStr]=Field(default_factory=list,max_length=20)


class TelegramRevision(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    revision:StrictInt=Field(ge=1)


class TelegramTrial(TelegramRevision):
    request_id:StrictStr=Field(min_length=36,max_length=36,pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')


class TelegramCohortMember(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    request_id:StrictStr=Field(min_length=36,max_length=36,pattern=r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$')
    binding_hash:StrictStr=Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')


class TelegramCohort(TelegramTrial):
    gate_revision:StrictInt=Field(ge=1)
    members:list[TelegramCohortMember]=Field(min_length=2,max_length=20)


class AudioApprovalInput(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    mix_ids:list[StrictStr]=Field(min_length=1,max_length=100)
    render_signature:StrictStr=Field(min_length=64,max_length=64,pattern=r'^[a-f0-9]{64}$')


class LocalBoundary:
    def __init__(self,app,session,csrf,port,base_path='',cookie_name='mpm_session',workspace_user_id=None):
        self.app,self.session,self.csrf=app,session,csrf
        self.base_path,self.cookie_name=base_path,cookie_name
        self.hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        self.origins={'http://'+host for host in self.hosts}
        if workspace_user_id is not None:
            from via_auth import workspace_host
            self.hosts.add(workspace_host(workspace_user_id,port))

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        headers={key.decode('latin1').lower():value.decode('latin1') for key,value in scope.get('headers',[])}
        response_started=False
        async def secure_send(message):
            nonlocal response_started
            if message['type']=='http.response.start':
                response_started=True
                extra={'cache-control':'no-store','x-content-type-options':'nosniff','x-frame-options':'DENY','referrer-policy':'no-referrer',
                       'content-security-policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}
                message=dict(message);message['headers']=list(message.get('headers',[]))+[(key.encode(),value.encode()) for key,value in extra.items()]
            await send(message)
        async def reject(detail,code,status):return await JSONResponse({'detail':detail,'code':code},status)(scope,receive,secure_send)
        try:local=ipaddress.ip_address(scope['client'][0]).is_loopback
        except (ValueError,TypeError,KeyError):local=False
        if not local or headers.get('host') not in self.hosts:return await reject('Chỉ cho phép truy cập ứng dụng trên máy này.','local_only',403)
        # Starlette Mount retains the original /pages/api/... path and records
        # the consumed prefix in root_path. Authenticate its relative API path.
        path=scope.get('path','');root_path=scope.get('root_path','')
        if root_path!=self.base_path:return await reject('Đường dẫn ứng dụng không hợp lệ.','invalid_mount',403)
        if root_path and path==root_path:path='/'
        elif root_path and path.startswith(root_path+'/'):path=path[len(root_path):]
        api=path.startswith('/api/')
        if api:
            if headers.get('origin') and headers['origin'] not in self.origins or headers.get('sec-fetch-site')=='cross-site':
                return await reject('Nguồn yêu cầu không được phép.','untrusted_origin',403)
            if path!='/api/session':
                try:
                    cookie=SimpleCookie();cookie.load(headers.get('cookie',''));value=cookie[self.cookie_name].value if self.cookie_name in cookie else ''
                except CookieError:value=''
                if not secrets.compare_digest(value.encode(),self.session.encode()):return await reject('Khởi tạo phiên Page Manager trước.','session_required',401)
                if scope['method'] not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(headers.get('x-mpm-csrf','').encode(),self.csrf.encode()):
                    return await reject('Phiên bảo vệ yêu cầu đã thay đổi.','csrf_required',403)
        if api and scope['method'] in ('POST','PUT','PATCH','DELETE'):
            original_path=legacy_api_path(path)
            video_upload=scope['method']=='POST' and original_path=='/api/reels/assets'
            limit=(MAX_VIDEO_BYTES if video_upload else IDEA_IMAGE_LIMIT if scope['method']=='POST' and original_path=='/api/idea-pipeline/images'
                   else 8*1024*1024 if scope['method']=='POST' and re.fullmatch(r'/api/pages/[0-9]{1,32}/image',original_path) else BODY_LIMIT)
            try:
                length=int(headers.get('content-length','0'))
                if length<0:raise ValueError()
            except ValueError:return await reject('Kích thước yêu cầu không hợp lệ.','invalid_length',400)
            if length>limit:return await reject('Yêu cầu quá lớn.','body_too_large',413)
            original_receive=receive
            if video_upload:
                # Authenticate before reading any bytes. Pass through bounded
                # chunks without buffering a potentially 1 GiB body in RAM.
                total=0
                async def video_receive():
                    nonlocal total
                    message=await original_receive()
                    total+=len(message.get('body',b''))
                    if total>limit:raise ReelAssetTooLarge('Video vượt giới hạn 1 GiB.')
                    return message
                receive=video_receive
            else:
                body=bytearray()
                while True:
                    message=await receive()
                    if message['type']=='http.disconnect':return
                    body.extend(message.get('body',b''))
                    if len(body)>limit:return await reject('Yêu cầu quá lớn.','body_too_large',413)
                    if not message.get('more_body'):break
                supplied=False
                async def bounded_receive():
                    nonlocal supplied
                    if not supplied:supplied=True;return {'type':'http.request','body':bytes(body),'more_body':False}
                    return await original_receive()
                receive=bounded_receive
        try:await self.app(scope,receive,secure_send)
        except Exception:
            if not response_started:return await reject('Chưa hoàn tất thao tác. Kiểm tra quyền truy cập dữ liệu cục bộ.','internal_error',500)


def create_app(root=None,*,graph_factory=None,secret_box=None,graph_version=DEFAULT_VERSION,port=8010,base_path='',
               reel_graph_factory=None,video_source_directory=None,idea_pipeline_directory=None,podsach_bridge_factory=None,
               idea_runs_factory=None,idea_results_factory=None,uploader_connection_factory=None,producer_diagnostics_factory=None,
               idea_automation_factory=None,audio_approvals_factory=None,telegram_approval_factory=None,identity_store=None,
               workspace_user_id=None,secondary_workspace=False):
    root=Path(root or Path(__file__).resolve().parents[2]).resolve()
    if (type(secondary_workspace) is not bool or secondary_workspace and workspace_user_id is None
            or workspace_user_id is not None and (type(workspace_user_id) is not int or workspace_user_id<=0 or identity_store is None)):
        raise ValueError('Page workspace identity binding is invalid.')
    identity_store=identity_store or IdentityStore(root.parent/'via-auth'/'data')
    if base_path not in ('','/pages'):raise ValueError('Chỉ hỗ trợ đường dẫn Page Manager /pages hoặc chạy độc lập.')
    if type(port) is not int or not 1024<=port<=65535 or port==8000 and not base_path:raise ValueError('Cổng Page Manager không hợp lệ hoặc trùng Chrome Manager.')
    selected_version=globals()['graph_version'](graph_version)
    manager=PageManager(root,graph_factory=graph_factory,secret_box=secret_box,graph_version=selected_version)
    publication_approvals=PublicationApprovals(manager.store)
    assets=TokenReelAssets(root,manager.store)
    reels=TokenReelScheduler(manager,assets,graph_factory=reel_graph_factory)
    avatars=PageAvatars(manager)
    if secondary_workspace:video_source_directory=root.parent/'media'/'videos'
    video_source=FolderVideoSource(manager,assets,**({'directory':video_source_directory} if video_source_directory is not None else {}))
    if not secondary_workspace:
        idea_pipeline=IdeaPipeline(manager,**({'directory':idea_pipeline_directory} if idea_pipeline_directory is not None else {}))
        uploader_connection=(uploader_connection_factory(manager) if uploader_connection_factory else
                             UploaderConnection(manager.store,manager.secrets))
        upload_bridge=podsach_bridge_factory() if podsach_bridge_factory else PodsachBridge(remote_connection=uploader_connection)
        idea_workspace=IdeaWorkspace(root,manager,idea_pipeline,bridge=upload_bridge)
        idea_views=IdeaViews(manager,idea_pipeline)
        idea_images=IdeaImages(idea_pipeline)
        try:
            idea_results=(idea_results_factory or IdeaRunResults)(manager,idea_pipeline,video_source)
        except Exception:
            # A missing external producer must not take down the Page workspace.
            idea_results=None
        idea_runs=(idea_runs_factory or IdeaRuns)(root,manager,idea_pipeline,on_result=idea_results.accept if idea_results else None)
        if isinstance(idea_runs,IdeaRuns) and idea_results is not None:
            idea_runs.on_item=getattr(idea_results,'accept_item',None)
        if audio_approvals_factory is None:
            from .audio_approvals import AudioApprovals
            audio_approvals_factory=AudioApprovals
        audio_approvals=audio_approvals_factory(manager.store,root)
        idea_runs.audio_approvals=audio_approvals
        if idea_results is not None:idea_results.audio_approvals=audio_approvals
        producer_diagnostics=(producer_diagnostics_factory or ProducerDiagnostics)()
        from .producer_evidence import ProducerEvidence
        producer_evidence=ProducerEvidence(manager.store,root,workflow_directory=idea_pipeline.output_directory)
        producer_diagnostics.capability_provider=producer_evidence.snapshot
        if isinstance(idea_runs,IdeaRuns):idea_runs.upload_bridge=upload_bridge
        idea_workspace.runs=idea_runs
        idea_workspace.views=idea_views
        idea_workspace.consumer_ready=bool(idea_results is not None and getattr(idea_results,'ffmpeg_path',None)
                                          and getattr(idea_results,'bridge',None) is not None)
        if idea_automation_factory is None:
            from .idea_automation import AutoIdeaController
            idea_automation_factory=AutoIdeaController
        idea_automation=idea_automation_factory(manager,idea_pipeline,idea_runs,upload_bridge,producer_diagnostics)
        from .manual_production_batch import ManualProductionBatch
        manual_production_batches=ManualProductionBatch(manager.store,idea_pipeline,idea_runs,audio_approvals)
    publication_media_resolvers={'token':lambda asset_id:assets.resolve(asset_id)['path']}
    def token_publication_status(job_id):
        from .publication_approvals import token_binding
        row=manager.store.reel_job(job_id);asset=manager.store.reel_asset(row['asset_id']);ready=False
        binding=token_binding(row,asset);approval=publication_approvals.verdict(binding)
        status,step=row['status'],row['step']
        if status in ('scheduled','waiting_page') and approval['state']=='expired':status,step='needs_user','approval_expired'
        if row['status']=='scheduled' and not row.get('archived_at'):
            try:
                source=manager.store.source(row['page_id'],row['connection_id'])
                ready=(source['token_revision']==row['token_revision'] and manager.store.posting_eligibility(row['page_id'])['can_publish']
                    and approval['can_publish'])
                if ready:assets.resolve(row['asset_id'])
            except AppError:ready=False
            except Exception:ready=False
        return {'binding':binding,'status':status,'step':step,
            'published_url':row.get('published_url'),'ready':bool(ready),'page_name':row['page_name']}
    publication_status_resolvers={'token':token_publication_status}
    def resolve_publication_media(queue,asset_id):
        resolver=publication_media_resolvers.get(queue)
        if not callable(resolver):raise AppError('Kho video của hàng đợi chưa sẵn sàng.','telegram_media_unavailable',409)
        return resolver(asset_id)
    def resolve_publication_status(queue,job_id):
        resolver=publication_status_resolvers.get(queue)
        if not callable(resolver):raise AppError('Hàng đợi chưa sẵn sàng để đọc kết quả đăng.','publication_status_unavailable',409)
        return resolver(job_id)
    telegram_approval=(telegram_approval_factory(manager.store,manager.secrets,publication_approvals) if telegram_approval_factory else
        TelegramApproval(manager.store,manager.secrets,publication_approvals,media_resolver=resolve_publication_media,status_resolver=resolve_publication_status))
    @asynccontextmanager
    async def lifespan(application):
        monitor=None
        try:
            if not base_path:
                ticks=[('reels',reels.tick),('video_source',video_source.tick)]
                if not secondary_workspace:
                    ticks.extend([('idea_workspace',idea_workspace.tick),('idea_automation',idea_automation.tick),
                                  ('idea_runs',idea_runs.tick),('idea_views',idea_views.tick)])
                ticks.append(('telegram_approval',telegram_approval.tick))
                supervisor=PeriodicSupervisor(ticks)
                application.state.scheduler_supervisor=supervisor
                monitor=asyncio.create_task(supervisor.run())
            yield
        finally:
            async def stop_monitor():
                if monitor:
                    monitor.cancel()
                    with suppress(asyncio.CancelledError):await monitor
            shutdown=[('monitor',stop_monitor)]
            if not secondary_workspace:shutdown.append(('idea_automation',idea_automation.shutdown))
            shutdown.append(('telegram_approval',telegram_approval.shutdown))
            if not secondary_workspace:
                shutdown.extend([('producer_diagnostics',producer_diagnostics.shutdown),('idea_views',idea_views.shutdown),
                                 ('idea_runs',idea_runs.shutdown),('idea_workspace',idea_workspace.shutdown)])
            shutdown.extend([('video_source',video_source.shutdown),('reels',reels.shutdown)])
            application.state.shutdown_failures=await shutdown_services(shutdown)
    session=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    cookie_name='mpm_pages_session' if base_path else 'mpm_session'
    app=FastAPI(title='Meta Page Manager',version=VERSION,docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.manager=manager;app.state.root=root;app.state.base_path=base_path
    app.state.reels=reels;app.state.reel_assets=assets
    app.state.page_avatars=avatars
    app.state.video_source=video_source
    if not secondary_workspace:
        app.state.idea_pipeline=idea_pipeline
        app.state.idea_workspace=idea_workspace
        app.state.idea_views=idea_views
        app.state.idea_images=idea_images
        app.state.idea_runs=idea_runs
        app.state.idea_results=idea_results
        app.state.producer_diagnostics=producer_diagnostics
        app.state.producer_evidence=producer_evidence
        app.state.idea_automation=idea_automation
        app.state.manual_production_batches=manual_production_batches
        app.state.audio_approvals=audio_approvals
    app.state.publication_approvals=publication_approvals
    app.state.publication_media_resolvers=publication_media_resolvers
    app.state.publication_status_resolvers=publication_status_resolvers
    app.state.telegram_approval=telegram_approval
    app.add_middleware(LocalBoundary,session=session,csrf=csrf,port=port,base_path=base_path,cookie_name=cookie_name,
                       workspace_user_id=workspace_user_id)
    owner_options=({'workspace_user_id':workspace_user_id,'workspace_owner':True} if workspace_user_id is not None else {})
    app.add_middleware(IdentityGate,store=identity_store,port=port,base_path=base_path,workspace='pages',**owner_options)
    app.state.identity_store=identity_store
    idea_routes=APIRouter(prefix='/api/idea-pipeline')
    resources=('overview','connections','pages','events','reels','video-source','telegram-approval')
    if not secondary_workspace:resources+=('idea-pipeline',)

    @app.get('/api/health/ready')
    async def ready():
        supervisor=getattr(app.state,'scheduler_supervisor',None)
        health=supervisor.snapshot() if supervisor else {'ready':False,'reason':'host_supervisor_required' if base_path else 'not_started'}
        return JSONResponse(health,200 if health.get('ready') else 503)

    @app.exception_handler(AppError)
    async def known_error(request,error):return JSONResponse({'detail':error.detail,'code':error.code},error.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request,error):return JSONResponse({'detail':'Dữ liệu nhập không hợp lệ. Kiểm tra tên, loại token, ID và giới hạn yêu cầu.','code':'invalid_input'},422)

    @app.exception_handler(ReelAssetTooLarge)
    async def video_large(request,error):return JSONResponse({'detail':str(error),'code':'body_too_large'},413)

    @app.exception_handler(ReelAssetError)
    async def video_invalid(request,error):return JSONResponse({'detail':str(error),'code':'invalid_video'},422)

    @app.get('/api/session')
    async def initialize():
        result=JSONResponse({'csrf_token':csrf,'app':{'name':'Page Manager','version':VERSION,'graph_version':selected_version,'timezone':'America/Los_Angeles',
            'base_path':base_path,'read_api_version':1,'rest_api_version':1,'rest_api_resources':list(resources),
            'rest_api_features':['idea_views'] if not secondary_workspace else [],
            'page_editing':True,'token_reels':True,'video_source':True,'idea_pipeline':not secondary_workspace,'chrome_manager_url':f'http://127.0.0.1:{port if base_path else 8000}/','page_manager_url':f'http://127.0.0.1:{port}{base_path}/'}})
        result.set_cookie(cookie_name,session,httponly=True,samesite='strict',path=base_path+'/' if base_path else '/')
        return result

    @app.get('/api/overview')
    async def overview(timezone:str='America/Los_Angeles'):return manager.overview(timezone)

    @app.get('/api/connections')
    async def connections(request:Request,limit:int|None=Query(default=None,ge=1,le=100,description='Defaults to 25 in API v1.'),offset:int=Query(default=0,ge=0),search:str=Query(default='',max_length=200),connected_only:bool=False):
        if limit is None and is_rest_v1(request.url.path):limit=REST_PAGE_SIZE
        return manager.connections(limit,offset,search,connected_only=connected_only)

    @app.get('/api/connections/options')
    async def connection_options(limit:int=Query(default=100,ge=1,le=100),offset:int=Query(default=0,ge=0),search:str=Query(default='',max_length=200)):
        return manager.store.connection_options(limit,offset,search)

    @app.post('/api/connections',status_code=201)
    async def add_connection(body:ConnectionInput):return manager.save_connection(body)

    @app.put('/api/connections/{connection_id}')
    async def update_connection(connection_id:int,body:ConnectionUpdate):return manager.save_connection(body,connection_id)

    @app.delete('/api/connections/{connection_id}')
    async def disconnect(connection_id:int,revision:int=Query(gt=0)):return manager.disconnect(connection_id,revision=revision)

    @app.post('/api/connections/{connection_id}/check')
    async def check(connection_id:int):return await manager.check(connection_id)

    @app.post('/api/connections/{connection_id}/sync')
    async def sync(connection_id:int):return await manager.sync(connection_id)

    @app.get('/api/pages')
    async def pages(request:Request,connection_id:int|None=Query(default=None,gt=0),search:str=Query(default='',max_length=200),status:str='',information_status:Literal['','updated','not_updated']='',timezone:str='America/Los_Angeles',today:Literal['','posted','zero','unknown']='',limit:int|None=Query(default=None,ge=1,le=100,description='Defaults to 25 in API v1.'),offset:int=Query(default=0,ge=0),posting_eligible_only:bool=False):
        if limit is None and is_rest_v1(request.url.path):limit=REST_PAGE_SIZE
        return manager.pages(connection_id=connection_id,search=search,status=status,information_status=information_status,timezone=timezone,today=today,limit=limit,offset=offset,posting_eligible_only=posting_eligible_only)

    @app.get('/api/pages/{page_id}')
    async def page(page_id:str,timezone:str='America/Los_Angeles',limit:int=Query(default=25,ge=1,le=100),offset:int=Query(default=0,ge=0)):
        return manager.page(page_id,timezone,limit,offset)

    @app.get('/api/pages/{page_id}/avatar')
    async def page_avatar(page_id:str,request:Request,connection_id:int|None=Query(default=None,gt=0)):
        try:return Response(await avatars.get(page_id,connection_id),media_type='image/png')
        except AvatarUnavailable:
            if is_rest_v1(request.url.path):raise AppError('Chưa có ảnh đại diện của Page.','avatar_unavailable',404) from None
            return Response(status_code=404)

    @app.post('/api/pages/{page_id}/refresh')
    async def refresh(page_id:str,body:PageRefresh,timezone:str='America/Los_Angeles'):return await manager.refresh(page_id,body,timezone)

    @app.post('/api/pages/{page_id}/edit-options')
    async def edit_options(page_id:str,body:PageEditSource):return await manager.edit_options(page_id,body)

    @app.get('/api/pages/{page_id}/categories')
    async def categories(page_id:str,connection_id:int=Query(gt=0),q:str=Query(default='',max_length=100)):
        return await manager.page_categories(page_id,connection_id,q)

    @app.post('/api/pages/{page_id}/edit')
    async def edit(page_id:str,body:PageEdit):return await manager.edit_page(page_id,body)

    @app.post('/api/pages/{page_id}/image')
    async def edit_image(page_id:str,request:Request,connection_id:int=Query(gt=0),request_id:UUID=Query(),kind:Literal['avatar','cover']=Query()):
        raw=bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw)>8*1024*1024:raise AppError('Ảnh vượt giới hạn 8 MiB.','body_too_large',413)
        result=await manager.edit_image(page_id,connection_id,request_id,kind,bytes(raw),request.headers.get('content-type',''))
        if kind=='avatar' and (result.get('requires_refresh') or result.get('outcome')=='accepted'):avatars.invalidate(page_id)
        return result

    @app.get('/api/events')
    async def events(limit:int=Query(default=50,ge=1,le=100),offset:int=Query(default=0,ge=0)):
        return {'items':manager.store.events(limit,offset),'total':manager.store.event_count(),'limit':limit,'offset':offset}

    @app.get('/api/video-source')
    async def video_source_status(limit:int=Query(default=100,ge=1,le=100),offset:int=Query(default=0,ge=0)):
        return video_source.status(limit,offset)

    @app.put('/api/video-source/settings')
    async def video_source_settings(body:VideoSourceSettings):return video_source.configure(body)

    @app.post('/api/video-source/scan')
    async def video_source_scan():return video_source.request_scan()

    @idea_routes.get('')
    async def idea_pipeline_status(request:Request,summary:bool=False,limit:int|None=Query(default=None,ge=1,le=100),
            offset:int=Query(default=0,ge=0),search:str=Query(default='',max_length=200),
            status:Literal['all','pending','verified','review']='all',sort:Literal['warehouse','views']='warehouse',
            ranking_limit:int|None=Query(default=None,ge=1,le=100),ranking_offset:int=Query(default=0,ge=0),
            calendar:Annotated[str,Query(max_length=10)]='',timezone:Annotated[str,Query(max_length=100)]='America/Los_Angeles',
            view:Literal['legacy','ideas','connections']='legacy',include:Annotated[str,Query(max_length=100)]=''):
        from .library_calendar import validate_library_calendar
        validate_library_calendar(calendar,timezone)
        included={name.strip() for name in include.split(',') if name.strip()}
        supplements={'automation','telegram','audio_mixes','project_assets','producers'}
        if included-supplements:
            raise AppError('Chọn thành phần trạng thái hợp lệ.','invalid_idea_include',422)
        versioned=is_rest_v1(request.url.path)
        if view=='connections':limit=None;ranking_limit=None
        elif versioned and limit is None:limit=REST_PAGE_SIZE
        if view!='connections' and calendar and limit is None:limit=12
        if summary or limit is not None or ranking_limit is not None or view!='legacy':
            result=await idea_workspace.page_status(limit=limit,offset=offset,search=search,status=status,sort=sort,
                ranking_limit=ranking_limit,ranking_offset=ranking_offset,calendar=calendar,timezone=timezone,
                clamp_offset=not versioned)
        else:result=await idea_workspace.status()
        if view=='legacy':included=supplements
        visible_ids=[item['id'] for item in result['pipeline'].get('ideas',[])]
        automation=(await asyncio.to_thread(idea_automation.status) if 'automation' in included
                    else await asyncio.to_thread(idea_automation.browser_status,visible_ids))
        result['policy']={**result.get('policy',{}),'production_enabled':automation['settings']['enabled']}
        if view=='ideas' or 'automation' in included:result['automation']=automation
        if view=='connections':
            for name in ('runs','ranking','performance'):result.pop(name,None)
            for name in ('ideas','jobs'):result['pipeline'].pop(name,None)
        for name,reader in (('producers',producer_diagnostics.snapshot),('telegram',telegram_approval.snapshot),
                            ('audio_mixes',audio_approvals.snapshot),('project_assets',project_assets)):
            if name in included:result[name]=await asyncio.to_thread(reader)
        return result

    @idea_routes.get('/project-assets')
    async def project_assets_status():return project_assets()

    @idea_routes.get('/audio-mixes')
    async def audio_mix_status():return await asyncio.to_thread(audio_approvals.snapshot)

    @idea_routes.post('/audio-mixes/approve')
    async def audio_mix_approve(body:AudioApprovalInput):
        if (len(set(body.mix_ids))!=len(body.mix_ids)
                or any(re.fullmatch(r'[a-f0-9]{64}',mix_id) is None for mix_id in body.mix_ids)):
            raise AppError('Danh sách bản mix phải gồm các mã riêng biệt hợp lệ.','invalid_audio_approval',422)
        return await asyncio.to_thread(audio_approvals.approve,body.mix_ids,body.render_signature)

    @idea_routes.get('/audio-mixes/{mix_id}/audio')
    async def audio_mix_preview(mix_id:str):
        if re.fullmatch(r'[a-f0-9]{64}',mix_id) is None:
            raise AppError('Mã bản mix không hợp lệ.','invalid_audio_mix',422)
        path=await asyncio.to_thread(audio_approvals.preview_path,mix_id)
        if not isinstance(path,Path) or path.suffix.lower()!='.wav':
            raise AppError('Bản mix chưa sẵn sàng để nghe.','audio_mix_unavailable',409)
        return FileResponse(path,media_type='audio/wav',filename=mix_id+'.wav',content_disposition_type='inline')

    @idea_routes.get('/automation')
    async def automation_status():return idea_automation.status()

    @idea_routes.post('/production-batches',status_code=201)
    async def manual_production_request(body:ManualProductionRequest):
        return {'batch':manual_production_batches.request(body.request_id,items=[item.model_dump() for item in body.items],page_count=body.page_count)}

    @idea_routes.get('/production-batches/{batch_id}')
    async def manual_production_status(batch_id:UUID):
        return {'batch':manual_production_batches.get(str(batch_id))}

    @idea_routes.put('/automation')
    async def automation_settings(body:AutomationSettings):return idea_automation.configure(body.enabled,body.revision)

    @idea_routes.get('/producers')
    async def producer_status():return producer_diagnostics.snapshot()

    @idea_routes.post('/producers/check')
    async def producer_check(body:ProducerCheck):return await producer_diagnostics.check(body.provider)

    @app.get('/api/telegram-approval')
    async def telegram_status():return telegram_approval.snapshot()

    @app.put('/api/telegram-approval')
    async def telegram_settings(body:TelegramSettings):return telegram_approval.configure(body.model_dump(exclude_unset=True))

    @app.post('/api/telegram-approval/check')
    async def telegram_check(body:TelegramRevision):return await telegram_approval.check(body.revision)

    @app.post('/api/telegram-approval/trial')
    async def telegram_trial(body:TelegramTrial):return telegram_approval.start_trial(body.revision,body.request_id)

    @app.post('/api/telegram-approval/cohorts')
    async def telegram_cohort(body:TelegramCohort):
        return await asyncio.to_thread(telegram_approval.start_cohort,body.model_dump())

    @app.delete('/api/telegram-approval/connection')
    async def telegram_disconnect(body:TelegramRevision):return telegram_approval.disconnect(body.revision)

    @app.get('/api/telegram-approval/requests/{request_id}/video')
    async def telegram_preview(request_id:str):
        path=await asyncio.to_thread(telegram_approval.preview_path,request_id)
        return FileResponse(path,media_type='video/mp4',filename='approval-preview.mp4',content_disposition_type='inline')

    @idea_routes.get('/upload/status')
    async def uploader_status():return await asyncio.to_thread(upload_bridge.inspect)

    @idea_routes.post('/upload/connect')
    async def uploader_connect(body:UploaderLogin):
        try:await asyncio.to_thread(uploader_connection.connect,body.username,body.password)
        finally:
            if callable(getattr(upload_bridge,'invalidate',None)):upload_bridge.invalidate()
        return {'upload':await asyncio.to_thread(upload_bridge.inspect)}

    @idea_routes.delete('/upload/connection')
    async def uploader_disconnect():
        await asyncio.to_thread(uploader_connection.disconnect)
        if callable(getattr(upload_bridge,'invalidate',None)):upload_bridge.invalidate()
        return {'upload':await asyncio.to_thread(upload_bridge.inspect)}

    @idea_routes.post('/upload/session-check')
    async def uploader_session_check(body:UploaderSessionCheck):
        try:
            result=await asyncio.to_thread(upload_bridge.check_shop_session,body.provider)
            upload=dict(result);check=upload.pop('session_check',None)
            public_check=None
            if (isinstance(check,dict) and check.get('provider')==body.provider
                    and type(check.get('authenticated')) is bool and type(check.get('ready')) is bool
                    and check.get('status') in ('authenticated','authentication_required')
                    and check.get('reason') in (None,'authentication_required','proof_not_available','configuration_required')):
                try:
                    stamp=datetime.fromisoformat(check['checked_at'])
                    if stamp.tzinfo is not None and stamp.utcoffset() is not None:
                        public_check={key:check[key] for key in ('provider','authenticated','ready','status','reason')}
                        public_check['checked_at']=stamp.astimezone(timezone.utc).isoformat()
                except (KeyError,TypeError,ValueError):pass
            return {'upload':upload,**({'check':public_check} if public_check is not None else {})}
        except ShopSessionCheckError as error:raise AppError(error.detail,'uploader_check_'+error.reason,error.status_code) from None
        except ValueError:raise AppError('Chưa kiểm tra được phiên shop. Mở tool để kiểm tra đúng shop và đăng nhập rồi thử lại.','uploader_session_unavailable',409) from None

    @idea_routes.get('/ideas/{idea_id}')
    async def idea_pipeline_item(idea_id:str):return await asyncio.to_thread(idea_pipeline.idea,idea_id)

    @idea_routes.get('/ideas/{idea_id}/image')
    async def idea_pipeline_image(idea_id:str):
        return Response(await asyncio.to_thread(idea_images.get,idea_id),media_type='image/png')

    @idea_routes.post('/images')
    async def idea_pipeline_upload(request:Request):
        filename=request.headers.get('x-filename','')
        if not filename or len(filename)>1800:raise AppError('Tên ảnh idea không hợp lệ.','invalid_idea_image',422)
        return await asyncio.to_thread(idea_images.save,await request.body(),unquote(filename))

    @idea_routes.post('/scan')
    async def idea_pipeline_scan(request:Request,body:dict,summary:bool=False):
        if body:raise AppError('Thao tác quét không nhận đường dẫn hoặc cấu hình từ trình duyệt.','invalid_idea_scan',422)
        await idea_workspace.scan()
        if summary:return await idea_workspace.page_status()
        if is_rest_v1(request.url.path):return await idea_workspace.page_status(limit=REST_PAGE_SIZE,clamp_offset=False)
        return await idea_workspace.status()

    @idea_routes.post('/views/refresh')
    async def idea_pipeline_views(body:dict):
        if body:raise AppError('Chỉ đọc lượt xem của video đã liên kết trong hệ thống.','invalid_idea_metrics',422)
        return await idea_views.refresh()

    @idea_routes.post('/ideas/{idea_id}/run')
    async def idea_pipeline_run(idea_id:str,body:dict):
        if set(body) not in ({'request_id'},{'request_id','variant_count'}):
            raise AppError('Cần mã yêu cầu riêng và số video hợp lệ cho lần bấm Chạy idea.','invalid_idea_run',422)
        count=body.get('variant_count',7)
        if type(count) is not int or not 1<=count<=14:
            raise AppError('Số video phải là số nguyên từ 1 đến 14.','invalid_idea_run',422)
        try:
            if (not isinstance(body['request_id'],str) or str(UUID(body['request_id']))!=body['request_id']
                    or str(UUID(idea_id))!=idea_id):raise ValueError()
        except (ValueError,TypeError,AttributeError):raise AppError('Mã yêu cầu hoặc idea không hợp lệ.','invalid_idea_run',422) from None
        with manager.store.connect() as db:
            existing=db.execute('SELECT id,idea_id FROM idea_manual_runs WHERE request_id=?',(body['request_id'],)).fetchone()
        if existing:
            if existing['idea_id']!=idea_id:raise AppError('Mã chạy đã dùng cho idea khác.','idea_run_conflict',409)
            run=await asyncio.to_thread(idea_runs.request,idea_id,body['request_id'],variant_count=count)
            return {'accepted':True,'run':run}
        if not idea_workspace.consumer_ready:
            raise AppError('Bộ kiểm tra kết quả video chưa sẵn sàng; chưa tạo lượt chạy.','idea_run_not_ready',409)
        # A corrupt or changed design must fail locally before a paid/remote
        # generation worker can be launched. Replays above need no file read.
        await asyncio.to_thread(idea_images.get,idea_id)
        run=await asyncio.to_thread(idea_runs.request,idea_id,body['request_id'],variant_count=count)
        return {'accepted':True,'run':run}

    @idea_routes.post('/ideas/{idea_id}/iterations')
    async def idea_pipeline_iteration(idea_id:str,body:IdeaIterationRequest):
        try:
            if str(UUID(idea_id))!=idea_id:raise ValueError()
        except (ValueError,TypeError,AttributeError):
            raise AppError('Mã idea không hợp lệ.','invalid_idea_run',422) from None
        with manager.store.connect() as db:
            previous=db.execute('SELECT idea_id FROM idea_manual_runs WHERE request_id=?',(body.request_id,)).fetchone()
        if previous is None:
            if not idea_workspace.consumer_ready:
                raise AppError('Bộ kiểm tra kết quả video chưa sẵn sàng; chưa tạo lượt chạy.','idea_run_not_ready',409)
            await asyncio.to_thread(idea_images.get,idea_id)
        run=await asyncio.to_thread(idea_runs.request_iteration,idea_id,body.request_id,
            expected_source_sha256=body.expected_source_sha256,variant_count=body.variant_count)
        return {'accepted':True,'run':run}

    @idea_routes.post('/runs/{run_id}/cancel')
    async def idea_pipeline_cancel(run_id:str,body:dict):
        if body:raise AppError('Thao tác dừng không nhận dữ liệu bổ sung.','invalid_idea_run',422)
        return {'run':await idea_runs.cancel(run_id)}

    @app.get('/api/reels/config')
    async def reel_config():
        from zoneinfo import available_timezones
        return {**reels.status(),'max_video_bytes':MAX_VIDEO_BYTES,'video_extensions':['.mp4','.m4v','.mov'],
                'default_timezone':DEFAULT_TIMEZONE,'timezones':sorted(available_timezones()),'max_parallel':2}

    @app.get('/api/reels/assets')
    async def reel_assets(request:Request,limit:int|None=Query(default=None,ge=1,le=100,description='Defaults to 25 in API v1.'),offset:int=Query(default=0,ge=0),search:str=Query(default='',max_length=200),status:Literal['all','unused','scheduled','published']='all',asset_id:str|None=Query(default=None,max_length=100),calendar:Annotated[str,Query(max_length=10)]='',timezone:Annotated[str,Query(max_length=100)]='America/Los_Angeles'):
        from .library_calendar import validate_library_calendar
        validate_library_calendar(calendar,timezone)
        versioned=is_rest_v1(request.url.path)
        if limit is None and versioned:limit=REST_PAGE_SIZE
        if calendar and limit is None:limit=12
        if limit is None:return {'assets':assets.list()}
        page=await asyncio.to_thread(manager.store.reel_asset_page,limit,offset,search,status,asset_id,calendar,timezone,clamp_offset=not versioned)
        return {'assets':[assets.public(row)|{'linked_job_count':row['linked_job_count'],'has_published_jobs':row['has_published_jobs'],'linked_jobs':[reels.public(job) for job in row['linked_jobs']]} for row in page['rows']],**{key:page[key] for key in ('total','limit','offset','summary')}}

    @app.post('/api/reels/assets',status_code=201)
    async def reel_upload(request:Request):
        if request.headers.get('content-type','').split(';')[0].strip().lower()!='application/octet-stream':
            raise AppError('Tải video bằng luồng tệp nhị phân.','invalid_video_type',415)
        encoded=request.headers.get('x-filename','')
        if len(encoded)>1500:raise AppError('Tên video quá dài.','invalid_video',422)
        try:filename=unquote(encoded,encoding='utf-8',errors='strict')
        except UnicodeError:raise AppError('Tên video không hợp lệ.','invalid_video',422) from None
        declared=request.headers.get('content-length')
        length=int(declared) if declared is not None else None
        return {'asset':await assets.ingest(request.stream(),filename,content_length=length)}

    @app.get('/api/reels/assets/{asset_id}/video')
    async def reel_preview(asset_id:str):
        row=await asyncio.to_thread(assets.resolve,asset_id)
        return FileResponse(row['path'],media_type=row['mime'],filename=row['name'],content_disposition_type='inline')

    @app.get('/api/reels')
    async def reel_jobs(request:Request,limit:int|None=Query(default=None,ge=1,le=100,description='Defaults to 25 in API v1.'),offset:int=Query(default=0,ge=0),search:str=Query(default='',max_length=200),status:Literal['all','pending','attention','scheduled','waiting_page','starting','uploading','processing','publishing','verifying','completed','needs_user','needs_review','cancelled']='all',request_id:UUID|None=None,calendar:Annotated[str,Query(max_length=10)]='',sort:Literal['recent','scheduled_asc','scheduled_desc']='recent',page_id:Annotated[str|None,Query(pattern=r'^[0-9]{1,32}$')]=None,connection_id:Annotated[int|None,Query(gt=0,le=2**63-1)]=None):
        from .reel_calendar import validate_calendar
        try:validate_calendar(calendar)
        except ValueError:raise AppError('Chọn năm, tháng hoặc ngày lịch hợp lệ.','invalid_reel_calendar',422) from None
        if limit is None and (is_rest_v1(request.url.path) or page_id is not None or connection_id is not None):limit=REST_PAGE_SIZE
        if limit is None and (calendar or sort!='recent'):
            raise AppError('Bộ lọc ngày và sắp xếp cần danh sách có phân trang.','reel_calendar_requires_limit',422)
        if limit is None:return {'jobs':reels.list()['items'],'scheduler':reels.status()}
        page=await asyncio.to_thread(manager.store.reel_job_page,limit,offset,search,status,request_id,calendar,sort,page_id=page_id,connection_id=connection_id)
        return {'jobs':[reels.public(row)|{'asset':assets.public(page['assets'][row['asset_id']]) if row['asset_id'] in page['assets'] else None} for row in page['rows']],'scheduler':reels.status(),**{key:page[key] for key in ('total','limit','offset')}}

    @app.get('/api/reels/{job_id}')
    async def reel_detail(job_id:int):
        row=manager.store.reel_job(job_id)
        try:asset=assets.public(manager.store.reel_asset(row['asset_id']))
        except AppError:asset=None
        return {'job':reels.public(row)|{'asset':asset}}

    @app.post('/api/reels',status_code=201)
    async def reel_create(body:TokenReelInput):
        previous=manager.store.reel_job_by_request(body.request_id)
        instant=datetime.now(timezone.utc)
        if previous and previous.get('publish_mode','scheduled')!=body.publish_mode:
            raise AppError('Mã yêu cầu đã dùng cho cách đăng khác.','reel_conflict',409)
        anchor=datetime.fromisoformat(previous['scheduled_at']) if previous and body.publish_mode=='on_approval' else instant
        schedule=body.schedule(now=anchor)
        # Replaying the same explicit request ID is safe even if its due time
        # has passed. A fresh request must always be scheduled in the future.
        existing=previous is not None
        if not existing and body.publish_mode=='scheduled' and datetime.fromisoformat(schedule['scheduled_at'])<=instant:
            raise AppError('Chọn thời gian đăng trong tương lai.','schedule_in_past',422)
        if not existing and body.publish_mode=='on_approval' and body.approval_policy is None and datetime.fromisoformat(body.approve_by)<=instant:
            raise AppError('Hạn duyệt phải ở tương lai.','approval_deadline_in_past',422)
        source=manager.store.source(body.page_id,body.connection_id)
        page,_=manager.store.page(body.page_id)
        asset=await asyncio.to_thread(assets.resolve,body.asset_id)
        values={**body.model_dump(mode='json'),**schedule,'page_name':page['name'],'connection_label':source['label'],
                'token_revision':source['token_revision'],'asset_name':asset['name']}
        if previous and 'approval_policy' not in body.model_fields_set:
            values['approval_policy']=previous.get('approval_policy')
        row,replayed=manager.store.reel_create(values)
        return {'job':reels.public(row),'replayed':replayed}

    @app.post('/api/reels/{job_id}/cancel')
    async def reel_cancel(job_id:int):return {'job':await reels.cancel(job_id)}

    @app.post('/api/reels/{job_id}/resume')
    async def reel_resume(job_id:int):return {'job':await reels.resume(job_id)}

    @app.post('/api/reels/{job_id}/verify')
    async def reel_verify(job_id:int):return {'job':await reels.verify(job_id)}

    install_rest_v1(app,resources,source_routes=idea_routes.routes if not secondary_workspace else (),base_path=base_path)
    if not secondary_workspace:app.include_router(idea_routes)
    frontend=root/'frontend'
    if frontend.is_dir():app.mount('/static',StaticFiles(directory=frontend),name='static')

    @app.get('/',include_in_schema=False)
    @app.get('/schedule',include_in_schema=False)
    @app.get('/saved',include_in_schema=False)
    @app.get('/settings',include_in_schema=False)
    async def index():
        path=frontend/'index.html'
        return FileResponse(path) if path.is_file() else JSONResponse({'detail':'Giao diện Page Manager chưa được tạo.','code':'frontend_unavailable'},503)
    return app

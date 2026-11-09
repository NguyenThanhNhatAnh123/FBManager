"""A separate Page Manager ASGI workspace behind the same local entry port."""
import importlib
from datetime import datetime,timedelta
from pathlib import Path
import re
import sys
from uuid import UUID
from filelock import FileLock, Timeout
from starlette.responses import HTMLResponse, JSONResponse
from ..core.scheduler_health import TickHealth, shutdown_services


class PageWorkspace:
    def __init__(self,chrome_root,port,*,factory=None,identity_store=None,workspace_user_id=None,secondary_workspace=False):
        if (type(secondary_workspace) is not bool or secondary_workspace and workspace_user_id is None
                or workspace_user_id is not None and (type(workspace_user_id) is not int or workspace_user_id<=0 or identity_store is None)):
            raise ValueError('Page workspace identity binding is invalid.')
        self.root=Path(chrome_root).resolve().parent/'meta-page-manager'
        self.port=port;self.factory=factory;self.application=None;self.lock=None
        self.identity_store=identity_store
        self.workspace_user_id=workspace_user_id
        self.secondary_workspace=secondary_workspace
        self.reason='Page Manager chưa sẵn sàng trong thư mục ứng dụng riêng.'
        self._publication_media_resolver=None
        self._publication_status_resolver=None
        self._tick_health=TickHealth()
        self._stopping=False
        self.shutdown_failures={}

    @staticmethod
    def _assert_namespace(backend):
        package=(backend/'page_manager').resolve()
        for name,module in list(sys.modules.items()):
            if name!='page_manager' and not name.startswith('page_manager.'):continue
            location=getattr(module,'__file__',None)
            if not location or not Path(location).resolve().is_relative_to(package):raise ValueError()
            for path in getattr(module,'__path__',()):
                if not Path(path).resolve().is_relative_to(package):raise ValueError()

    def start(self):
        if self.application is not None:return True
        backend=self.root/'backend'
        if self.root.is_symlink() or self.root.resolve()!=self.root or not (backend/'page_manager'/'app.py').is_file():return False
        try:
            data=self.root/'data'
            lock_path=data/'page-manager.lock'
            if self.workspace_user_id is not None:
                from via_auth.store import _assert_private_path
                _assert_private_path(data,directory=True,missing=True)
                _assert_private_path(lock_path,missing=True)
                for suffix in ('','-wal','-shm','-journal'):
                    _assert_private_path(data/('page-manager.sqlite3'+suffix),missing=True)
            if data.is_symlink() or data.exists() and data.resolve()!=data:raise ValueError()
            data.mkdir(parents=True,exist_ok=True)
            if lock_path.is_symlink() or lock_path.exists() and lock_path.stat().st_nlink>1:raise ValueError()
            self.lock=FileLock(str(lock_path));self.lock.acquire(timeout=0)
            factory=self.factory
            if factory is None:
                self._assert_namespace(backend)
                if str(backend) not in sys.path:sys.path.insert(0,str(backend))
                module=importlib.import_module('page_manager.app')
                self._assert_namespace(backend)
                factory=module.create_app
            options={'port':self.port,'base_path':'/pages'}
            if self.identity_store is not None:options['identity_store']=self.identity_store
            if self.workspace_user_id is not None:
                options.update(workspace_user_id=self.workspace_user_id,secondary_workspace=self.secondary_workspace)
            self.application=factory(self.root,**options)
            self._stopping=False
            self._tick_health=TickHealth()
            self._register_publication_media()
            self.reason=''
            return True
        except Timeout:
            self.reason='Page Manager đang chạy riêng. Dừng cửa sổ Page Manager riêng rồi mở lại Chrome Manager để dùng chung cổng.'
        except Exception:
            self.reason='Chưa mở được Page Manager. Kiểm tra mã nguồn và quyền truy cập thư mục ứng dụng riêng.'
        self.close()
        return False

    def close(self):
        self.application=None
        if self.lock is not None:
            self.lock.release();self.lock=None

    @staticmethod
    def _posting_blocked(reason='page_policy_unavailable'):
        return {'page_id':None,'can_publish':False,'information_status':'not_updated','reason':reason,'revision':0}

    @classmethod
    def _posting_projection(cls,value):
        if not isinstance(value,dict):return cls._posting_blocked()
        pid=value.get('page_id');ready=value.get('can_publish');revision=value.get('revision')
        reason=value.get('reason');information=value.get('information_status')
        valid_id=isinstance(pid,str) and re.fullmatch(r'[1-9][0-9]{0,31}',pid)
        if (type(ready) is not bool or type(revision) is not int or not 0<=revision<=2**63-1
                or reason is not None and not isinstance(reason,str)
                or pid is not None and not valid_id
                or ready and (not valid_id or revision<1 or reason is not None or information!='updated')
                or not ready and (information!='not_updated' or reason not in {'page_information_required','page_identity_unverified','page_policy_unavailable'})):
            return cls._posting_blocked()
        return {'page_id':pid,'can_publish':ready,'information_status':information,'reason':reason,'revision':revision}

    def posting_eligibility(self,page_url):
        """Delegate bounded metadata to the mounted owner; never open its database."""
        manager=getattr(getattr(self.application,'state',None),'manager',None)
        method=getattr(manager,'posting_eligibility',None)
        if not callable(method):return self._posting_blocked()
        try:return self._posting_projection(method(page_url))
        except Exception:return self._posting_blocked()

    def record_chrome_setup(self,page_url,receipt_id,field_fingerprints):
        """Private trusted Python handoff after readback, without an HTTP approval endpoint."""
        manager=getattr(getattr(self.application,'state',None),'manager',None)
        method=getattr(manager,'record_chrome_setup',None)
        if not callable(method):return self._posting_blocked()
        try:return self._posting_projection(method(page_url,receipt_id,field_fingerprints))
        except Exception:return self._posting_blocked()

    @staticmethod
    def _approval_blocked():
        return {'required':True,'can_publish':False,'state':'unavailable','reason':'publication_approval_unavailable',
                'request_id':None,'binding_hash':None,'approve_by':None,'publish_window_end':None}

    def publication_approval(self,binding,*,create=False):
        """Private bounded metadata bridge; Chrome never opens the Page database."""
        service=getattr(getattr(self.application,'state',None),'publication_approvals',None)
        method=getattr(service,'ensure' if create else 'verdict',None)
        if not callable(method):return self._approval_blocked()
        try:
            value=method(binding)
            if not isinstance(value,dict) or type(value.get('required')) is not bool or type(value.get('can_publish')) is not bool:return self._approval_blocked()
            states={'not_required','pending','approved','expired','rejected','cancelled','binding_changed','unavailable'}
            if value.get('state') not in states:return self._approval_blocked()
            if value['can_publish'] and not (value['state']=='not_required' and not value['required'] or value['state']=='approved' and value['required']):return self._approval_blocked()
            if value['can_publish'] and value.get('reason') is not None:return self._approval_blocked()
            reasons={'publication_approval_required','publication_approval_expired','publication_approval_rejected',
                'publication_approval_cancelled','publication_binding_changed','publication_approval_unavailable'}
            if not value['can_publish'] and (not value['required'] or value.get('reason') not in reasons):return self._approval_blocked()
            fields=[value.get(key) for key in ('request_id','binding_hash','approve_by','publish_window_end')]
            if any(item is not None for item in fields):
                request,digest,deadline,window=fields
                if not isinstance(request,str) or str(UUID(request))!=request or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest):return self._approval_blocked()
                if binding.get('approval_policy')=='remind_until_decision_v1':
                    if value.get('approval_policy')!=binding['approval_policy'] or deadline is not None or value.get('reminder_interval_seconds')!=300:return self._approval_blocked()
                    due=datetime.fromisoformat(binding['scheduled_at'].replace('Z','+00:00'))
                    reported_due=datetime.fromisoformat(value['due_at'].replace('Z','+00:00'))
                    if due.tzinfo is None or reported_due.tzinfo is None or due!=reported_due:return self._approval_blocked()
                    approved=value.get('approved_at')
                    if approved is None:
                        if window is not None or value['state']=='approved':return self._approval_blocked()
                    else:
                        approved=datetime.fromisoformat(approved.replace('Z','+00:00'))
                        end=datetime.fromisoformat(window.replace('Z','+00:00'))
                        if approved.tzinfo is None or end.tzinfo is None or end-max(due,approved)!=timedelta(minutes=30):return self._approval_blocked()
                    if value['state']=='pending' and approved is not None:return self._approval_blocked()
                else:
                    end=datetime.fromisoformat(window.replace('Z','+00:00'));due=datetime.fromisoformat(deadline.replace('Z','+00:00'))
                    if due.tzinfo is None or end.tzinfo is None or end-due!=timedelta(minutes=30):return self._approval_blocked()
            elif value['state']=='approved':return self._approval_blocked()
            keys=set(self._approval_blocked())
            if binding.get('approval_policy')=='remind_until_decision_v1':
                keys.update(('approval_policy','reminder_interval_seconds','due_at','approved_at'))
            return {key:value.get(key) for key in keys}
        except Exception:return self._approval_blocked()

    def withdraw_publication(self,job_id):
        service=getattr(getattr(self.application,'state',None),'publication_approvals',None)
        method=getattr(service,'withdraw',None)
        if callable(method):
            try:method('chrome',job_id)
            except Exception:pass

    def register_publication_media_resolver(self,callback):
        if not callable(callback):raise ValueError('Invalid media resolver')
        self._publication_media_resolver=callback;self._register_publication_media()

    def register_publication_status_resolver(self,callback):
        if not callable(callback):raise ValueError('Invalid publication status resolver')
        self._publication_status_resolver=callback;self._register_publication_media()

    def _register_publication_media(self):
        state=getattr(self.application,'state',None)
        if state is not None and self._publication_media_resolver is not None:
            resolvers=getattr(state,'publication_media_resolvers',{})
            if not isinstance(resolvers,dict):resolvers={}
            resolvers['chrome']=self._publication_media_resolver
            state.publication_media_resolvers=resolvers
        if state is not None and self._publication_status_resolver is not None:
            resolvers=getattr(state,'publication_status_resolvers',{})
            if not isinstance(resolvers,dict):resolvers={}
            resolvers['chrome']=self._publication_status_resolver
            state.publication_status_resolvers=resolvers

    def tick(self):
        if self._stopping:return
        state=getattr(self.application,'state',None)
        for name in ('telegram_approval','idea_views','idea_runs','idea_workspace','idea_automation','video_source','reels'):
            service=getattr(state,name,None)
            if service is not None:self._tick_health.call(name,service.tick)

    def scheduler_health(self):
        result=self._tick_health.snapshot()
        return {**result,'ready':self.application is not None and not self._stopping and result['ready']}

    async def shutdown(self):
        self._stopping=True
        state=getattr(self.application,'state',None)
        services=[(name,getattr(state,name,None)) for name in ('telegram_approval','idea_automation',
            'producer_diagnostics','idea_views','idea_runs','idea_workspace','video_source','reels')]
        self.shutdown_failures=await shutdown_services((name,service.shutdown) for name,service in services if service is not None)

    async def __call__(self,scope,receive,send):
        if self.application is not None:return await self.application(scope,receive,send)
        if scope['type']!='http':return
        path=scope.get('path','');prefix=scope.get('root_path','')
        if prefix and path.startswith(prefix):path=path[len(prefix):]
        if path.startswith('/api/'):
            response=JSONResponse({'detail':self.reason,'code':'page_workspace_unavailable'},503)
        else:
            response=HTMLResponse('<!doctype html><html lang="vi"><meta charset="utf-8"><title>Page Manager</title><body><h1>Page Manager</h1><p>'+self.reason+'</p><p>Dữ liệu Page Manager được giữ riêng; Chrome Manager vẫn hoạt động.</p><a href="/">Về Chrome Manager</a></body></html>',503)
        return await response(scope,receive,send)

"""Safe startup metadata: never persist exception messages, tracebacks or argv."""
from contextlib import suppress
from datetime import datetime,timezone
import json
import logging
import os
from pathlib import Path
import re
from uuid import UUID,uuid4


def failure_reason(error):
    current=error
    for _ in range(8):
        if current is None:break
        if isinstance(current,OSError) and (getattr(current,'errno',None) in (48,98,10048) or getattr(current,'winerror',None)==10048):return 'port_in_use'
        if type(current).__name__=='Timeout' and type(current).__module__.startswith('filelock'):return 'already_running'
        if isinstance(current,PermissionError):return 'permission_denied'
        if isinstance(current,ModuleNotFoundError):return 'missing_dependency'
        if type(current).__name__=='OperationalError':return 'database_unavailable'
        current=current.__cause__ or current.__context__
    return 'startup_exception'


class StartupDiagnostics:
    def __init__(self,root):
        self.root=Path(root);self.failed=False;self.port=None
        try:self.attempt_id=str(UUID(os.environ.get('CM_STARTUP_ATTEMPT_ID','')))
        except (ValueError,TypeError):self.attempt_id=str(uuid4())

    def _write(self,name,value):
        folder=self.root/'logs';temporary=folder/('.'+name+'.'+self.attempt_id+'.tmp')
        with suppress(OSError):
            folder.mkdir(parents=True,exist_ok=True)
            with temporary.open('w',encoding='utf-8') as stream:json.dump(value,stream,indent=2)
            temporary.replace(folder/name)

    def status(self,state,*,reason=None,port=None,pid=None):
        if port is not None:self.port=port
        value={'state':state,'reason':reason,'port':self.port,'pid':pid or os.getpid(),
               'checked_at':datetime.now(timezone.utc).isoformat(),'attempt_id':self.attempt_id}
        self._write('startup-status.json',value)

    def failure(self,stage,error=None,*,reason=None,exit_code=3,port=None):
        if port is not None:self.port=port
        self.failed=True
        kind=type(error).__name__ if error is not None else None
        kind=kind if kind and re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]{0,79}',kind) else None
        value={'step':'run.py','program':'python.exe','exit_code':exit_code,'stage':stage,
               'reason':reason or failure_reason(error),'exception_type':kind,
               'errno':getattr(error,'errno',None),'winerror':getattr(error,'winerror',None),
               'port':self.port,'checked_at':datetime.now(timezone.utc).isoformat(),'attempt_id':self.attempt_id}
        for key in ('errno','winerror'):
            if type(value[key]) is not int:value[key]=None
        self._write('startup-error.json',value)
        self.status('failed',reason=value['reason'])


class StartupASGI:
    def __init__(self,application,diagnostics):
        self.application,self.diagnostics=application,diagnostics

    async def __call__(self,scope,receive,send):
        if scope['type']!='lifespan':return await self.application(scope,receive,send)
        started=False
        async def observe(message):
            nonlocal started
            if message['type']=='lifespan.startup.complete':started=True
            elif message['type']=='lifespan.startup.failed':self.diagnostics.failure('asgi_startup')
            await send(message)
        try:await self.application(scope,receive,observe)
        except BaseException as error:
            if not started:self.diagnostics.failure('asgi_startup',error)
            raise


class StartupObserver(logging.Handler):
    """Uvicorn logs ready only after lifespan success AND a bound listener."""
    def __init__(self,diagnostics,on_ready=None):
        super().__init__();self.diagnostics=diagnostics;self.on_ready=on_ready;self.ready=False

    def emit(self,record):
        if not self.ready and isinstance(record.msg,OSError):self.diagnostics.failure('socket_bind',record.msg)
        elif isinstance(record.msg,str) and record.msg.startswith('Uvicorn running on ') and not self.ready:
            self.ready=True;self.diagnostics.status('running',reason='started')
            if self.on_ready is not None:
                try:self.on_ready()
                except Exception:self.diagnostics.status('running',reason='browser_open_failed')

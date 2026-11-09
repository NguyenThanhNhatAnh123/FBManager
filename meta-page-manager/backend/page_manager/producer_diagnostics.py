"""Explicit, bounded CLI connection checks. No prompts, login, generation or failover."""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from uuid import UUID

from .errors import AppError

PROVIDERS=('codex','claude')
TTL_SECONDS=300
COMMAND_TIMEOUT=3
CHECK_TIMEOUT=14  # Reserve one second to reap an owned child on timeout.
MAX_OUTPUT_BYTES=64*1024


@dataclass(frozen=True)
class CommandResult:
    code:int|None=None
    stdout:str=''
    stderr:str=''
    reason:str|None=None


class _OutputLimit(Exception):pass


async def run_command(argv,timeout=COMMAND_TIMEOUT):
    """Capture bounded private output and reap only this owned child on cancellation."""
    process=None;readers=[]
    try:
        environment={**os.environ,'DISABLE_AUTOUPDATER':'1','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1'}
        process=await asyncio.create_subprocess_exec(*map(str,argv),stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,env=environment,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        async def read(stream):
            value=bytearray()
            while chunk:=await stream.read(8192):
                value.extend(chunk)
                if len(value)>MAX_OUTPUT_BYTES:raise _OutputLimit()
            return bytes(value).decode('utf-8',errors='replace')
        async def collect():
            readers.extend([asyncio.create_task(read(process.stdout)),asyncio.create_task(read(process.stderr))])
            stdout,stderr=await asyncio.gather(*readers)
            await process.wait()
            return CommandResult(process.returncode,stdout,stderr)
        return await asyncio.wait_for(collect(),timeout)
    except asyncio.TimeoutError:return CommandResult(reason='timeout')
    except _OutputLimit:return CommandResult(reason='output_limit')
    except (OSError,ValueError):return CommandResult(reason='unavailable')
    finally:
        for task in readers:
            if not task.done():task.cancel()
        if readers:await asyncio.gather(*readers,return_exceptions=True)
        if process is not None and process.returncode is None:
            try:process.kill()
            except ProcessLookupError:pass
            try:await asyncio.wait_for(process.wait(),1)
            except asyncio.TimeoutError:pass


def resolve_cli(provider):
    """Only existing absolute native executables, never shell wrappers or command text."""
    variable='PM_CODEX_CLI' if provider=='codex' else 'PM_CLAUDE_CLI'
    if variable in os.environ:return Path(os.environ[variable]),'environment'
    if provider=='codex':
        # Match the production worker's actual executable selection, without launching it.
        from .idea_runs import _cli_path
        return _cli_path(),'default'
    preferred=Path.home()/'.local'/'bin'/'claude.exe'
    if preferred.is_file():return preferred,'default'
    located=shutil.which('claude.exe')
    return (Path(located),'path') if located else (None,'unavailable')


def _version(value):
    match=re.search(r'\b(\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)\b',value)
    return match[1] if match and len(match[1])<=40 else None


class ProducerDiagnostics:
    def __init__(self,*,resolver=resolve_cli,runner=run_command,clock=None,capability_provider=None):
        self.resolver,self.runner=resolver,runner
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.cache={};self.signatures={};self.checking=set();self.inflight=None
        self.capability_provider=capability_provider

    def _capabilities(self):
        if not callable(self.capability_provider):return None
        from .producer_evidence import REASONS,TTL_SECONDS as EVIDENCE_TTL
        result={'image_verified':False,'production_verified':False,'checked_at':None,'last_run_id':None,
                'reason':'evidence_unavailable'}
        try:
            value=self.capability_provider()
            if not isinstance(value,dict):return result
            result['reason']=value.get('reason') if value.get('reason') in REASONS else 'evidence_unavailable'
            checked=value.get('checked_at')
            if checked is not None:
                if not isinstance(checked,str) or len(checked)>64:return result
                stamp=datetime.fromisoformat(checked.replace('Z','+00:00'))
                if stamp.tzinfo is None or stamp.utcoffset() is None or not 0<=(self.clock()-stamp).total_seconds()<=EVIDENCE_TTL:return result
                result['checked_at']=stamp.astimezone(timezone.utc).isoformat()
                result['image_verified']=value.get('image_verified') is True
                run_id=value.get('last_run_id')
                valid_run=isinstance(run_id,str) and str(UUID(run_id))==run_id and UUID(run_id).int!=0
                result['production_verified']=result['image_verified'] and value.get('production_verified') is True and valid_run
                if result['production_verified']:result['last_run_id']=run_id
        except Exception:return {'image_verified':False,'production_verified':False,'checked_at':None,'last_run_id':None,'reason':'evidence_unavailable'}
        return result

    def _selection(self,provider):
        source='unavailable'
        try:
            path,source=self.resolver(provider)
            if source not in ('environment','default','path','unavailable'):source='unavailable'
            if path is None:return None,source,None
            path=Path(path)
            if not path.is_absolute() or path.suffix.lower()!='.exe' or path.drive.startswith('\\\\'):return None,source,None
            path=path.resolve(strict=True)
            if path.suffix.lower()!='.exe' or path.drive.startswith('\\\\'):return None,source,None
            info=path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size<=0:return None,source,None
            return path,source,(str(path),info.st_size,info.st_mtime_ns)
        except (OSError,ValueError,TypeError):return None,source,None

    @staticmethod
    def _base(provider,installed,source):
        reason='check_required' if installed else 'cli_configuration_invalid' if source=='environment' else 'cli_not_installed'
        return {'installed':installed,'authenticated':None,'checked_at':None,'expires_at':None,
                'status':'not_checked' if installed else 'unavailable','reason':reason,'version':None,
                'checking':False,'configuration_source':source,
                'image_generation':{'configured':None,'flag_supported':None,'verified':False,
                    'reason':'backup_adapter_not_configured' if provider=='claude' else 'not_checked'},
                'production_verified':False}

    def snapshot(self):
        providers={};now=self.clock();capabilities=self._capabilities()
        for provider in PROVIDERS:
            path,source,signature=self._selection(provider)
            value=self._base(provider,path is not None,source)
            cached=self.cache.get(provider)
            if cached and self.signatures.get(provider)==signature:
                value=deepcopy(cached);value.update(installed=path is not None,configuration_source=source)
                if path is None:
                    value.update(authenticated=None,status='unavailable',reason='cli_configuration_invalid' if source=='environment' else 'cli_not_installed')
                elif now>=datetime.fromisoformat(value['expires_at']):
                    value.update(authenticated=None,status='stale',reason='check_expired')
            elif cached and path is not None:
                value['reason']='cli_changed'
            if provider in self.checking:
                value.update(checking=True,authenticated=None,status='checking',reason='check_in_progress')
            if provider=='codex' and capabilities is not None:
                value['image_generation']['verified']=capabilities['image_verified']
                value['production_verified']=capabilities['production_verified']
                value['capability_evidence']=capabilities
            providers[provider]=value
        return {'primary':'codex','backup':'claude','providers':providers,
                'fallback':{'mode':'manual_review','automatic':False,'reason':'backup_requires_manual_review'}}

    async def _run(self,path,*args):
        try:
            value=await self.runner([str(path),*args],COMMAND_TIMEOUT)
            if not isinstance(value,CommandResult):return CommandResult(reason='unavailable')
            if (not isinstance(value.stdout,str) or not isinstance(value.stderr,str)
                    or value.code is not None and type(value.code) is not int):return CommandResult(reason='unavailable')
            if len(value.stdout.encode('utf-8'))>MAX_OUTPUT_BYTES or len(value.stderr.encode('utf-8'))>MAX_OUTPUT_BYTES:
                return CommandResult(reason='output_limit')
            return value
        except asyncio.CancelledError:raise
        except Exception:return CommandResult(reason='unavailable')

    @staticmethod
    def _auth_failure(result):
        if result.reason=='timeout':return 'auth_check_timeout'
        if result.reason=='output_limit':return 'output_limit'
        if result.reason=='unavailable':return 'executable_unavailable'
        return 'auth_check_failed'

    async def _diagnose(self,provider):
        path,source,signature=self._selection(provider)
        value=self._base(provider,path is not None,source)
        if path is not None:
            version=await self._run(path,'--version')
            if version.code==0 and version.reason is None:value['version']=_version(version.stdout+'\n'+version.stderr)
            if provider=='codex':
                auth=await self._run(path,'login','status')
                if auth.reason is None and auth.code==0:value['authenticated']=True
                elif auth.reason is None and re.search(r'not (?:logged in|authenticated)|login required|signed out',auth.stdout+'\n'+auth.stderr,re.I):
                    value['authenticated']=False
                else:value['reason']=self._auth_failure(auth)
                features=await self._run(path,'features','list')
                image=value['image_generation']
                matches=re.findall(r'^image_generation\s+\S+\s+(true|false)\s*$',features.stdout,re.M)
                if features.code==0 and features.reason is None:
                    image.update(flag_supported=bool(matches),configured=matches[0]=='true' if len(matches)==1 else None,
                                 reason='cli_feature_only' if matches==['true'] else 'flag_disabled' if matches==['false'] else 'feature_unsupported')
                else:image['reason']='feature_check_timeout' if features.reason=='timeout' else 'feature_check_failed'
            else:
                help_result=await self._run(path,'auth','status','--help')
                supported=(help_result.code==0 and help_result.reason is None
                           and re.search(r'^Usage:\s*claude\s+auth\s+status\b',help_result.stdout,re.M|re.I)
                           and '--json' in help_result.stdout)
                if not supported:value['reason']='auth_check_timeout' if help_result.reason=='timeout' else 'auth_status_unsupported'
                else:
                    auth=await self._run(path,'auth','status','--json')
                    if auth.reason is not None:value['reason']=self._auth_failure(auth)
                    else:
                        try:
                            data=json.loads(auth.stdout)
                            authenticated=data.get('loggedIn')
                            if type(authenticated) is not bool:raise ValueError()
                            if auth.code not in (0,1) or authenticated and auth.code!=0:
                                value['reason']='auth_check_failed'
                            else:value['authenticated']=authenticated
                        except (ValueError,TypeError,AttributeError,RecursionError):value['reason']='auth_response_invalid'
            if value['authenticated'] is True:value.update(status='cli_ready',reason=None)
            elif value['authenticated'] is False:value.update(status='not_authenticated',reason='not_authenticated')
            else:value['status']='check_failed'
        checked=self.clock()
        value.update(checked_at=checked.isoformat(),expires_at=(checked+timedelta(seconds=TTL_SECONDS)).isoformat())
        return value,signature

    async def _perform(self,selected):
        async def one(provider):
            try:
                value,signature=await asyncio.wait_for(self._diagnose(provider),CHECK_TIMEOUT)
            except asyncio.TimeoutError:
                path,source,signature=self._selection(provider);value=self._base(provider,path is not None,source)
                checked=self.clock();value.update(status='check_failed',reason='diagnostic_timeout',
                    checked_at=checked.isoformat(),expires_at=(checked+timedelta(seconds=TTL_SECONDS)).isoformat())
            self.cache[provider]=value;self.signatures[provider]=signature
        try:await asyncio.gather(*(one(provider) for provider in selected))
        finally:self.checking.difference_update(selected)

    async def check(self,provider='all'):
        if provider not in (*PROVIDERS,'all'):raise AppError('Chọn Codex, Claude hoặc cả hai.','invalid_producer_provider',422)
        if self.inflight is not None and not self.inflight.done():return self.snapshot()
        selected=PROVIDERS if provider=='all' else (provider,)
        self.checking.update(selected)
        self.inflight=asyncio.create_task(self._perform(selected))
        await asyncio.shield(self.inflight)
        return self.snapshot()

    async def shutdown(self):
        task=self.inflight
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        self.inflight=None

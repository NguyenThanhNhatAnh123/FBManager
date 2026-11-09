"""Existing-instance recognition with injected sockets/processes and temp locks."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import psutil
import pytest
from filelock import FileLock

from app.core import startup


@pytest.fixture
def config(tmp_path):
    root=tmp_path/'manager';root.mkdir()
    return SimpleNamespace(root=root,data_dir=root/'data',port=8000)


def inspect(config,*,pids=(),held=False,matching=True,ready=True,**kwargs):
    return startup.inspect_existing_instance(config,timeout=0,listeners=lambda port:list(pids),
        same_host=lambda pid,root:matching,lock_probe=lambda path:held,http_probe=lambda port,timeout:ready,**kwargs)


def test_stopped_requires_both_free_lock_and_no_listener(config):
    assert inspect(config)=={'state':'stopped','port':8000,'reason':'no_instance'}
    assert inspect(config,held=True)['state']=='busy'


def test_running_requires_all_three_independent_identity_checks(config):
    assert inspect(config,pids=[123],held=True)=={'state':'running','port':8000,'pid':123,'reason':'existing_instance'}
    assert inspect(config,pids=[123],held=False)['reason']=='instance_identity_unverified'
    assert inspect(config,pids=[123],held=True,matching=False)['reason']=='port_in_use'
    assert inspect(config,pids=[123],held=True,ready=False)['state']=='busy'


@pytest.mark.parametrize('pids',[[None],[123,456],[0],[-1],[True]])
def test_unknown_or_multiple_listener_identity_is_never_reused(config,pids):
    assert inspect(config,pids=pids,held=True)['reason']=='instance_identity_unverified'


def test_inspection_errors_fail_closed_without_commandline_or_exception_text(config):
    def denied(_port):raise psutil.AccessDenied(123,msg='PRIVATE command cookie')
    result=startup.inspect_existing_instance(config,timeout=0,listeners=denied,lock_probe=lambda path:False)
    assert result=={'state':'unavailable','port':8000,'reason':'instance_probe_unavailable'}
    assert 'PRIVATE' not in json.dumps(result)


def test_starting_host_is_polled_with_bounded_wait_and_can_become_ready(config):
    elapsed=[0];checks=[]
    def listeners(port):return [] if elapsed[0]<.2 else [123]
    def sleep(seconds):
        assert 0<seconds<=.100001
        elapsed[0]+=seconds
    result=startup.inspect_existing_instance(config,timeout=.5,listeners=listeners,
        same_host=lambda pid,root:True,lock_probe=lambda path:True,
        http_probe=lambda port,timeout:checks.append(timeout) or True,monotonic=lambda:elapsed[0],sleep=sleep)
    assert result['state']=='running' and .2<=elapsed[0]<=.5
    assert checks and all(.05<=value<=.35 for value in checks)


def test_starting_timeout_does_not_start_or_kill_any_process(config):
    elapsed=[0]
    result=startup.inspect_existing_instance(config,timeout=.3,listeners=lambda port:[],
        lock_probe=lambda path:True,monotonic=lambda:elapsed[0],sleep=lambda seconds:elapsed.__setitem__(0,elapsed[0]+seconds))
    assert result=={'state':'busy','port':8000,'reason':'instance_starting'}
    assert .3<=elapsed[0]<=.300001


@pytest.mark.parametrize('timeout',[True,-1,6,float('nan'),float('inf'),'1'])
def test_probe_timeout_must_be_finite_and_bounded(config,timeout):
    with pytest.raises(ValueError):startup.inspect_existing_instance(config,timeout=timeout)


@pytest.mark.parametrize('arguments,matching',[
    (['-B','backend/run.py'],True),(['run.py'],True),
    (['-X','utf8','run.py'],True),(['-m','app.main','run.py'],False),
    (['-c','print(1)','run.py'],False),(['-cprint(1)','run.py'],False),
    (['-mapp.main','run.py'],False),(['different.py','run.py'],False)])
def test_process_identity_requires_actual_python_script_argument(config,monkeypatch,arguments,matching):
    cwd=config.root if arguments[0]=='-B' else config.root/'backend'
    process=SimpleNamespace(exe=lambda:str(Path(sys.executable)),cmdline=lambda:['python.exe',*arguments],cwd=lambda:str(cwd))
    monkeypatch.setattr(startup.psutil,'Process',lambda pid:process)
    assert startup._same_host(123,config.root) is matching


def test_other_installation_or_non_python_executable_is_not_reused(config,monkeypatch):
    process=SimpleNamespace(exe=lambda:'other.exe',cmdline=lambda:['other.exe',str(config.root/'backend/run.py')])
    monkeypatch.setattr(startup.psutil,'Process',lambda pid:process)
    assert not startup._same_host(123,config.root)
    process.exe=lambda:sys.executable
    process.cmdline=lambda:[sys.executable,str(config.root.parent/'other/backend/run.py')]
    assert not startup._same_host(123,config.root)


@pytest.mark.parametrize('body,status,expected',[(b'{"ok":true}',200,True),
    (b'{"ok":true,"rest_api_version":1}',200,True),(b'{"ok":1}',200,False),
    (b'{"ok":true,"rest_api_version":true}',200,False),(b'{"ok":true,"rest_api_version":"1"}',200,False),
    (b'{"ok":true,"rest_api_version":null}',200,False),(b'{"ok":true,"rest_api_version":2}',200,False),
    (b'{"ok":false,"rest_api_version":1}',200,False),(b'{"rest_api_version":1}',200,False),
    (b'{"ok":true,"extra":1}',200,False),(b'{"ok":true}',302,False),(b'x'*257,200,False)])
def test_session_probe_is_direct_bounded_and_accepts_only_supported_metadata(monkeypatch,body,status,expected):
    calls=[]
    monkeypatch.setattr(startup,'_service_headers',lambda:{})
    class Connection:
        def __init__(self,*args,**kwargs):calls.append((args,kwargs))
        def request(self,*args,**kwargs):calls.append((args,kwargs))
        def getresponse(self):return SimpleNamespace(status=status,read=lambda amount:body[:amount])
        def close(self):calls.append('closed')
    monkeypatch.setattr(startup.http.client,'HTTPConnection',Connection)
    assert startup._session_ready(8000,.2) is expected
    assert calls[0]==(('127.0.0.1',8000),{'timeout':.2}) and calls[-1]=='closed'
    assert calls[1][0]==('GET','/api/session')


@pytest.mark.parametrize('body,expected',[(b'{"ok":true}','running'),
    (b'{"ok":true,"rest_api_version":1}','running'),
    (b'{"ok":true,"rest_api_version":true}','busy')])
def test_instance_recognition_reuses_legacy_and_versioned_session_responses(config,monkeypatch,body,expected):
    class Connection:
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):pass
        def getresponse(self):return SimpleNamespace(status=200,read=lambda limit:body[:limit])
        def close(self):pass
    monkeypatch.setattr(startup.http.client,'HTTPConnection',Connection)
    monkeypatch.setattr(startup,'_service_headers',lambda:{})
    result=startup.inspect_existing_instance(config,timeout=0,listeners=lambda port:[123],
        same_host=lambda pid,root:True,lock_probe=lambda path:True)
    assert result['state']==expected
    assert result['pid']==123


@pytest.mark.parametrize('credential',[{}, {'X-Via-Service-Token':'INERT-FIXTURE-KEY-NEVER-LIVE'}])
def test_session_probe_recognizes_old_and_identity_protected_hosts(monkeypatch,credential):
    calls=[]
    class Connection:
        def __init__(self,*args,**kwargs):
            assert args==('127.0.0.1',8000) and kwargs=={'timeout':.2}
        def request(self,method,path,*,headers):
            assert (method,path)==('GET','/api/session')
            calls.append(headers)
        def getresponse(self):
            return SimpleNamespace(status=200,read=lambda limit:b'{"ok":true}')
        def close(self):pass
    monkeypatch.setattr(startup.http.client,'HTTPConnection',Connection)
    monkeypatch.setattr(startup,'_service_headers',lambda:dict(credential))
    assert startup._session_ready(8000,.2)
    assert calls==[{'Connection':'close',**credential}]


def test_invalid_service_credential_is_not_sent_and_probe_closes_connection(monkeypatch):
    calls=[]
    class Connection:
        def __init__(self,*_a,**_kw):pass
        def request(self,*_a,**_kw):raise AssertionError('Unsafe credential request was sent')
        def close(self):calls.append('closed')
    monkeypatch.setattr(startup.http.client,'HTTPConnection',Connection)
    monkeypatch.setattr(startup,'_service_headers',lambda:(_ for _ in ()).throw(ValueError('INERT PRIVATE DETAIL')))
    with pytest.raises(ValueError):startup._session_ready(8000,.2)
    assert calls==['closed']


def test_service_header_loading_is_read_only_and_never_returns_unrelated_secrets(tmp_path,monkeypatch):
    from via_auth import client as client_module
    expected={'X-Via-Service-Token':'INERT-FIXTURE-KEY-ONLY'}
    monkeypatch.setattr(client_module,'service_headers',lambda:expected.copy())
    assert startup._service_headers()==expected
    def unsafe():raise OSError('PRIVATE FILE CONTENTS')
    monkeypatch.setattr(client_module,'service_headers',unsafe)
    with pytest.raises(ValueError) as error:startup._service_headers()
    assert str(error.value)=='Local service credential unavailable' and error.value.__cause__ is None


def test_lock_probe_never_creates_truncates_or_deletes_file(tmp_path):
    path=tmp_path/'manager.lock'
    assert not startup._lock_held(path) and not path.exists()
    path.write_bytes(b'preserve-this-metadata')
    before=path.stat()
    assert not startup._lock_held(path)
    assert path.read_bytes()==b'preserve-this-metadata' and path.stat().st_mtime_ns==before.st_mtime_ns
    with FileLock(path):
        before=path.stat()
        assert startup._lock_held(path)
        assert path.stat().st_size==before.st_size and path.stat().st_mtime_ns==before.st_mtime_ns


@pytest.mark.parametrize('state,record',[('running',False),('stopped',True),('running',True),('busy',True),('unavailable',True)])
def test_cli_probe_is_read_only_unless_record_requested(config,monkeypatch,capsys,state,record):
    from app.core import config as config_module
    script=Path(__file__).resolve().parents[1]/'scripts/startup_state.py'
    spec=importlib.util.spec_from_file_location('fixture_startup_state',script)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    result={'state':state,'port':8000,'reason':{'running':'existing_instance','stopped':'no_instance',
            'busy':'instance_starting','unavailable':'port_in_use'}[state]}
    if state=='running':result['pid']=123
    monkeypatch.setattr(config_module,'Config',lambda:config)
    monkeypatch.setattr(startup,'inspect_existing_instance',lambda actual:result)
    assert module.main(['--record'] if record else [])==0
    assert json.loads(capsys.readouterr().out)==result
    logs=config.root/'logs'
    if not record:assert not logs.exists()
    else:
        status=json.loads((logs/'startup-status.json').read_text(encoding='utf-8'))
        if state in ('busy','unavailable'):
            error=json.loads((logs/'startup-error.json').read_text(encoding='utf-8'))
            assert error['stage']=='preflight' and error['reason']==result['reason']
            assert status['state']=='failed'
        else:
            assert status['state']==('running' if state=='running' else 'checking')
            assert not (logs/'startup-error.json').exists()


@pytest.mark.parametrize('same_attempt',[False,True])
def test_powershell_preserves_only_current_richer_run_diagnostics(tmp_path,same_attempt,monkeypatch):
    # Extract only the checked-command function. The launcher itself never runs.
    source=Path(__file__).resolve().parents[1]/'scripts/start.ps1'
    from app.core.startup_diagnostics import StartupDiagnostics
    attempt='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    monkeypatch.setenv('CM_STARTUP_ATTEMPT_ID',attempt if same_attempt else 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
    StartupDiagnostics(tmp_path).failure('app_lock',RuntimeError('PRIVATE exception'),reason='already_running')
    error=tmp_path/'logs/startup-error.json';saved=json.loads(error.read_text(encoding='utf-8'))
    runner=tmp_path/'run.py';runner.write_text('raise SystemExit(3)',encoding='utf-8')
    harness=tmp_path/'probe.ps1'
    harness.write_text('''param($Source,$TaskRoot,$Python,$Runner,$Attempt)
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell parse failed'}
$definition=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-Checked'},$true)
Invoke-Expression $definition.Extent.Text
$projectRoot=$TaskRoot
$env:CM_STARTUP_ATTEMPT_ID=$Attempt
try{Invoke-Checked $Python @($Runner)}catch{}
Get-Content -LiteralPath (Join-Path $TaskRoot 'logs/startup-error.json') -Raw
''',encoding='utf-8')
    result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(harness),
        str(source),str(tmp_path),sys.executable,str(runner),attempt],capture_output=True,text=True,timeout=15,check=True)
    actual=json.loads(result.stdout.lstrip('\ufeff'))
    if same_attempt:assert actual==saved
    else:
        assert actual['step']=='run.py' and actual['exit_code']==3 and actual['attempt_id']==attempt
        assert actual['timestamp'] and 'stage' not in actual

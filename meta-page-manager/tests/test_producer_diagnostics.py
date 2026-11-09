"""Connection metadata only: all CLI responses/processes are synthetic."""
import asyncio
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import producer_diagnostics as module
from page_manager.producer_diagnostics import CommandResult,ProducerDiagnostics,MAX_OUTPUT_BYTES

PRIVATE='PRIVATE_AUTH_TOKEN_OR_ACCOUNT'


class Runner:
    def __init__(self):self.calls=[];self.overrides={}
    async def __call__(self,argv,timeout):
        provider=Path(argv[0]).stem;args=tuple(argv[1:]);self.calls.append((provider,args,timeout))
        value=self.overrides.get((provider,args))
        if value is not None:return value
        if args==('--version',):return CommandResult(0,'0.163.0' if provider=='codex' else '2.1.302')
        if provider=='codex' and args==('login','status'):return CommandResult(0,stderr=PRIVATE)
        if provider=='codex' and args==('features','list'):return CommandResult(0,'image_generation stable true\n')
        if provider=='claude' and args==('auth','status','--help'):return CommandResult(0,'Usage: claude auth status [options]\n --json')
        if provider=='claude' and args==('auth','status','--json'):
            return CommandResult(0,json.dumps({'loggedIn':True,'email':PRIVATE,'apiKey':PRIVATE}))
        raise AssertionError('No prompt, login or arbitrary CLI command is allowed')


def fixture(tmp_path):
    paths={}
    for provider in ('codex','claude'):
        path=tmp_path/(provider+'.exe');path.write_bytes(b'fixture native CLI');paths[provider]=path
    runner=Runner();clock=[datetime(2030,1,1,tzinfo=timezone.utc)]
    service=ProducerDiagnostics(resolver=lambda provider:(paths[provider],'default'),runner=runner,clock=lambda:clock[0])
    return service,runner,clock,paths


def test_get_snapshot_never_executes_cli_and_does_not_claim_auth_or_generation(tmp_path):
    service,runner,_,_=fixture(tmp_path)
    value=service.snapshot()
    assert runner.calls==[] and value['primary']=='codex' and value['backup']=='claude'
    assert value['fallback']=={'mode':'manual_review','automatic':False,'reason':'backup_requires_manual_review'}
    for provider in value['providers'].values():
        assert provider['installed'] and provider['authenticated'] is None and provider['checked_at'] is None
        assert provider['production_verified'] is False and provider['image_generation']['verified'] is False


def test_explicit_check_reports_only_bounded_cli_status_and_feature_configuration(tmp_path):
    service,runner,_,paths=fixture(tmp_path)
    value=asyncio.run(service.check('all'))
    codex,claude=(value['providers'][name] for name in ('codex','claude'))
    assert codex['status']==claude['status']=='cli_ready' and codex['authenticated'] is claude['authenticated'] is True
    assert codex['image_generation']=={'configured':True,'flag_supported':True,'verified':False,'reason':'cli_feature_only'}
    assert claude['image_generation']=={'configured':None,'flag_supported':None,'verified':False,'reason':'backup_adapter_not_configured'}
    assert all(not provider['production_verified'] for provider in (codex,claude))
    assert datetime.fromisoformat(codex['expires_at'])-datetime.fromisoformat(codex['checked_at'])==timedelta(seconds=300)
    serialized=json.dumps(value)
    assert PRIVATE not in serialized and all(str(path) not in serialized for path in paths.values())
    assert len(runner.calls)==6 and all(call[2]==module.COMMAND_TIMEOUT for call in runner.calls)


def test_expired_authentication_is_unknown_and_get_does_not_refresh_it(tmp_path):
    service,runner,clock,_=fixture(tmp_path);asyncio.run(service.check('codex'));count=len(runner.calls)
    clock[0]+=timedelta(seconds=300)
    result=service.snapshot()['providers']['codex']
    assert result['authenticated'] is None and result['status']=='stale' and result['reason']=='check_expired'
    assert len(runner.calls)==count


def test_recorded_capability_decorates_only_codex_without_claiming_live_authentication(tmp_path):
    service,runner,clock,_=fixture(tmp_path);run_id=str(uuid4())
    evidence={'image_verified':True,'production_verified':True,'checked_at':clock[0].isoformat(),
        'last_run_id':run_id,'reason':'verified','private_path':PRIVATE}
    service.capability_provider=lambda:evidence
    value=service.snapshot();codex=value['providers']['codex'];claude=value['providers']['claude']
    assert runner.calls==[] and codex['authenticated'] is None and codex['status']=='not_checked'
    assert codex['image_generation']['verified'] and codex['production_verified']
    assert codex['capability_evidence']['last_run_id']==run_id
    assert not claude['image_generation']['verified'] and not claude['production_verified']
    assert 'capability_evidence' not in claude and PRIVATE not in json.dumps(value)
    asyncio.run(service.check('codex'));clock[0]+=timedelta(seconds=300)
    codex=service.snapshot()['providers']['codex']
    assert codex['authenticated'] is None and codex['status']=='stale' and codex['production_verified']


@pytest.mark.parametrize('change',['future','stale','bad_time','bad_run','raises'])
def test_invalid_capability_callback_fails_closed_without_private_output(tmp_path,change):
    service,_,clock,_=fixture(tmp_path)
    evidence={'image_verified':True,'production_verified':True,'checked_at':clock[0].isoformat(),
        'last_run_id':str(uuid4()),'reason':'verified','private_path':PRIVATE}
    if change=='future':evidence['checked_at']=(clock[0]+timedelta(seconds=1)).isoformat()
    elif change=='stale':evidence['checked_at']=(clock[0]-timedelta(days=2)).isoformat()
    elif change=='bad_time':evidence['checked_at']=PRIVATE
    elif change=='bad_run':evidence['last_run_id']=PRIVATE
    def read():
        if change=='raises':raise RuntimeError(PRIVATE)
        return evidence
    service.capability_provider=read
    value=service.snapshot()['providers']['codex']
    assert not value['image_generation']['verified'] and not value['production_verified']
    assert PRIVATE not in json.dumps(value)


def test_changed_cli_binary_invalidates_cached_auth_without_running_it(tmp_path):
    service,runner,_,paths=fixture(tmp_path);asyncio.run(service.check('codex'));count=len(runner.calls)
    paths['codex'].write_bytes(b'replaced native fixture CLI')
    result=service.snapshot()['providers']['codex']
    assert result['status']=='not_checked' and result['authenticated'] is None and result['reason']=='cli_changed'
    assert len(runner.calls)==count


@pytest.mark.parametrize('path',[Path('relative.exe'),Path('C:/fake/cli.cmd'),Path('C:/fake/cli.ps1'),Path('C:/fake/cli.exe --prompt secret')])
def test_no_shell_wrapper_relative_path_or_command_text_can_be_executed(path):
    runner=Runner();service=ProducerDiagnostics(resolver=lambda _:(path,'environment'),runner=runner)
    result=asyncio.run(service.check('all'))
    assert runner.calls==[]
    assert all(not value['installed'] and value['reason']=='cli_configuration_invalid' for value in result['providers'].values())


@pytest.mark.parametrize('result,expected_authenticated,reason',[
    (CommandResult(1,stderr='Not logged in'),False,'not_authenticated'),
    (CommandResult(reason='timeout'),None,'auth_check_timeout'),
    (CommandResult(4,stderr=PRIVATE),None,'auth_check_failed'),
    (CommandResult(reason='output_limit'),None,'output_limit'),
])
def test_codex_auth_failures_are_safe_unknown_or_explicitly_signed_out(tmp_path,result,expected_authenticated,reason):
    service,runner,_,_=fixture(tmp_path);runner.overrides[('codex',('login','status'))]=result
    value=asyncio.run(service.check('codex'))['providers']['codex']
    assert value['authenticated'] is expected_authenticated and value['reason']==reason
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize('result,authenticated,reason',[
    (CommandResult(0,'{"loggedIn":false}'),False,'not_authenticated'),
    (CommandResult(0,'{"loggedIn":"true"}'),None,'auth_response_invalid'),
    (CommandResult(0,PRIVATE),None,'auth_response_invalid'),
    (CommandResult(1,'{"loggedIn":true}'),None,'auth_check_failed'),
])
def test_claude_uses_strict_logged_in_boolean_and_success_exit_code(tmp_path,result,authenticated,reason):
    service,runner,_,_=fixture(tmp_path);runner.overrides[('claude',('auth','status','--json'))]=result
    value=asyncio.run(service.check('claude'))['providers']['claude']
    assert value['authenticated'] is authenticated and value['reason']==reason
    assert PRIVATE not in json.dumps(value)


def test_unknown_claude_auth_subcommand_is_not_sent_as_a_model_prompt(tmp_path):
    service,runner,_,_=fixture(tmp_path)
    runner.overrides[('claude',('auth','status','--help'))]=CommandResult(0,'Usage: claude [options] [prompt] --json')
    value=asyncio.run(service.check('claude'))['providers']['claude']
    assert value['reason']=='auth_status_unsupported' and value['authenticated'] is None
    assert not any(call[1]==('auth','status','--json') for call in runner.calls)


def test_global_singleflight_prevents_overlapping_provider_processes(tmp_path):
    async def scenario():
        service,runner,_,_=fixture(tmp_path);started=asyncio.Event();release=asyncio.Event()
        async def delayed(argv,timeout):
            started.set();await release.wait();return await runner(argv,timeout)
        service.runner=delayed
        first=asyncio.create_task(service.check('codex'));await started.wait()
        second=await service.check('claude')
        assert second['providers']['codex']['checking'] is True and second['providers']['claude']['checked_at'] is None
        release.set();await first
        assert {call[0] for call in runner.calls}=={'codex'}
    asyncio.run(scenario())


def test_shutdown_cancels_only_its_inflight_check(tmp_path):
    async def scenario():
        service,_,_,_=fixture(tmp_path);started=asyncio.Event()
        async def delayed(*_):started.set();await asyncio.Event().wait()
        service.runner=delayed
        checking=asyncio.create_task(service.check('all'));await started.wait()
        await service.shutdown()
        with pytest.raises(asyncio.CancelledError):await checking
        assert not service.checking and service.inflight is None
    asyncio.run(scenario())


def test_owned_subprocess_output_limit_and_timeout_are_bounded_and_reaped(monkeypatch):
    class Stream:
        def __init__(self,data=b'',wait=False):self.data=data;self.wait=wait
        async def read(self,size):
            if self.wait:await asyncio.Event().wait()
            chunk,self.data=self.data[:size],self.data[size:];return chunk
    class Process:
        def __init__(self,large=False,wait=False):
            self.stdout=Stream(b'x'*(MAX_OUTPUT_BYTES+1) if large else b'',wait);self.stderr=Stream();self.returncode=None;self.killed=0
        def kill(self):self.killed+=1;self.returncode=-9
        async def wait(self):return self.returncode
    async def scenario():
        for large,wait,reason in ((True,False,'output_limit'),(False,True,'timeout')):
            process=Process(large,wait)
            async def launch(*args,**kwargs):
                assert 'shell' not in kwargs and args==('fixture.exe','--version')
                return process
            monkeypatch.setattr(module.asyncio,'create_subprocess_exec',launch)
            result=await module.run_command(['fixture.exe','--version'],1 if large else .01)
            assert result.reason==reason and process.killed==1 and result.stdout==result.stderr==''
    asyncio.run(scenario())

"""Connection failures must identify the layer without leaking captured config/errors."""
import json
import subprocess
from types import SimpleNamespace

import pytest

import smoke


def config(root, **overrides):
    value={'enabled':True,'transport':{'type':'stdio',
           'command':str(root.parent/'chrome-manager'/'.venv'/'Scripts'/'python.exe'),
           'args':['-I',str(root/'server.py'),'--port','8000'],
           'env':{'PRIVATE_TOKEN':'fixture-private'}}}
    value.update(overrides)
    return value


def cli_result(value=None, *, code=0, stderr=b'fixture-private-error'):
    return SimpleNamespace(returncode=code,stdout=json.dumps(value).encode(),stderr=stderr)


def test_configuration_checks_real_cli_registration_without_returning_secrets(tmp_path):
    calls=[]
    def runner(command, **kwargs):
        calls.append((command,kwargs))
        return cli_result(config(tmp_path,startup_timeout_sec=20,tool_timeout_sec=900))
    result=smoke.configuration_check(tmp_path,runner=runner,find=lambda name:'fixture-codex')
    assert result=={'status':'passed','registered':True,'enabled':True,'expected_target':True,
                    'startup_timeout_sec':20,'tool_timeout_sec':900}
    assert calls[0][0]==['fixture-codex','mcp','get','via_manager','--json']
    assert calls[0][1]['cwd']==tmp_path
    assert 'fixture-private' not in json.dumps(result)


@pytest.mark.parametrize('change', ['disabled','other-port','other-command','other-script','duplicate-port','unknown-argument'])
def test_configuration_distinguishes_disabled_or_wrong_target(tmp_path,change):
    value=config(tmp_path)
    if change=='disabled':value['enabled']=False
    if change=='other-port':value['transport']['args'][-1]='8010'
    if change=='other-command':value['transport']['command']='fixture-other-python'
    if change=='other-script':value['transport']['args'].insert(1,'fixture-other-script')
    if change=='duplicate-port':value['transport']['args'].extend(['--port','8010'])
    if change=='unknown-argument':value['transport']['args'].extend(['--unexpected','fixture-private'])
    result=smoke.configuration_check(tmp_path,runner=lambda *a,**k:cli_result(value),find=lambda name:'fixture-codex')
    assert result['status']=='failed'


def test_configuration_failure_is_unverified_without_echoing_cli_output(tmp_path):
    result=smoke.configuration_check(tmp_path,runner=lambda *a,**k:cli_result('fixture-private',code=1),find=lambda name:'fixture-codex')
    assert result=={'status':'unverified','code':'codex_mcp_get_failed'}
    assert smoke.configuration_check(tmp_path,find=lambda name:None)['code']=='codex_cli_not_found'


@pytest.mark.parametrize('stdout,stderr', [(b'not-json',b''),(b'[]\n',b''),(b'',b'fixture-private-error')])
def test_transport_failures_return_safe_layer_code(stdout,stderr):
    result,records=smoke.transport_check(SimpleNamespace(returncode=0,stdout=stdout,stderr=stderr))
    assert result['status']=='failed' and records is None
    assert 'fixture-private' not in json.dumps(result)


def test_backend_failure_does_not_claim_the_server_is_stopped():
    messages={4:{'result':{'structuredContent':{'ok':True,'result':{'managers':{
        scope:{'available':False,'code':'manager_unavailable','message':'fixture-private'} for scope in ('chrome','pages')}}}}}}
    for number in (5,6):messages[number]={'result':{'structuredContent':{'ok':False,'code':'manager_unavailable'}}}
    result=smoke.backend_check(messages)
    assert result['status']=='unavailable_from_this_process'
    assert result['page_count'] is None and result['profile_count'] is None
    assert 'fixture-private' not in json.dumps(result)


def test_backend_requires_both_managers_and_real_integer_counts():
    messages={4:{'result':{'structuredContent':{'ok':True,'result':{'managers':{
        scope:{'available':True} for scope in ('chrome','pages')}}}}}}
    for number in (5,6):messages[number]={'result':{'structuredContent':{'ok':True,'result':{'total':0}}}}
    assert smoke.backend_check(messages)['status']=='passed'
    messages[5]['result']['structuredContent']['result']['total']=False
    assert smoke.backend_check(messages)['status']!='passed'


def test_startup_timeout_reports_transport_and_leaves_backend_unchecked(tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(smoke,'ROOT',tmp_path)
    monkeypatch.setattr(smoke.sys,'argv',['smoke.py','--live'])
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('fixture-private-command',90)
    monkeypatch.setattr(smoke.subprocess,'run',timeout)
    assert smoke.main()==1
    result=json.loads(capsys.readouterr().out)
    assert result['checks']['transport']['code']=='mcp_process_start_or_timeout'
    assert result['checks']['backend']['status']=='not_run'
    assert 'fixture-private' not in json.dumps(result)
    assert json.loads((tmp_path/'.runtime'/'smoke-live.json').read_text())==result

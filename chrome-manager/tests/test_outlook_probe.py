"""Read-only probe lifecycle with temporary DB and immediate offline fakes."""
import asyncio
import json
from datetime import datetime,timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.page_recipient import PageRecipientStart
from app.database.session import Database
from app.models.entities import BmProfile,PageRecipientJob,Profile
from app.services import outlook_probe
from app.services.outlook_inbox import DIAGNOSTIC_FLAGS,OutlookInboxNeedsLogin,OutlookInboxWrongAccount


SINCE='2026-10-30T07:12:30+00:00'
SYNTHETIC_CODE='12345678'
EMAIL='probe.fixture@hotmail.com'


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in offline probe fixture')


@pytest.fixture
def manager(tmp_path,monkeypatch):
    db=Database(tmp_path/'probe.sqlite3')
    with db.session.begin() as session:
        session.add(Profile(id=3,name='Synthetic recipient',profile_path=str(tmp_path/'unused-profile')))
        session.flush()
        session.add(PageRecipientJob(profile_id=3,status='completed',step='completed',message='Unchanged fixture',
            configuration=PageRecipientStart(profile_id=3,outlook_email=EMAIL).model_dump_json(),
            checkpoint=json.dumps({'code_requested_at':SINCE,'accepted':True,'switched':True}),fields='{}'))
    calls=[]
    class Page:
        async def goto(self,url,**options):calls.append(('goto',url,options))
        async def evaluate(self,source):calls.append(('structure',source));return value.structure_data
    class Context:
        async def new_page(self):calls.append(('new_page',));return Page()
    class Browsers:
        recipient_profiles=set();extension_profiles=set();facebook_profiles=set();outlook_profiles=set()
        current_status='Stopped';launch_error=None;close_error=None
        def status(self,pid):assert pid==3;return self.current_status
        def validate_automation_start(self,pid,**options):calls.append(('validate',pid,options))
        async def ensure_open(self,pid,**options):
            assert pid in self.recipient_profiles
            calls.append(('open',pid,options))
            if self.launch_error:raise self.launch_error
            return Context()
        async def stop(self,pid):
            assert pid in self.recipient_profiles
            calls.append(('stop',pid))
            if self.close_error:raise self.close_error
    def row(pid):
        with db.session() as session:return session.scalar(select(PageRecipientJob).where(PageRecipientJob.profile_id==pid))
    value=SimpleNamespace(db=db,browsers=Browsers(),jobs={},tasks=SimpleNamespace(profile_jobs={}),row=row,calls=calls,
        config=SimpleNamespace(root=tmp_path),structure_data={'roles':{'main':1},'candidates':{'iframe':1},'iframes':[]})
    class Budget:
        async def __aenter__(self):return self
        async def __aexit__(self,*_):return False
    monkeypatch.setattr(outlook_probe.asyncio,'timeout',lambda seconds:Budget())
    monkeypatch.setattr(outlook_probe.time,'monotonic',lambda:100)
    yield value
    db.engine.dispose()


def fake_reader(monkeypatch,manager,error=None,flags=None):
    class Reader:
        diagnostics=flags or {key:True for key in DIAGNOSTIC_FLAGS}|{'raw_secret':'not-for-output'}
        def __init__(self,page,email,*,require_account):
            manager.calls.append(('reader',email,require_account))
        async def wait_for_code(self,since,**options):
            manager.calls.append(('read',since,options))
            if error:raise error
            return SYNTHETIC_CODE
    monkeypatch.setattr(outlook_probe,'OutlookInboxReader',Reader)


def job_snapshot(manager):
    row=manager.row(3)
    return {key:getattr(row,key) for key in ('status','step','message','configuration','checkpoint','fields','updated_at','code_deadline')}


def test_probe_returns_boolean_evidence_only_and_preserves_completed_checkpoint(manager,monkeypatch):
    fake_reader(monkeypatch,manager)
    before=job_snapshot(manager)
    result=immediate(outlook_probe.probe_outlook(manager,3))
    assert result['found'] is True and set(result)=={'found','message','diagnostics'}
    assert result['diagnostics']==dict.fromkeys(DIAGNOSTIC_FLAGS,True)
    output=json.dumps(result)
    assert all(secret not in output for secret in (SYNTHETIC_CODE,EMAIL,'https://','not-for-output'))
    assert ('open',3,{'for_automation':True,'for_recipient':True}) in manager.calls
    read=next(call for call in manager.calls if call[0]=='read')
    assert read[1]==datetime.fromisoformat(SINCE) and read[2]=={'timeout_seconds':30}
    assert manager.calls[-1]==('stop',3) and not manager.browsers.recipient_profiles
    assert job_snapshot(manager)==before


@pytest.mark.parametrize('status',['Running','Starting','Queued','Error'])
def test_probe_requires_a_stopped_profile(manager,status):
    manager.browsers.current_status=status
    with pytest.raises(ValueError):immediate(outlook_probe.probe_outlook(manager,3))
    assert manager.calls==[] and not manager.browsers.recipient_profiles


@pytest.mark.parametrize('busy',['recipient','extension','facebook','outlook','task','job'])
def test_probe_never_steals_an_active_profile_or_job(manager,busy):
    if busy=='job':manager.jobs[3]=object()
    elif busy=='task':manager.tasks.profile_jobs[3]=object()
    else:getattr(manager.browsers,busy+'_profiles').add(3)
    with pytest.raises(ValueError):immediate(outlook_probe.probe_outlook(manager,3))
    assert manager.calls==[]


def test_probe_rejects_bm_profiles(manager):
    with manager.db.session.begin() as session:session.add(BmProfile(profile_id=3))
    with pytest.raises(ValueError):immediate(outlook_probe.probe_outlook(manager,3))
    assert manager.calls==[]


@pytest.mark.parametrize('checkpoint',['{}','{"code_requested_at":"2026-10-30T07:12:30"}','{"code_requested_at":"invalid"}'])
def test_probe_requires_original_aware_request_timestamp(manager,checkpoint):
    with manager.db.session.begin() as session:session.scalar(select(PageRecipientJob)).checkpoint=checkpoint
    with pytest.raises(ValueError):immediate(outlook_probe.probe_outlook(manager,3))
    assert manager.calls==[]


@pytest.mark.parametrize('error',[TimeoutError('synthetic-secret'),OutlookInboxNeedsLogin('synthetic-secret'),OutlookInboxWrongAccount('synthetic-secret'),RuntimeError('synthetic-secret https://example.test/?token=private')])
def test_probe_errors_are_fixed_and_browser_and_reservation_are_released(manager,monkeypatch,error):
    flags={key:key in ('sender_verified','recipient_verified','body_present') for key in DIAGNOSTIC_FLAGS}
    fake_reader(monkeypatch,manager,error,flags)
    before=job_snapshot(manager)
    result=immediate(outlook_probe.probe_outlook(manager,3,timeout_seconds=12))
    assert result['found'] is False and result['diagnostics']==flags
    assert 'synthetic-secret' not in json.dumps(result) and 'https://' not in json.dumps(result)
    assert manager.calls[-1]==('stop',3) and not manager.browsers.recipient_profiles
    assert job_snapshot(manager)==before


def test_probe_launch_failure_also_cleans_its_queued_browser(manager,monkeypatch):
    fake_reader(monkeypatch,manager)
    manager.browsers.launch_error=RuntimeError('synthetic-secret')
    result=immediate(outlook_probe.probe_outlook(manager,3))
    assert result['found'] is False and result['diagnostics']==dict.fromkeys(DIAGNOSTIC_FLAGS,False)
    assert manager.calls[-1]==('stop',3) and not manager.browsers.recipient_profiles


def test_probe_cancellation_propagates_after_owned_browser_cleanup(manager,monkeypatch):
    fake_reader(monkeypatch,manager,asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):immediate(outlook_probe.probe_outlook(manager,3))
    assert manager.calls[-1]==('stop',3) and not manager.browsers.recipient_profiles


@pytest.mark.parametrize('budget,expected',[(1,1),(12,12),(100,30)])
def test_probe_caps_automatic_read_budget_to_thirty_seconds(manager,monkeypatch,budget,expected):
    fake_reader(monkeypatch,manager)
    immediate(outlook_probe.probe_outlook(manager,3,timeout_seconds=budget))
    assert next(call for call in manager.calls if call[0]=='read')[2]=={'timeout_seconds':expected}


def test_probe_close_failure_uses_fixed_message_and_releases_reservation(manager,monkeypatch):
    fake_reader(monkeypatch,manager);manager.browsers.close_error=RuntimeError('synthetic-secret')
    result=immediate(outlook_probe.probe_outlook(manager,3))
    assert result['found'] is True and 'Chrome chưa đóng xong' in result['message']
    assert 'synthetic-secret' not in json.dumps(result) and not manager.browsers.recipient_profiles


def test_failed_probe_saves_only_safe_structure_before_owned_browser_closes(manager,monkeypatch):
    fake_reader(monkeypatch,manager,TimeoutError())
    manager.structure_data={
        'roles':{'main':1,'option':4,'raw_mail_text':'synthetic-private-token'},
        'candidates':{'reading_pane_id':0,'iframe':1},
        'visible_id_shapes':[{'id_shape':'Message_12345678_synthetic-private-token_probe.fixture@hotmail.com','count':2}],
        'body_children':{'div':2,'mail-12345678-synthetic-private-token':1},
        'open_shadow_root_count':1,'shadow_roles':{'document':1},
        'iframes':[{'id_shape':'message-12345678-synthetic-private-token','origin_hostname':'outlook.live.com',
                    'readable':True,'roles':{'document':1},'body_children':{'div':1},'open_shadow_root_count':0,'shadow_roles':{},
                    'src':'https://outlook.live.com/message?code=12345678&token=synthetic-private-token',
                    'title':'synthetic-private-token','body':'12345678 probe.fixture@hotmail.com'}],
        'body':'12345678 probe.fixture@hotmail.com synthetic-private-token',
        'form_value':'synthetic-private-token','raw_url':'https://outlook.live.com/?token=synthetic-private-token',
    }
    before=job_snapshot(manager)
    result=immediate(outlook_probe.probe_outlook(manager,3))
    path=manager.config.root/'.runtime'/'outlook-inbox'/'structure_3.json'
    output=path.read_text(encoding='utf-8');data=json.loads(output)
    assert result['found'] is False and data['roles']['main']==1 and data['roles']['option']==4
    assert data['open_shadow_root_count']==1 and data['shadow_roles']['document']==1
    assert data['iframes'][0]['roles']['document']==1 and data['iframes'][0]['readable'] is True
    assert data['iframes'][0]['origin_hostname']=='outlook.live.com'
    assert all(secret not in output for secret in ('12345678',EMAIL,'synthetic-private-token','https://','?code=','raw_mail_text','form_value','raw_url'))
    assert next(index for index,call in enumerate(manager.calls) if call[0]=='structure')<next(index for index,call in enumerate(manager.calls) if call[0]=='stop')
    assert job_snapshot(manager)==before


def test_structure_script_is_hot_read_for_each_capture(manager,monkeypatch,tmp_path):
    source=tmp_path/'structure.js';source.write_text('() => ({version:1})',encoding='utf-8')
    monkeypatch.setattr(outlook_probe,'STRUCTURE_PATH',source)
    scripts=[]
    class Page:
        async def evaluate(self,script):scripts.append(script);return {'roles':{}}
    assert immediate(outlook_probe.capture_outlook_structure(manager,3,Page())) is True
    source.write_text('() => ({version:2})',encoding='utf-8')
    assert immediate(outlook_probe.capture_outlook_structure(manager,3,Page())) is True
    assert 'version:1' in scripts[0] and 'version:2' in scripts[1]


def test_structure_capture_failures_never_change_probe_result_or_leave_browser_reserved(manager,monkeypatch):
    fake_reader(monkeypatch,manager,TimeoutError())
    monkeypatch.setattr(outlook_probe,'STRUCTURE_PATH',manager.config.root/'missing.js')
    result=immediate(outlook_probe.probe_outlook(manager,3))
    assert result['found'] is False and not manager.browsers.recipient_profiles
    assert manager.calls[-1]==('stop',3)


def test_structure_schema_sanitizes_counts_and_hostname_without_keeping_query_data():
    safe=outlook_probe._structure_safe({'roles':{'main':12345678,'row':'12345678'},
        'iframes':[{'id_shape':'ReadingPaneContainerId-12345678','origin_hostname':'outlook.live.com/?token=synthetic-private-token'},
                   {'id_shape':'message','origin_hostname':'12345678.outlook.live.com'}]})
    assert safe['roles']['main']==10000 and safe['roles']['row']==0
    assert safe['iframes'][0]['origin_hostname']==''
    assert safe['iframes'][1]['origin_hostname']=='[redacted].outlook.live.com'
    assert all(secret not in json.dumps(safe) for secret in ('12345678','synthetic-private-token','https://'))

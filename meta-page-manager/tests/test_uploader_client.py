"""Offline only. The worker client never receives real cookies or publishes."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'uploader_client.py'
spec = importlib.util.spec_from_file_location('private_uploader_client', SCRIPT)
cli = importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)


@pytest.fixture
def run(tmp_path):
    runs = tmp_path / 'runs';runs.mkdir();folder = runs / str(uuid4());folder.mkdir()
    source = folder / 'source.png';payload = b'\x89PNG\r\n\x1a\nTEST DESIGN';source.write_bytes(payload)
    idea = str(uuid4());variants = [{'variant_id': str(uuid4()), 'reservation_id': str(uuid4()), 'ordinal': i} for i in range(7)]
    settings = {'enabled': True, 'page_ids': ['1001'], 'daily_time': '06:00', 'timezone': 'America/Los_Angeles'}
    sources = {'campaign_source': {'shop': cli.SHOP, 'campaign_url': 'https://blueox179.com/campaign/zitat25', 'verified': True},
               'black_campaign_source': {'shop': cli.SHOP, 'campaign_url': 'https://blueox179.com/campaign/ge2610', 'verified': True}}
    frozen = {'source_snapshot': sources, 'source_revision': 4, 'variants': variants,
              'scheduling_bindings_hash': hashlib.sha256(cli.canonical(settings).encode()).hexdigest(),
              'caption_mode': 'product_link_only', 'trigger_mode': 'manual_web_run_idea',
              'uploader': {'kind': 'local', 'endpoint': cli.LOCAL, 'shop': cli.SHOP}}
    digest = hashlib.sha256(payload).hexdigest()
    data = {'schema_version': '1.0', 'run_id': folder.name, 'idea_id': idea, 'artwork_sha256': digest,
            'source_path': str(source), 'original_filename': 'original-design.png', 'lease_fence': 2,
            **{k: frozen[k] for k in ('source_snapshot', 'source_revision', 'variants', 'caption_mode', 'trigger_mode', 'uploader')}}
    path = folder / 'run-input.json';path.write_text(cli.canonical(data), encoding='utf-8')
    dbpath = tmp_path / 'private.sqlite3'
    @contextmanager
    def connect():
        db = sqlite3.connect(dbpath);db.row_factory = sqlite3.Row
        try: yield db;db.commit()
        finally: db.close()
    store = SimpleNamespace(connect=connect)
    with connect() as db:
        db.executescript('''CREATE TABLE idea_manual_runs(id TEXT PRIMARY KEY,idea_id TEXT,artwork_sha256 TEXT,payload TEXT,payload_hash TEXT,status TEXT,cancel_requested INTEGER,fence INTEGER);
        CREATE TABLE idea_pipeline_ideas(id TEXT,artwork_sha256 TEXT);
        CREATE TABLE idea_pipeline_variants(id TEXT,idea_id TEXT,reservation_id TEXT,ordinal INTEGER);
        CREATE TABLE video_source_settings(id INTEGER,revision INTEGER,settings TEXT);
        CREATE TABLE idea_pipeline_settings(id INTEGER,settings TEXT);''')
        encoded = cli.canonical(frozen)
        db.execute('INSERT INTO idea_manual_runs VALUES(?,?,?,?,?,?,?,?)', (folder.name,idea,digest,encoded,hashlib.sha256(encoded.encode()).hexdigest(),'running',0,2))
        db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?)', (idea,digest))
        db.executemany('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?)', [(v['variant_id'],idea,v['reservation_id'],v['ordinal']) for v in variants])
        db.execute('INSERT INTO video_source_settings VALUES(1,4,?)', (cli.canonical(settings),))
        db.execute('INSERT INTO idea_pipeline_settings VALUES(1,?)', (cli.canonical(sources),))
    binding = cli.load_binding(path,store,runs_root=runs)
    overview = {'info': {'domain': cli.SHOP, 'authenticated': True, 'fallback_origin': 'zitat25'}, 'input': [], 'review': None, 'batches': [], 'job': {'running': False, 'type': '', 'log': ['secret raw log']}}
    return SimpleNamespace(binding=binding, data=data, path=path, source=source, payload=payload,
                           folder=folder, runs=runs, store=store, frozen=frozen, overview=overview)


def client_for(run, *, respond=None):
    requests = []
    def route(request):
        requests.append(request)
        assert request.headers['X-PODSach-Expected-Shop'] == cli.SHOP
        assert request.url.host == '127.0.0.1'
        if request.url.path == '/api/overview': return httpx.Response(200,json=run.overview)
        if request.url.path.startswith('/img/input/'): return httpx.Response(200,content=run.payload)
        if respond: return respond(request)
        if request.url.path == '/api/input/upload': return httpx.Response(200,json={'saved':['source.png'],'skipped':[]})
        return httpx.Response(200,json={'started':True})
    return httpx.Client(base_url=cli.LOCAL,transport=httpx.MockTransport(route),follow_redirects=False), requests


def test_valid_binding_and_read_only_sanitized_overview(run):
    run.overview['info'].update(session={'cookie':'TOPSECRET'},password='TOPSECRET',auth_file='C:\\private\\auth.json')
    with client_for(run)[0] as client:
        value = cli.perform_request(run.binding,client,method='GET',path='/api/overview')
    assert value['ok'] and value['result']['info']['authenticated']
    assert 'TOPSECRET' not in json.dumps(value) and 'raw log' not in json.dumps(value)
    assert not (run.folder / cli.STATE_NAME).exists()


@pytest.mark.parametrize('error_type',[sqlite3.OperationalError,sqlite3.DatabaseError])
def test_sqlite_authority_failure_is_distinct_from_frozen_binding_mismatch(run,error_type):
    @contextmanager
    def unavailable():
        raise error_type('PRIVATE database path / SQL / cookie must never be echoed')
        yield
    with pytest.raises(cli.ClientError) as caught:
        cli.load_binding(run.path,SimpleNamespace(connect=unavailable),runs_root=run.runs)
    assert caught.value.code=='run_authority_unavailable'
    assert str(caught.value)=='run_authority_unavailable'
    assert not (run.folder / cli.STATE_NAME).exists()


@pytest.mark.parametrize('path', ['https://evil.test/api/overview','//evil.test/api/job','/api/session/check','/api/shop/activate','/api/overview?x=1','/api/item/../../bad','/api/item/%61bc'])
def test_disallows_unknown_or_unsafe_routes(run,path):
    with client_for(run)[0] as client:
        with pytest.raises(cli.ClientError): cli.perform_request(run.binding,client,method='GET',path=path)


@pytest.mark.parametrize('change', ['hash','fence','cancel','terminal','uploader','variants','schedule','sources'])
def test_revalidates_canonical_bindings_before_any_mutation(run,change):
    with run.store.connect() as db:
        if change=='hash': db.execute('UPDATE idea_manual_runs SET payload_hash=?',('0'*64,))
        elif change=='fence': db.execute('UPDATE idea_manual_runs SET fence=3')
        elif change=='cancel': db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
        elif change=='terminal': db.execute("UPDATE idea_manual_runs SET status='needs_review'")
        elif change=='schedule': db.execute('UPDATE video_source_settings SET revision=5')
        elif change=='sources': db.execute('UPDATE idea_pipeline_settings SET settings=?',('{}',))
        elif change=='variants': db.execute('UPDATE idea_pipeline_variants SET reservation_id=? WHERE ordinal=0',(str(uuid4()),))
        elif change=='uploader':
            run.data['uploader']={'kind':'web','endpoint':cli.WEB,'shop':cli.SHOP};run.path.write_text(cli.canonical(run.data),encoding='utf8')
    client,requests=client_for(run)
    with client,pytest.raises(cli.ClientError): cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
    assert requests == []


def test_upload_is_exact_source_once_and_never_retries(run):
    client,requests=client_for(run)
    with client:
        result=cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
        assert result['result']['saved']==['source.png']
        again=cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
    assert again['replayed'] and sum(r.method=='POST' for r in requests)==1
    assert run.payload in next(r for r in requests if r.method=='POST').content


def test_foreign_file_and_altered_staged_source_rejected(run,tmp_path):
    foreign=tmp_path/'other.png';foreign.write_bytes(run.payload)
    client,requests=client_for(run)
    with client,pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=foreign)
    assert not requests
    run.source.write_bytes(b'changed')
    with pytest.raises(cli.ClientError): cli.load_binding(run.path,run.store,runs_root=run.runs)


@pytest.mark.parametrize('blocker', ['domain','auth','busy','review','pending_batch','foreign_input'])
def test_preflight_holds_foreign_or_busy_workspaces(run,blocker):
    if blocker=='domain':run.overview['info']['domain']='other.example'
    elif blocker=='auth':run.overview['info']['authenticated']=None
    elif blocker=='busy':run.overview['job']['running']=True
    elif blocker=='review':run.overview['review']={'batch':'other','items':[]}
    elif blocker=='pending_batch':run.overview['batches']=[{'name':'other','rows':[{'status':'pending'}]}]
    elif blocker=='foreign_input':run.overview['input']=[{'name':'foreign.png','size':4}];run.payload=b'NOT OUR SOURCE'
    client,requests=client_for(run)
    with client,pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
    assert not any(r.method=='POST' for r in requests)


def test_existing_input_requires_full_hash_and_does_not_upload_again(run):
    run.overview['input']=[{'name':'existing.png','size':len(run.payload)}]
    client,requests=client_for(run)
    with client:result=cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
    assert result['result']=={'already_present':True,'saved':['existing.png']}
    assert not any(r.method=='POST' for r in requests)


def test_timeout_latches_ambiguity_across_requests(run):
    def timeout(request):raise httpx.ReadTimeout('SECRET PROVIDER TEXT',request=request)
    client,requests=client_for(run,respond=timeout)
    with client:
        with pytest.raises(cli.ClientError) as failed:cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
        assert failed.value.code=='uploader_outcome_unknown' and failed.value.ambiguous
        count=len(requests)
        with pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='POST',path='/api/prep/start',body={'origin':'zitat25'})
        assert len(requests)==count
    assert cli.read_state(run.binding)['operations'][0]['outcome']=='uncertain'
    assert 'SECRET' not in (run.folder/cli.STATE_NAME).read_text()


def test_bounded_response_and_no_redirects(run):
    client,requests=client_for(run,respond=lambda _r:httpx.Response(302,headers={'Location':'https://evil.test/'}))
    with client,pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='GET',path='/api/job')
    assert len(requests)==1
    client,_=client_for(run,respond=lambda _r:httpx.Response(200,content=b'x'*(cli.MAX_RESPONSE+1)))
    with client,pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='GET',path='/api/job')


def test_log_output_contains_only_candidate_blueox_links(run):
    client,_=client_for(run,respond=lambda _r:httpx.Response(200,json={'log':'password=SECRET\nC:\\private\\auth.json\nhttps://blueox179.com/campaign/new123\nhttps://other.example/campaign/no'}))
    with client:result=cli.perform_request(run.binding,client,method='GET',path='/api/batch/batch123/log')
    assert result['result']=={'raw_log_omitted':True,'candidate_campaign_urls':['https://blueox179.com/campaign/new123']}


@pytest.mark.parametrize('body',[{}, {'origin':'foreign'}, {'origin':'zitat25','extra':'x'}])
def test_prep_uses_only_verified_frozen_template(run,body):
    client,requests=client_for(run)
    with client,pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='POST',path='/api/prep/start',body=body)
    assert not requests


def test_prep_review_export_and_upload_are_run_bound(run):
    run.overview['input']=[{'name':'source.png','size':len(run.payload)}]
    def respond(request):
        if request.url.path=='/api/review/export':return httpx.Response(200,json={'batch':'our-batch','count':1,'dir':'C:\\private'})
        return httpx.Response(200,json={'started':True})
    client,requests=client_for(run,respond=respond)
    with client:
        cli.perform_request(run.binding,client,method='POST',path='/api/prep/start',body={'origin':'zitat25'})
        run.overview['review']={'batch':'our-batch','items':[{'id':'abcdef123456','source':'source.png'}]}
        cli.perform_request(run.binding,client,method='POST',path='/api/item/abcdef123456',body={'quote':'Actual quote','slug':'our123','approved':True})
        cli.perform_request(run.binding,client,method='POST',path='/api/review/export',body={})
        run.overview.update(review=None,input=[],batches=[{'name':'our-batch','rows':[{'source_mockup':'source.png','status':'pending'}]}])
        cli.perform_request(run.binding,client,method='POST',path='/api/upload/start',body={'batch':'our-batch','origin':'zitat25','limit':1})
        before=len(requests)
        with pytest.raises(cli.ClientError):cli.perform_request(run.binding,client,method='POST',path='/api/upload/start',body={'batch':'our-batch','origin':'ge2610','limit':1})
        assert len(requests)==before
    assert sum(r.method=='POST' for r in requests)==4


def test_path_json_and_legacy_run_safety(run,tmp_path):
    with pytest.raises(cli.ClientError):cli.load_binding(Path('run-input.json'),run.store,runs_root=run.runs)
    run.data.pop('uploader');run.path.write_text(cli.canonical(run.data),encoding='utf8')
    with pytest.raises(cli.ClientError):cli.load_binding(run.path,run.store,runs_root=run.runs)
    with pytest.raises(cli.ClientError):cli.parse_json(b'{"x":1,"x":2}')
    with pytest.raises(cli.ClientError):cli.parse_json(b'{"x":NaN}')


def test_terminal_main_output_does_not_echo_raw_errors(monkeypatch,capsys):
    monkeypatch.setattr(cli,'ReadOnlyRunStore',lambda:object())
    monkeypatch.setattr(cli,'load_binding',lambda *_a,**_kw: (_ for _ in ()).throw(ValueError('TOKEN=SECRET')))
    assert cli.main(['--run-input','C:\\unused\\run-input.json','--method','GET','--path','/api/overview'])==2
    assert 'SECRET' not in capsys.readouterr().out


@pytest.mark.parametrize('method',['GET','POST'])
def test_main_sqlite_errors_have_safe_authority_code(monkeypatch,capsys,method):
    monkeypatch.setattr(cli,'ReadOnlyRunStore',lambda:object())
    def unavailable(*_args,**_kwargs):raise sqlite3.OperationalError('PRIVATE SQL/path/token')
    monkeypatch.setattr(cli,'load_binding',unavailable)
    assert cli.main(['--run-input','C:\\unused\\run-input.json','--method',method,'--path','/api/overview'])==2
    value=json.loads(capsys.readouterr().out)
    assert value=={'ok':False,'code':'run_authority_unavailable','needs_review':True,'outcome_unknown':method=='POST'}


def test_read_only_run_store_never_creates_schema_or_changes_authority_bytes(run,tmp_path,monkeypatch):
    with run.store.connect() as db:original=Path(db.execute('PRAGMA database_list').fetchone()[2])
    root=tmp_path/'fixed-manager';data=root/'data';data.mkdir(parents=True)
    path=data/'page-manager.sqlite3';path.write_bytes(original.read_bytes())
    monkeypatch.setattr(cli,'ROOT',root)
    store=cli.ReadOnlyRunStore();before=path.read_bytes()
    assert cli.load_binding(run.path,store,runs_root=run.runs)['data']['run_id']==run.folder.name
    with store.connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute('CREATE TABLE forbidden(id)')
        with pytest.raises(sqlite3.OperationalError):db.execute('UPDATE idea_manual_runs SET fence=99')
    assert path.read_bytes()==before and not Path(str(path)+'-wal').exists()
    assert not Path(str(path)+'-journal').exists()


def test_read_only_run_store_does_not_create_a_missing_database(tmp_path,monkeypatch):
    root=tmp_path/'missing-manager';monkeypatch.setattr(cli,'ROOT',root)
    with pytest.raises(cli.ClientError):cli.ReadOnlyRunStore()
    assert not root.exists()


def test_default_run_lookup_accepts_exact_legacy_folder_but_rejects_duplicate_uuid(run,tmp_path,monkeypatch):
    primary=tmp_path/'new/runs'
    monkeypatch.setattr(cli,'RUNS_ROOT',primary);monkeypatch.setattr(cli,'LEGACY_RUNS_ROOT',run.runs)
    assert cli.load_binding(run.path,run.store)['runs_root']==run.runs
    (primary/run.folder.name).mkdir(parents=True)
    with pytest.raises(cli.ClientError) as caught:cli.load_binding(run.path,run.store)
    assert caught.value.code=='invalid_run_location'


def test_read_only_web_session_initialization_has_no_ddl_and_fails_if_host_schema_missing(run,tmp_path,monkeypatch):
    from page_manager.uploader_connection import UploaderConnection
    from page_manager.errors import AppError
    with run.store.connect() as db:original=Path(db.execute('PRAGMA database_list').fetchone()[2])
    root=tmp_path/'fixed-manager';(root/'data').mkdir(parents=True)
    path=root/'data/page-manager.sqlite3';path.write_bytes(original.read_bytes());monkeypatch.setattr(cli,'ROOT',root)
    store=cli.ReadOnlyRunStore();before=path.read_bytes()
    with pytest.raises(AppError) as caught:UploaderConnection(store,object(),initialize=False)
    assert caught.value.code=='uploader_session_unavailable' and path.read_bytes()==before
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE uploader_remote_session(id INTEGER PRIMARY KEY,encrypted_cookies TEXT,
            revision INTEGER,updated_at TEXT,checked_revision INTEGER,checked_at TEXT,checked_ok INTEGER)''')
        db.execute('INSERT INTO uploader_remote_session(id,revision) VALUES(1,1)')
    before=path.read_bytes()
    connection=UploaderConnection(store,object(),initialize=False)
    assert connection.session_revision()==1 and connection.shop_verified() is None
    assert path.read_bytes()==before


def test_read_only_web_client_keeps_response_cookie_rotation_only_in_memory(tmp_path,monkeypatch):
    from test_uploader_connection import build,Box,PASSWORD,COOKIE,OVERVIEW_PATH,valid_info
    from page_manager.uploader_connection import UploaderConnection
    root=tmp_path/'fixed-manager'
    host,store,_=build(root);host.connect('fixture-user',PASSWORD);host.record_shop_check(True)
    monkeypatch.setattr(cli,'ROOT',root)
    reader=cli.ReadOnlyRunStore();before=reader.path.read_bytes()
    with store.connect() as db:session=dict(db.execute('SELECT * FROM uploader_remote_session').fetchone())
    calls=[]
    def respond(request):
        calls.append(request)
        assert COOKIE in request.headers.get('cookie','')
        return httpx.Response(200,json=valid_info(),headers={'set-cookie':'pod_session=ROTATED_FIXTURE_COOKIE; Path=/; Secure; HttpOnly'})
    web=UploaderConnection(reader,Box(),initialize=False,transport=httpx.MockTransport(respond))
    assert web.shop_verified() is True
    with web.client() as client:assert client.get('/api/overview').status_code==200
    with reader.connect() as db:assert dict(db.execute('SELECT * FROM uploader_remote_session').fetchone())==session
    assert reader.path.read_bytes()==before and len(calls)==1


def test_argument_errors_never_echo_credentials(capsys):
    with pytest.raises(SystemExit) as failed: cli.main(['--password','SECRET'])
    assert failed.value.code==2
    output=capsys.readouterr()
    assert 'SECRET' not in output.out+output.err
    assert 'invalid_client_arguments' in output.out


def test_cancel_during_remote_inspection_stops_post(run):
    seen=[]
    def respond(request):
        seen.append(request.method)
        with run.store.connect() as db: db.execute('UPDATE idea_manual_runs SET cancel_requested=1')
        return httpx.Response(200,json=run.overview)
    with httpx.Client(base_url=cli.LOCAL,transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(cli.ClientError): cli.perform_request(run.binding,client,method='POST',path='/api/input/upload',file=run.source)
    assert seen==['GET']
    assert not (run.folder/cli.STATE_NAME).exists()


def bind_web(run):
    run.frozen['uploader'] = {'kind': 'web', 'endpoint': cli.WEB, 'shop': cli.SHOP}
    run.data['uploader'] = run.frozen['uploader']
    run.path.write_text(cli.canonical(run.data), encoding='utf8')
    encoded = cli.canonical(run.frozen)
    with run.store.connect() as db:
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',
                   (encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    run.binding = cli.load_binding(run.path, run.store, runs_root=run.runs)


@pytest.mark.parametrize('proof', [None, False, True])
def test_modern_web_requires_fresh_explicit_shop_attestation(run, proof):
    bind_web(run)
    run.overview['info'].pop('authenticated')
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.host == 'ul.eagleglobal2023.com'
        assert request.url.path.startswith('/s/blueox179.com/api/')
        if request.method == 'GET':
            return httpx.Response(200, json=run.overview)
        return httpx.Response(200, json={'saved': ['source.png'], 'skipped': []})
    with httpx.Client(base_url=cli.WEB+'/', transport=httpx.MockTransport(respond)) as client:
        if proof is True:
            result = cli.perform_request(run.binding, client, method='POST', path='/api/input/upload',
                                         file=run.source, shop_verifier=lambda: proof)
            assert result['ok']
        else:
            with pytest.raises(cli.ClientError, match='uploader_not_ready'):
                cli.perform_request(run.binding, client, method='POST', path='/api/input/upload',
                                    file=run.source, shop_verifier=lambda: proof)
    assert sum(r.method == 'POST' for r in requests) == (1 if proof is True else 0)


def test_attestation_never_overrides_explicit_expired_shop_session(run):
    bind_web(run)
    run.overview['info']['authenticated'] = False
    client, requests = client_for(run)
    with client, pytest.raises(cli.ClientError, match='uploader_not_ready'):
        cli.perform_request(run.binding, client, method='POST', path='/api/input/upload',
                            file=run.source, shop_verifier=lambda: True)
    assert not any(r.method == 'POST' for r in requests)


def test_web_read_uses_attestation_without_exposing_session_data(run):
    bind_web(run)
    run.overview['info'].pop('authenticated')
    run.overview['info']['session'] = {'cookies': 'PRIVATE_COOKIE_VALUE'}
    with client_for(run)[0] as client:
        result = cli.perform_request(run.binding, client, method='GET', path='/api/overview',
                                     shop_verifier=lambda: True)
    assert result['result']['info']['authenticated'] is True
    assert 'PRIVATE_COOKIE' not in json.dumps(result)


def dynamic_binding(run,count=2,trigger='automatic_idea_batch'):
    variants=list(run.frozen['variants'])
    while len(variants)<count:
        variant={'variant_id':str(uuid4()),'reservation_id':str(uuid4()),'ordinal':len(variants)}
        variants.append(variant)
        with run.store.connect() as db:
            db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?)',
                       (variant['variant_id'],run.data['idea_id'],variant['reservation_id'],variant['ordinal']))
    run.frozen.update(variant_count=count,variants=variants[:count],trigger_mode=trigger,
        workflow={'version':'page-shirt-zoom-8s-v2','sha256':'a'*64})
    if trigger=='automatic_idea_batch':
        run.frozen['automatic_batch']={'batch_id':str(uuid4()),'settings_revision':1,'day_key':'2030-01-01'}
    for key in ('variant_count','variants','trigger_mode','workflow','automatic_batch'):
        if key in run.frozen:run.data[key]=run.frozen[key]
    persist_dynamic_binding(run)


def persist_dynamic_binding(run):
    run.path.write_text(cli.canonical(run.data),encoding='utf8')
    encoded=cli.canonical(run.frozen)
    with run.store.connect() as db:
        db.execute('UPDATE idea_manual_runs SET payload=?,payload_hash=?',
                   (encoded,hashlib.sha256(encoded.encode()).hexdigest()))


@pytest.mark.parametrize('count',[1,2,5,14])
def test_dynamic_frozen_prefix_allows_both_authorized_triggers_without_rewriting_extras(run,count):
    dynamic_binding(run,count)
    binding=cli.load_binding(run.path,run.store,runs_root=run.runs)
    assert len(binding['data']['variants'])==count and binding['data']['variant_count']==count
    with run.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0]==max(count,7)
    run.frozen['trigger_mode']=run.data['trigger_mode']='manual_web_run_idea'
    run.frozen.pop('automatic_batch');run.data.pop('automatic_batch');persist_dynamic_binding(run)
    assert cli.load_binding(run.path,run.store,runs_root=run.runs)['data']['trigger_mode']=='manual_web_run_idea'


@pytest.mark.parametrize('field',['variant_count','variants','trigger_mode','automatic_batch','workflow'])
def test_dynamic_worker_cannot_change_frozen_count_trigger_context_or_workflow(run,field):
    dynamic_binding(run)
    if field=='variant_count':run.data[field]=1
    elif field=='variants':run.data[field]=list(reversed(run.data[field]))
    elif field=='trigger_mode':run.data[field]='manual_web_run_idea'
    else:run.data[field]={**run.data[field],'tampered':True}
    run.path.write_text(cli.canonical(run.data),encoding='utf8')
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)


@pytest.mark.parametrize('count',[0,15,True,False,'2',2.0,None])
def test_dynamic_uploader_count_is_strict_and_bounded(run,count):
    dynamic_binding(run)
    run.data['variant_count']=count
    run.path.write_text(cli.canonical(run.data),encoding='utf8')
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)


def test_legacy_payload_can_receive_explicit_seven_in_recreated_run_input(run):
    assert 'variant_count' not in run.frozen
    run.data['variant_count']=7
    run.path.write_text(cli.canonical(run.data),encoding='utf8')
    assert cli.load_binding(run.path,run.store,runs_root=run.runs)['data']['variant_count']==7


@pytest.mark.parametrize('field',['audio_mix_policy','approved_audio_mixes'])
def test_worker_cannot_change_or_drop_frozen_human_audio_approval_snapshot(run,field):
    dynamic_binding(run)
    run.frozen['audio_mix_policy']=run.data['audio_mix_policy']='human_approved_exact_pcm_v1'
    snapshots={'a'*64:{'approval_id':'b'*64,'pcm_sha256':'c'*64}}
    run.frozen['approved_audio_mixes']=run.data['approved_audio_mixes']=snapshots
    persist_dynamic_binding(run)
    assert cli.load_binding(run.path,run.store,runs_root=run.runs)['data']['approved_audio_mixes']==snapshots
    run.data.pop(field)
    run.path.write_text(cli.canonical(run.data),encoding='utf8')
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)


def authorize_iteration(run):
    """Synthetic second production of the same idea; old slots are retained."""
    iteration={'id':str(uuid4()),'number':2};request_id=str(uuid4())
    variants=[{'variant_id':str(uuid4()),'reservation_id':str(uuid4()),'ordinal':n} for n in (1,2)]
    for target in (run.data,run.frozen):
        target.update(production_iteration=iteration,variant_count=2,variants=variants)
    with run.store.connect() as db:
        db.executescript('''ALTER TABLE idea_manual_runs ADD COLUMN request_id TEXT;
            CREATE TABLE idea_production_iterations(id TEXT PRIMARY KEY,idea_id TEXT,request_id TEXT UNIQUE,
                artwork_sha256 TEXT,number INTEGER,variant_count INTEGER,created_at TEXT);
            CREATE TABLE idea_production_iteration_variants(iteration_id TEXT,ordinal INTEGER,variant_id TEXT UNIQUE,
                PRIMARY KEY(iteration_id,ordinal));''')
        db.execute('UPDATE idea_manual_runs SET request_id=?',(request_id,))
        db.execute('INSERT INTO idea_production_iterations VALUES(?,?,?,?,?,?,?)',
            (iteration['id'],run.data['idea_id'],request_id,run.data['artwork_sha256'],2,2,'2030-01-01'))
        for value in variants:
            db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?)',
                (value['variant_id'],run.data['idea_id'],value['reservation_id'],20+value['ordinal']))
            db.execute('INSERT INTO idea_production_iteration_variants VALUES(?,?,?)',
                (iteration['id'],value['ordinal'],value['variant_id']))
    persist_dynamic_binding(run)
    return iteration


def test_second_iteration_authorizes_fresh_local_slots_without_reusing_legacy_prefix(run):
    original=list(run.data['variants']);authorize_iteration(run)
    checked=cli.load_binding(run.path,run.store,runs_root=run.runs)
    assert checked['data']['variants']==run.data['variants']
    assert not {x['variant_id'] for x in original}&{x['variant_id'] for x in run.data['variants']}
    with run.store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM idea_pipeline_variants').fetchone()[0]==9


@pytest.mark.parametrize('field',['id','number','idea_id','request_id','artwork_sha256','variant_count',
                                 'missing_mapping','extra_mapping','mapping_order','foreign_variant','reservation'])
def test_iteration_authority_tamper_is_rejected_before_uploader_mutation(run,field):
    iteration=authorize_iteration(run)
    with run.store.connect() as db:
        if field in ('id','number','idea_id','request_id','artwork_sha256','variant_count'):
            value=3 if field=='number' else 1 if field=='variant_count' else 'a'*64 if field=='artwork_sha256' else str(uuid4())
            db.execute('UPDATE idea_production_iterations SET '+field+'=?',(value,))
        elif field=='missing_mapping':db.execute('DELETE FROM idea_production_iteration_variants WHERE ordinal=2')
        elif field=='extra_mapping':db.execute('INSERT INTO idea_production_iteration_variants VALUES(?,?,?)',(iteration['id'],3,str(uuid4())))
        elif field=='mapping_order':db.execute('UPDATE idea_production_iteration_variants SET ordinal=4 WHERE ordinal=2')
        elif field=='foreign_variant':db.execute('UPDATE idea_pipeline_variants SET idea_id=? WHERE id=?',(str(uuid4()),run.data['variants'][0]['variant_id']))
        else:db.execute('UPDATE idea_pipeline_variants SET reservation_id=? WHERE id=?',(str(uuid4()),run.data['variants'][0]['variant_id']))
    client,requests=client_for(run)
    with client,pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.perform_request({**run.binding,'data':run.data},client,method='POST',path='/api/prep/start',body={'origin':'zitat25'})
    assert requests==[] and not (run.folder/cli.STATE_NAME).exists()


@pytest.mark.parametrize('value',[None,{},True,{'id':str(uuid4()),'number':True},
    {'id':str(uuid4()),'number':1},{'id':str(uuid4()),'number':2**63},
    {'id':str(uuid4()),'number':2,'extra':'private'}])
def test_iteration_metadata_is_closed_and_requires_a_frozen_explicit_authorization(run,value):
    authorize_iteration(run)
    run.data['production_iteration']=run.frozen['production_iteration']=value
    persist_dynamic_binding(run)
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)


def test_worker_cannot_drop_iteration_metadata_to_reuse_first_production(run):
    authorize_iteration(run)
    run.data.pop('production_iteration');run.path.write_text(cli.canonical(run.data),encoding='utf8')
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)


@pytest.mark.parametrize('ordinal',[True,1.0,'1'])
def test_iteration_local_ordinals_use_exact_integers(run,ordinal):
    authorize_iteration(run)
    run.data['variants'][0]['ordinal']=ordinal;persist_dynamic_binding(run)
    with pytest.raises(cli.ClientError,match='invalid_run_binding'):
        cli.load_binding(run.path,run.store,runs_root=run.runs)

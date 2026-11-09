"""Chrome catalog protocol tests: fake HTTP client and own temp journal only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from via_mcp.chrome_tools import tools
from via_mcp.common import MutationInput,ToolError
from via_mcp.privacy import protect
from via_mcp.registry import Registry
from via_mcp.journal import Journal

ASSET='a'*32
PAGE_URL='https://www.facebook.com/profile.php?id=555'
PRIVATE='FixturePrivateValue82'
CATALOG={tool.name:tool for tool in tools()}


def profile(pid=1,bm=False):
    return {'id':pid,'name':'Fixture '+str(pid),'profile_path':'C:/private/'+PRIVATE,'browser_status':'Stopped','proxy_id':21,
            'proxy':{'id':21,'host':PRIVATE,'port':1234,'ip_address':'192.0.2.1','username':PRIVATE,'password':PRIVATE,'proxy_url':'http://'+PRIVATE+':secret@proxy.test','status':'Working','has_credentials':True},
            'outlook':{'email':'fixture-private@example.test','password':PRIVATE,'status':'success','message':PRIVATE,'active':False},
            'facebook':{'name':'Fixture FB','url':PAGE_URL,'status':'completed','pages':[{'id':9,'name':'Fixture Page','url':PAGE_URL,'posts_today':1,'posts':[{'body':PRIVATE}]}]},
            'bm':{'portfolios':[{'bm_id':'123','name':'Fixture BM','secret':PRIVATE}],'note':PRIVATE,'mobile_enabled':True} if bm else None}


class Client:
    def __init__(self):
        self.calls=[];self.profiles=[profile(),profile(2,True)]
        self.assets=[{'id':ASSET,'name':'fixture.mp4','size':128,'mime':'video/mp4','path':'C:/private/'+PRIVATE,'sha256':PRIVATE}]
        self.pages=[{'page_url':'https://m.facebook.com/555?ref=fixture','page_name':'Fixture Page','source':'facebook_scan'}]
        self.reels=[{'id':7,'profile_id':1,'page_url':PAGE_URL,'page_name':'Fixture Page','status':'needs_review','asset_id':ASSET,'can_cancel':True,'can_resume':True,'can_verify_link':True,'can_archive':True,'can_restore':True,'publish_attempted':True,'path':PRIVATE}]
        self.recipient=[{'id':8,'profile_id':1,'status':'needs_user','active':False,'outlook_email':'fixture-private@example.test','configuration':PRIVATE,
                         'fields':{'avatar':{'status':'completed','path':PRIVATE,'value':PRIVATE}},'candidates':[{'name':'Fixture Page','url':PAGE_URL,'secret':PRIVATE}]}]
        self.uncertain_create=False
    def request(self,workspace,method,path,**kwargs):
        assert workspace=='chrome'
        self.calls.append((workspace,method,path,deepcopy(kwargs)))
        if method=='GET':
            params=kwargs.get('params',{})
            if path in ('/api/v1/profiles','/api/v1/profile-ids'):
                scope=params['scope']
                rows=[row for row in self.profiles if scope=='all' or (row['bm'] is not None)==(scope=='bm')]
                if path.endswith('/profile-ids'):
                    ids={int(value) for value in params['ids'].split(',')}
                    assert len(ids)<=100
                    return {'ids':[row['id'] for row in rows if row['id'] in ids]}
            elif path=='/api/v1/proxies':
                rows=[self.profiles[0]['proxy']]
                if 'ids' in params:
                    ids={int(value) for value in params['ids'].split(',')}
                    assert len(ids)<=100
                    rows=[row for row in rows if row['id'] in ids]
            elif path=='/api/v1/reel-assets':rows=self.assets
            elif path=='/api/v1/reels':rows=[row for row in self.reels if params['include_archived'] or not row.get('archived_at')]
            elif path=='/api/v1/recipient-jobs':rows=[row for row in self.recipient if 'profile_id' not in params or row['profile_id']==params['profile_id']]
            else:rows=None
            if rows is not None:
                limit,offset=params['limit'],params['offset']
                assert 1<=limit<=100
                return {'items':deepcopy(rows[offset:offset+limit]),'total':len(rows),'offset':offset,'limit':limit}

            for prefix,items in (('/api/v1/profiles/',self.profiles),('/api/v1/reel-assets/',self.assets),('/api/v1/reels/',self.reels)):
                if path.startswith(prefix):
                    match=next((row for row in items if str(row['id'])==path.removeprefix(prefix)),None)
                    if match is None:raise ToolError('Fixture item not found.','not_found')
                    result=deepcopy(match)
                    if prefix=='/api/v1/profiles/' and result.get('facebook'):
                        result['facebook']['pages']=result['facebook']['pages'][:params.get('preview',0)]
                    return result
            values={'/api/reels/pages':self.pages,
                    '/api/page-recipient/config':{'asset_root':'C:/private/'+PRIVATE,'token':PRIVATE},
                    '/api/reels/config':{'max_upload_bytes':1234,'accepted_extensions':['.mp4','.m4v','.mov','.exe'],'default_timezone':'UTC','path':PRIVATE},
                    '/api/facebook/refresh-status':{'enabled':True,'interval_hours':24,'profiles':[{'profile_id':1,'result':'success','path':PRIVATE}],'secret':PRIVATE}}
            return deepcopy(values[path])
        if path=='/api/reels':
            if self.uncertain_create:raise ToolError('Fixture uncertain transport','transport_timeout',uncertain=True)
            return {'id':10,'profile_id':kwargs['body']['profile_id'],'page_url':kwargs['body']['page_url'],'status':'scheduled','path':PRIVATE}
        if path in ('/api/page-recipient/start','/api/page-recipient/1/resume'):return {'accepted':[1],'password':PRIVATE}
        if path=='/api/proxies/check':return {'accepted':[21]}
        if path=='/api/facebook/check':return {'accepted':[1],'errors':[{'profile_id':3,'message':'Busy','password':PRIVATE}]}
        return {'ok':True,'password':PRIVATE}
    def upload(self,workspace,file_id,**kwargs):
        self.calls.append((workspace,'UPLOAD',file_id,kwargs));return deepcopy(self.assets[0])


@pytest.fixture
def context():return SimpleNamespace(client=Client(),protect=protect)


def invoke(ctx,name,**values):
    tool=CATALOG[name]
    return tool.handler(ctx,tool.model.model_validate(values))


def mutation(**values):return {'operation_id':str(uuid4()),**values}
def schedule(**extra):
    return mutation(profile_id=1,page_url='https://m.facebook.com/555',asset_id=ASSET,caption='Fixture caption',scheduled_local='2030-11-01T10:30',timezone='America/Los_Angeles',**extra)


def posting_proof(**extra):
    return {'can_publish':True,'information_status':'updated','posting_page_id':'555',
            'posting_revision':3,'posting_block_reason':None,**extra}


def test_reel_list_preserves_safe_policy_metadata_and_keeps_private_proof_out(context):
    context.client.reels[0].update(**posting_proof(),receipt_id=PRIVATE,field_fingerprints={'bio':PRIVATE},raw_error=PRIVATE)
    result=invoke(context,'chrome_list_reels')['items'][0]
    assert all(result[key]==value for key,value in posting_proof().items())
    assert result['can_verify_link'] is True and result['publish_attempted'] is True
    assert PRIVATE not in json.dumps(result) and 'field_fingerprints' not in result and 'receipt_id' not in result
    assert context.client.calls==[('chrome','GET','/api/v1/reels',{'read_only':True,
        'params':{'include_archived':False,'sort':'scheduled_desc','limit':50,'offset':0}})]


@pytest.mark.parametrize('bad',[{},posting_proof(can_publish='true'),posting_proof(posting_revision=0),
    posting_proof(posting_page_id='0'),posting_proof(information_status='not_updated'),
    posting_proof(posting_block_reason=PRIVATE)])
def test_reel_projection_never_promotes_legacy_or_malformed_policy(context,bad):
    context.client.reels[0].update(**bad)
    result=invoke(context,'chrome_list_reels')['items'][0]
    assert result['can_publish'] is False and result['posting_block_reason'] is not None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('url',['https://www.facebook.com/555','https://www.facebook.com/profile.php?id=555'])
def test_numeric_create_uses_approved_cached_page_identity_when_cache_has_only_vanity(context,url):
    context.client.pages=[{'page_url':'https://www.facebook.com/fixture-alias','page_name':'Fixture Page',**posting_proof()}]
    args=schedule();args['page_url']=url
    result=invoke(context,'chrome_create_reel',**args)
    assert result['job']['id']==10
    assert context.client.calls[-1][1:3]==('POST','/api/reels')
    assert context.client.calls[-1][3]['body']['page_url']==PAGE_URL


@pytest.mark.parametrize('proof',[{},posting_proof(can_publish=False,posting_block_reason='page_information_required'),
    posting_proof(posting_page_id='999'),posting_proof(posting_revision=0),
    posting_proof(information_status='not_updated')])
def test_numeric_create_does_not_infer_identity_from_names_or_unapproved_cache(context,proof):
    context.client.pages=[{'page_url':'https://www.facebook.com/fixture-alias','page_name':'555',**proof}]
    with pytest.raises(ToolError) as error:invoke(context,'chrome_create_reel',**schedule())
    assert error.value.code=='invalid_source'
    assert not any(call[1]=='POST' for call in context.client.calls)


def test_other_alias_does_not_match_even_when_name_and_numeric_proof_match(context):
    context.client.pages=[{'page_url':'https://www.facebook.com/fixture-alias','page_name':'Fixture Page',**posting_proof()}]
    args=schedule();args['page_url']='https://www.facebook.com/another-alias'
    with pytest.raises(ToolError):invoke(context,'chrome_create_reel',**args)
    assert not any(call[1]=='POST' for call in context.client.calls)


@pytest.mark.parametrize('url',[PAGE_URL,'https://www.facebook.com/fixture-alias'])
def test_conflicting_cached_alias_identities_do_not_create_a_schedule(context,url):
    context.client.pages=[{'page_url':'https://www.facebook.com/fixture-alias',**posting_proof()},
                          {'page_url':'https://www.facebook.com/fixture-alias',**posting_proof(posting_page_id='999')}]
    args=schedule();args['page_url']=url
    with pytest.raises(ToolError):invoke(context,'chrome_create_reel',**args)
    assert not any(call[1]=='POST' for call in context.client.calls)


def test_exact_numeric_url_with_contradictory_identity_proof_is_rejected(context):
    context.client.pages=[{'page_url':PAGE_URL,**posting_proof(posting_page_id='999')}]
    with pytest.raises(ToolError):invoke(context,'chrome_create_reel',**schedule())
    assert not any(call[1]=='POST' for call in context.client.calls)


def test_multiple_cache_aliases_for_one_approved_identity_are_unambiguous(context):
    context.client.pages=[{'page_url':'https://www.facebook.com/fixture-alias',**posting_proof()},
                          {'page_url':'https://www.facebook.com/second-fixture-alias',**posting_proof()}]
    assert invoke(context,'chrome_create_reel',**schedule())['job']['id']==10


def test_names_are_unique_chrome_scoped_and_mutations_have_operation_ids():
    assert len(CATALOG)==len(tools()) and all(name.startswith('chrome_') for name in CATALOG)
    assert not any(any(word in name for word in ('password','code','import','javascript','shell','task_run')) for name in CATALOG)
    for tool in CATALOG.values():
        schema=tool.descriptor()
        assert schema['inputSchema']['additionalProperties'] is False
        assert schema['annotations']['readOnlyHint'] is tool.read_only
        if not tool.read_only:
            assert issubclass(tool.model,MutationInput)
            assert 'operation_id' in schema['inputSchema']['required']
        else:assert 'operation_id' not in schema['inputSchema']['properties']


@pytest.mark.parametrize('name,arguments',[
    ('chrome_get_profile',{'profile_id':True}),('chrome_get_profile',{'profile_id':'1'}),('chrome_get_profile',{'profile_id':0}),
    ('chrome_list_profiles',{'limit':201}),('chrome_list_profiles',{'offset':-1}),('chrome_list_profiles',{'include_bm':'true'}),
    ('chrome_start_profile',{'profile_id':1}),('chrome_open_outlook',mutation(profile_id=1,password=PRIVATE)),
    ('chrome_open_outlook',mutation(profile_id=1,email='fixture@example.test')),
    ('chrome_check_proxies',mutation(proxy_ids=[])),('chrome_check_proxies',mutation(proxy_ids=[True])),
    ('chrome_reel_upload',mutation(file_id='C:/private/file.mp4')),
])
def test_inputs_are_strict_bounded_and_reject_unknown_secrets(name,arguments):
    with pytest.raises(ValidationError):CATALOG[name].model.model_validate(arguments)


def test_profile_and_bm_projection_never_exposes_paths_proxy_endpoints_or_outlook_mail(context):
    value=invoke(context,'chrome_list_profiles');assert value['total']==1 and value['limit']==50
    selected=invoke(context,'chrome_get_profile',profile_id=1)['profile']
    assert selected['facebook']['pages'][0]['posts_today']==1 and selected['outlook']=={'status':'success','active':False}
    assert selected['proxy']=={'id':21,'status':'Working','has_credentials':True}
    bm=invoke(context,'chrome_get_bm_profile',profile_id=2)['profile']
    assert bm['is_bm'] is True and bm['bm']['portfolios']==[{'bm_id':'123','name':'Fixture BM'}]
    serialized=json.dumps([value,selected,bm])
    assert PRIVATE not in serialized and 'fixture-private@example.test' not in serialized and '192.0.2.1' not in serialized
    assert invoke(context,'chrome_list_profiles',include_bm=True)['total']==2
    assert invoke(context,'chrome_list_bm_profiles')['total']==1


def test_profile_list_pagination_is_bounded_without_backend_unknown_params(context):
    context.client.profiles=[profile(i,bm=(i%2==0)) for i in range(1,601)]
    value=invoke(context,'chrome_list_profiles',offset=50,limit=200)
    assert value['total']==300 and len(value['items'])==200 and value['items'][0]['id']==101
    assert [row['id'] for row in value['items']]==list(range(101,500,2))
    assert context.client.calls==[('chrome','GET','/api/v1/profiles',{'read_only':True,
        'params':{'scope':'regular','limit':100,'offset':offset}}) for offset in (50,150)]
    context.client.calls.clear()
    assert invoke(context,'chrome_list_profiles',offset=400)=={'items':[],'total':300,'offset':400,'limit':50}
    assert len(context.client.calls)==1


def test_exact_profile_preflight_rejects_wrong_id_or_missing_bm_marker(context,monkeypatch):
    for invalid in ({'id':2,'bm':None},{'id':1},{'id':1,'bm':False}):
        calls=[]
        def read_only(workspace,method,path,**kwargs):
            calls.append((workspace,method,path,kwargs));return invalid
        monkeypatch.setattr(context.client,'request',read_only)
        with pytest.raises(ToolError) as error:
            invoke(context,'chrome_start_profile',**mutation(profile_id=1))
        assert error.value.code=='invalid_response'
        assert calls==[('chrome','GET','/api/v1/profiles/1',{'read_only':True})]


def test_profile_lookup_does_not_retry_through_the_legacy_list(context):
    with pytest.raises(ToolError) as error:
        invoke(context,'chrome_get_profile',profile_id=99)
    assert error.value.code=='not_found'
    assert context.client.calls==[('chrome','GET','/api/v1/profiles/99',{'read_only':True,'params':{'preview':25}})]


def test_start_stop_and_bm_open_use_exact_existing_endpoints_without_operation_id(context):
    invoke(context,'chrome_start_profile',**mutation(profile_id=1))
    assert context.client.calls[-1]==('chrome','POST','/api/profiles/1/start',{'read_only':False})
    invoke(context,'chrome_stop_profile',**mutation(profile_id=2))
    assert context.client.calls[-1][2]=='/api/profiles/2/stop'
    invoke(context,'chrome_open_bm_profile',**mutation(profile_id=2,bm_id='123'))
    assert context.client.calls[-1]==('chrome','POST','/api/bm-profiles/2/open',{'read_only':False,'body':{'bm_id':'123'}})
    with pytest.raises(ToolError):invoke(context,'chrome_start_profile',**mutation(profile_id=2))
    with pytest.raises(ToolError):invoke(context,'chrome_open_bm_profile',**mutation(profile_id=2,bm_id='999'))


def test_bm_open_requires_exact_choice_for_multiple_portfolios(context):
    portfolios=context.client.profiles[1]['bm']['portfolios']
    result=invoke(context,'chrome_open_bm_profile',**mutation(profile_id=2))
    assert result['bm_id']=='123' and context.client.calls[-1][3]['body']=={'bm_id':'123'}
    portfolios.append({'bm_id':'456','name':'Other BM'})
    before=sum(call[1]=='POST' for call in context.client.calls)
    with pytest.raises(ToolError) as error:
        invoke(context,'chrome_open_bm_profile',**mutation(profile_id=2))
    assert error.value.code=='bm_id_required'
    assert sum(call[1]=='POST' for call in context.client.calls)==before
    result=invoke(context,'chrome_open_bm_profile',**mutation(profile_id=2,bm_id='456'))
    assert result['bm_id']=='456' and context.client.calls[-1][3]['body']=={'bm_id':'456'}


def test_proxy_check_keeps_private_bm_scope_separate_and_projects_health(context):
    assert invoke(context,'chrome_list_proxies')['items']==[{'id':21,'status':'Working','has_credentials':True}]
    result=invoke(context,'chrome_check_proxies',**mutation(proxy_ids=[21]));assert result['result']['accepted']==[21]
    assert context.client.calls[-2]==('chrome','GET','/api/v1/proxies',{'read_only':True,
        'params':{'ids':'21','limit':1,'offset':0}})
    count=sum(call[1]=='POST' for call in context.client.calls)
    with pytest.raises(ToolError):invoke(context,'chrome_check_proxies',**mutation(proxy_ids=[999]))
    assert sum(call[1]=='POST' for call in context.client.calls)==count
    invoke(context,'chrome_check_bm_proxy',**mutation(profile_id=2))
    assert context.client.calls[-1][2]=='/api/bm-profiles/2/proxy/check'


def test_proxy_errors_return_only_safe_code_not_embedded_endpoint(context):
    context.client.profiles[0]['proxy']['error_message']='[PROXY_TUNNEL] http://private-user:private-password@private-proxy.test:9000/'
    result=invoke(context,'chrome_list_proxies')
    assert result['items'][0]['error_code']=='PROXY_TUNNEL' and 'private-' not in json.dumps(result)


def test_facebook_check_forwards_exact_window_and_has_no_browser_script_input(context):
    args=mutation(profile_ids=[1,1],timezone='America/Los_Angeles',check_date='2026-11-01',day_start='2026-11-01T07:00:00Z',day_end='2026-11-02T08:00:00Z',names_only=True)
    value=invoke(context,'chrome_check_facebook',**args)
    assert context.client.calls[0]==('chrome','GET','/api/v1/profile-ids',{'read_only':True,
        'params':{'scope':'regular','ids':'1'}})
    body=context.client.calls[-1][3]['body']
    assert context.client.calls[-1][2]=='/api/facebook/check' and body['profile_ids']==[1] and body['names_only'] is True
    assert body['day_start']=='2026-11-01T07:00:00+00:00' and 'operation_id' not in body
    assert value=={'accepted':[1],'errors':[{'profile_id':3,'message':'Busy'}]}
    with pytest.raises(ValidationError):CATALOG['chrome_check_facebook'].model.model_validate({**args,'day_end':'2026-11-01T08:00:00Z'})
    with pytest.raises(ToolError):invoke(context,'chrome_check_facebook',**{**args,'profile_ids':[2]})
    value=invoke(context,'chrome_facebook_refresh_status')
    assert PRIVATE not in json.dumps(value)


def test_facebook_check_verifies_all_selected_ids_in_bounded_chunks_before_writing(context):
    context.client.profiles=[profile(i,bm=i==101) for i in range(1,103)]
    args=mutation(profile_ids=list(range(1,103)),timezone='UTC',check_date='2026-11-01',
        day_start='2026-11-01T00:00:00Z',day_end='2026-11-02T00:00:00Z')
    with pytest.raises(ToolError) as error:
        invoke(context,'chrome_check_facebook',**args)
    assert error.value.code=='invalid_source'
    assert [call[2] for call in context.client.calls]==['/api/v1/profile-ids']*2
    assert context.client.calls[1][3]['params']['ids']=='101,102'


def test_outlook_open_never_enters_login_or_code_route(context):
    invoke(context,'chrome_open_outlook',**mutation(profile_id=1))
    assert context.client.calls[-1]==('chrome','POST','/api/outlook/1/open',{'read_only':False})
    assert all('/outlook/start'!=call[2] and not call[2].endswith('/code') for call in context.client.calls)


def test_reel_create_binds_exact_cached_profile_page_asset_and_keeps_metadata(context):
    args=schedule();value=invoke(context,'chrome_create_reel',**args)
    call=context.client.calls[-1]
    assert call[2]=='/api/reels' and call[3]['read_only'] is False
    assert call[3]['body']=={key:value for key,value in {**args,'page_url':PAGE_URL,'scheduled_local':'2030-11-01T10:30:00','fold':None,'request_id':args['operation_id']}.items() if key not in ('operation_id','fold')}
    assert value['job']['id']==10 and PRIVATE not in json.dumps(value)


@pytest.mark.parametrize('change',[{'profile_id':2},{'asset_id':'b'*32},{'page_url':'https://www.facebook.com/profile.php?id=999'}])
def test_reel_create_rejects_wrong_source_or_asset_before_post(context,change):
    args=schedule();args.update(change)
    with pytest.raises(ToolError):invoke(context,'chrome_create_reel',**args)
    assert not any(call[1]=='POST' for call in context.client.calls)


@pytest.mark.parametrize('page',[
    'https://evil.test/555','http://www.facebook.com/555','https://fixture:secret@www.facebook.com/555',
    'https://www.facebook.com:444/555','https://www.facebook.com/reels/create','https://www.facebook.com/..',
    'https://www.facebook.com/notifications','https://www.facebook.com/profile.php?id=１２３',
])
def test_page_inputs_never_allow_arbitrary_navigation(page):
    with pytest.raises(ValidationError):CATALOG['chrome_create_reel'].model.model_validate({**schedule(),'page_url':page})


@pytest.mark.parametrize('change',[
    {'scheduled_at':'2030-01-01T00:00:00Z'}, {'scheduled_local':'2030-11-01T10:30:00+00:00'},
    {'scheduled_local':'2030-11-01'}, {'scheduled_local':None}, {'fold':True}, {'timezone':'../../UTC'},
])
def test_schedule_requires_one_well_formed_time_and_strict_fold(change):
    args=schedule();args.update(change)
    with pytest.raises(ValidationError):CATALOG['chrome_create_reel'].model.model_validate(args)


@pytest.mark.parametrize('action,route,capability',[
    ('cancel','cancel','can_cancel'),('resume','resume','can_resume'),('verify','verify-link','can_verify_link'),
    ('archive','archive','can_archive'),('restore','restore','can_restore'),
])
def test_reel_actions_preflight_capability_and_use_bound_job_endpoint(context,action,route,capability):
    args=mutation(job_id=7)
    if action=='verify':args['reel_url']='https://m.facebook.com/reels/999?ref=fixture'
    value=invoke(context,'chrome_'+action+'_reel',**args)
    assert context.client.calls[-1][2]=='/api/reels/7/'+route and value['job_id']==7 and value['page_url']==PAGE_URL
    if action=='verify':assert context.client.calls[-1][3]['body']=={'reel_url':'https://www.facebook.com/reel/999'}
    assert context.client.calls[0]==('chrome','GET','/api/v1/reels/7',{'read_only':True})
    context.client.reels[0][capability]=False;context.client.calls=[]
    with pytest.raises(ToolError):invoke(context,'chrome_'+action+'_reel',**args)
    assert not any(call[1]=='POST' for call in context.client.calls)


def test_chrome_upload_delegates_only_registered_file_id_and_projects_asset(context):
    result=invoke(context,'chrome_reel_upload',**mutation(file_id='file_fixture_123'))
    assert context.client.calls==[('chrome','UPLOAD','file_fixture_123',{'kind':'video'})]
    assert result['asset']=={'id':ASSET,'name':'fixture.mp4','size':128,'mime':'video/mp4'}
    assert PRIVATE not in json.dumps(result)
    assert invoke(context,'chrome_reel_config')['accepted_extensions']==['.mp4','.m4v','.mov']


def test_recipient_config_jobs_and_start_strip_private_paths_mail_and_no_hidden_changes(context):
    config=invoke(context,'chrome_recipient_config')
    assert config=={'has_asset_root':True,'supported_setup_fields':['bio','address','categories'],'image_setup_supported':False}
    jobs=invoke(context,'chrome_list_recipient_jobs');assert PRIVATE not in json.dumps(jobs) and 'fixture-private@example.test' not in json.dumps(jobs)
    result=invoke(context,'chrome_start_recipient',**mutation(profile_id=1,expected_page_url=PAGE_URL,outlook_email='fixture-own@example.test',setup={'bio':'Authorized intro'}))
    assert result=={'profile_id':1,'result':{'accepted':[1]}}
    body=context.client.calls[-1][3]['body']
    assert body['setup']=={'bio':'Authorized intro','categories':[]} and 'operation_id' not in body and body['outlook_email']=='fixture-own@example.test'
    assert 'avatar_path' not in json.dumps(body) and 'cover_path' not in json.dumps(body)


@pytest.mark.parametrize('setup',[{'avatar_path':'C:/private/avatar.jpg'},{'cover_path':'https://evil.test/cover.jpg'},{'address':{'city':'Only city would clear other defaults'}}])
def test_recipient_setup_rejects_file_paths_and_implicit_address_clearing(setup):
    with pytest.raises(ValidationError):CATALOG['chrome_start_recipient'].model.model_validate(mutation(profile_id=1,expected_page_url=PAGE_URL,outlook_email='fixture@example.test',setup=setup))


def test_recipient_resume_stop_use_profile_job_binding_and_reject_active_resume(context):
    invoke(context,'chrome_resume_recipient',**mutation(profile_id=1));assert context.client.calls[-1][2]=='/api/page-recipient/1/resume'
    assert context.client.calls[-2]==('chrome','GET','/api/v1/recipient-jobs',{'read_only':True,
        'params':{'profile_id':1,'limit':1,'offset':0}})
    invoke(context,'chrome_stop_recipient',**mutation(profile_id=1));assert context.client.calls[-1][2]=='/api/page-recipient/1/stop'
    context.client.recipient[0]['active']=True;context.client.calls=[]
    with pytest.raises(ToolError):invoke(context,'chrome_resume_recipient',**mutation(profile_id=1))
    assert not any(call[1]=='POST' for call in context.client.calls)


def test_registry_journal_makes_non_idempotent_chrome_creation_replay_safe(context,tmp_path):
    registry=Registry(context.client,None,Journal(tmp_path),catalog=[CATALOG['chrome_create_reel']]);args=schedule()
    first=registry.call('chrome_create_reel',args);assert first['ok'] and not first['replayed']
    calls=deepcopy(context.client.calls)
    second=Registry(context.client,None,Journal(tmp_path),catalog=[CATALOG['chrome_create_reel']]).call('chrome_create_reel',args)
    assert second['ok'] and second['replayed'] and context.client.calls==calls
    with pytest.raises(ToolError):registry.call('chrome_create_reel',{**args,'caption':'Changed intent'})


def test_uncertain_chrome_creation_never_reaches_http_again_after_restart(context,tmp_path):
    context.client.uncertain_create=True;args=schedule()
    registry=Registry(context.client,None,Journal(tmp_path),catalog=[CATALOG['chrome_create_reel']])
    first=registry.call('chrome_create_reel',args);assert first['uncertain'] is True
    calls=deepcopy(context.client.calls)
    second=Registry(context.client,None,Journal(tmp_path),catalog=[CATALOG['chrome_create_reel']]).call('chrome_create_reel',args)
    assert second['uncertain'] is True and context.client.calls==calls


@pytest.mark.parametrize('name,arguments',[
    ('chrome_create_reel',schedule()),
    ('chrome_start_recipient',mutation(profile_id=1,expected_page_url=PAGE_URL,outlook_email='fixture@example.test')),
])
def test_semantically_missing_creation_acknowledgement_is_uncertain(context,monkeypatch,name,arguments):
    original=context.client.request
    def request(workspace,method,path,**kwargs):return {} if method=='POST' else original(workspace,method,path,**kwargs)
    monkeypatch.setattr(context.client,'request',request)
    with pytest.raises(ToolError) as error:invoke(context,name,**arguments)
    assert error.value.uncertain is True

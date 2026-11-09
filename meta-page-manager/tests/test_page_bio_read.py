"""Bio/about target selection through bounded read-only fake Graph responses."""
import httpx
import pytest

from conftest import FixtureBox,immediate
from page_manager.graph import GraphClient,GraphError
from page_manager.inputs import ConnectionInput,PageEditSource
from page_manager.service import PageManager


TOKEN='FixtureBioSourceToken82'
PAGE_TOKEN='FixtureBioPageToken82'
INTRO='Funny tees. Bold quotes. Wear your sense of humor.'
COMBINED='id,bio,location,category_list,website,is_always_open'


def read(respond):
    async def run():
        async with GraphClient(transport=httpx.MockTransport(respond)) as graph:
            return await graph.edit_details(PAGE_TOKEN,'555')
    return immediate(run())


def response_error(code,message='Private '+PAGE_TOKEN):
    return httpx.Response(400,json={'error':{'code':code,'message':message}})


def test_successful_omitted_bio_reads_about_and_preserves_other_current_fields():
    seen=[]
    def respond(req):
        seen.append(req)
        assert req.method=='GET' and req.url.path=='/v26.0/555'
        assert req.headers['authorization']=='Bearer '+PAGE_TOKEN and PAGE_TOKEN not in str(req.url)
        if req.url.params['fields']==COMBINED:
            return httpx.Response(200,json={'id':'555','location':{'city':'Fixture city'},'category_list':[{'id':'123','name':'Retail'}]})
        assert req.url.params['fields']=='id,about'
        return httpx.Response(200,json={'id':'555','about':INTRO})
    current,unavailable=read(respond)
    assert current=={'id':'555','location':{'city':'Fixture city'},'category_list':[{'id':'123','name':'Retail'}],'about':INTRO,'_bio_field':'about'}
    assert unavailable==['bio'] and len(seen)==2


@pytest.mark.parametrize('bio',[INTRO,'',None])
def test_present_native_bio_including_empty_is_not_replaced_with_about(bio):
    seen=[]
    def respond(req):
        seen.append(req)
        assert req.url.params['fields']==COMBINED
        return httpx.Response(200,json={'id':'555','bio':bio})
    current,unavailable=read(respond)
    assert current=={'id':'555','bio':bio,'_bio_field':'bio'} and unavailable==[] and len(seen)==1


def test_successful_about_omission_keeps_current_value_unknown_not_empty():
    seen=[]
    def respond(req):
        seen.append(req.url.params['fields'])
        return httpx.Response(200,json={'id':'555'})
    current,unavailable=read(respond)
    assert current=={'id':'555','_bio_field':'about'} and unavailable==['bio']
    assert 'bio' not in current and 'about' not in current and seen==[COMBINED,'id,about']


@pytest.mark.parametrize('about',['',None])
def test_explicit_about_empty_or_null_is_preserved_without_fabrication(about):
    def respond(req):
        return httpx.Response(200,json={'id':'555'} if req.url.params['fields']==COMBINED else {'id':'555','about':about})
    current,_=read(respond)
    assert current['_bio_field']=='about' and 'about' in current and current['about']==about


@pytest.mark.parametrize('stage',['combined','about'])
@pytest.mark.parametrize('wrong_id',['777',None])
def test_mismatched_or_missing_page_identity_stops_reads(stage,wrong_id):
    seen=[]
    def respond(req):
        seen.append(req.url.params['fields'])
        wrong=stage=='combined' or req.url.params['fields']=='id,about'
        return httpx.Response(200,json={'id':wrong_id if wrong else '555','about':INTRO})
    with pytest.raises(GraphError):read(respond)
    assert seen==([COMBINED] if stage=='combined' else [COMBINED,'id,about'])


def test_about_rejected_with_code100_is_unavailable_without_clearing_current_fields():
    seen=[]
    def respond(req):
        seen.append(req.url.params['fields'])
        if req.url.params['fields']==COMBINED:return httpx.Response(200,json={'id':'555','location':{'city':'Fixture city'}})
        return response_error(100)
    current,unavailable=read(respond)
    assert current=={'id':'555','location':{'city':'Fixture city'},'_bio_field':None}
    assert unavailable==['bio','about'] and seen==[COMBINED,'id,about']


@pytest.mark.parametrize('message,status',[
    ('Private '+PAGE_TOKEN,'permission_limited'),('API access blocked.','api_access_blocked'),
])
def test_about_permission_denial_is_not_reclassified_as_missing_or_retried(message,status):
    seen=[]
    def respond(req):
        seen.append(req.url.params['fields'])
        return httpx.Response(200,json={'id':'555'}) if req.url.params['fields']==COMBINED else response_error(200,message)
    with pytest.raises(GraphError) as caught:read(respond)
    assert caught.value.status==status and PAGE_TOKEN not in str(caught.value)
    assert seen==[COMBINED,'id,about']


@pytest.mark.parametrize('bio_rejected',[False,True])
def test_split_optional_reads_also_choose_about_for_missing_or_rejected_native_bio(bio_rejected):
    seen=[]
    def respond(req):
        fields=req.url.params['fields'];seen.append(fields)
        assert req.method=='GET'
        if fields==COMBINED or fields=='id,bio' and bio_rejected:return response_error(100)
        values={'id,bio':{'id':'555'},'id,location':{'id':'555','location':{'street':'Fixture street'}},
                'id,category_list':{'id':'555','category_list':[{'id':'123','name':'Retail'}]},'id,website':{'id':'555'},'id,is_always_open':{'id':'555'},'id,about':{'id':'555','about':INTRO}}
        return httpx.Response(200,json=values[fields])
    current,unavailable=read(respond)
    assert current['_bio_field']=='about' and current['about']==INTRO and unavailable==['bio']
    assert current['location']=={'street':'Fixture street'} and current['category_list'][0]['id']=='123'
    assert seen==[COMBINED,'id,bio','id,location','id,category_list','id,website','id,is_always_open','id,about']


@pytest.mark.parametrize('about_present',[True,False])
def test_editor_options_exposes_selected_about_and_keeps_omission_unknown(tmp_path,about_present):
    seen=[]
    def respond(req):
        seen.append(req)
        assert req.method=='GET' and req.headers['authorization']=='Bearer '+PAGE_TOKEN
        if req.url.path.endswith('/me'):return httpx.Response(200,json={'id':'555','name':'Fixture Page'})
        fields=req.url.params['fields']
        assert fields in (COMBINED,'id,about')
        return httpx.Response(200,json={'id':'555',**({'about':INTRO} if fields=='id,about' and about_present else {})})
    manager=PageManager(tmp_path,secret_box=FixtureBox(),graph_factory=lambda:GraphClient(transport=httpx.MockTransport(respond)))
    source=manager.save_connection(ConnectionInput(label='Fixture source',token_type='system_user',token=TOKEN))
    manager.store.sync(source['id'],manager.store.connection(source['id'])['token_revision'],[{
        'id':'555','name':'Fixture Page','sources':['assigned_pages'],'tasks':None,'encrypted_page_token':manager.secrets.encrypt(PAGE_TOKEN),
    }],True,'working',None,{'id':'77','name':'Fixture user'})
    result=immediate(manager.edit_options('555',PageEditSource(connection_id=source['id'])))
    assert result['current']['bio_field']=='about' and result['current']['bio']==(INTRO if about_present else None)
    assert 'bio' in result['supported_fields'] and result['current']['location'] is None and result['current']['category_ids'] is None
    assert len(seen)==3 and any('Giới thiệu' in warning for warning in result['warnings'])

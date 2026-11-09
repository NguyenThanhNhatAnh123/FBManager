"""SQL windowing/public payload checks on a disposable 1007-video library."""
import asyncio
import json
from uuid import UUID

import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from test_list_pagination import get
from test_http_security import session


@pytest.fixture
def library(tmp_path,monkeypatch):
    async def inline(call,*args,**kwargs):return call(*args)
    monkeypatch.setattr(asyncio,'to_thread',inline)
    def forbidden():pytest.fail('Listing saved videos cannot call Meta')
    app=create_app(tmp_path,secret_box=FixtureBox(),graph_factory=forbidden,reel_graph_factory=forbidden)
    store=app.state.manager.store
    cid=store.save_connection({'label':'Source A','token_type':'page'},FixtureBox().encrypt('private-fixture-token'))
    store.sync(cid,1,[{'id':'10001','name':'Page Áo','sources':['page'],'tasks':['CREATE_CONTENT']}],True,'working',None,{'id':'10001'})
    with store.connect() as db:
        db.executemany('INSERT INTO reel_assets(id,name,path,size,sha256,mime,created_at) VALUES(?,?,?,?,?,?,?)',
            [(f'{i:032x}',f'Áo-{i:04}.mp4',f'private-file-{i}',100,f'{i:064x}','video/mp4','2030-11-01T00:00:00Z') for i in range(1007)])
        db.executemany('INSERT INTO reel_jobs(request_id,payload_hash,page_id,page_name,connection_id,connection_label,token_revision,asset_id,asset_name,caption,scheduled_at,scheduled_local,timezone,status,step,message,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(str(UUID(int=i+1)),'fixture','10001','Page Áo',cid,'Source A',1,f'{i:032x}',f'Áo-{i:04}.mp4','caption%_ literal' if i==12 else 'caption '+str(i),'2030-11-02T12:00:00Z','2030-11-02T12:00','UTC','completed' if i%3==0 else 'scheduled','fixture','Ready','2030-11-01T00:00:00Z','2030-11-01T00:00:00Z') for i in range(1000)])
    return app,store,session(app)


def test_paged_jobs_hydrate_only_sql_window_and_exact_assets(library,monkeypatch):
    app,store,headers=library
    monkeypatch.setattr(store,'reel_jobs',lambda:pytest.fail('Paged GET must not hydrate all jobs'))
    monkeypatch.setattr(store,'reel_assets',lambda:pytest.fail('Paged GET must not hydrate all assets'))
    first=get(app,'/api/reels?limit=12&offset=0',headers)[2]
    last=get(app,'/api/reels?limit=12&offset=996',headers)[2]
    assert first['total']==last['total']==1000
    assert len(first['jobs'])==12 and len(last['jobs'])==4
    assert [row['id'] for row in last['jobs']]==[4,3,2,1]
    assert len({row['asset']['id'] for row in first['jobs']})==12
    assert all(row['asset']['id']==row['asset_id'] for row in first['jobs'])
    assert not {row['id'] for row in first['jobs']} & {row['id'] for row in last['jobs']}
    raw=json.dumps(first,ensure_ascii=False)
    assert len(raw.encode())<25000
    assert all(secret not in raw for secret in ('private-file','sha256','checkpoint','payload_hash','private-fixture-token'))
    one=get(app,'/api/reels?limit=1&request_id='+str(UUID(int=13)),headers)[2]
    assert one['total']==1 and one['jobs'][0]['id']==13
    direct=get(app,'/api/reels/13',headers)[2]['job']
    assert direct['id']==13 and direct['asset']['id']==direct['asset_id']


def test_assets_sql_filters_counts_unicode_and_literal_search(library,monkeypatch):
    app,store,headers=library
    monkeypatch.setattr(store,'reel_assets',lambda:pytest.fail('Paged assets must not hydrate whole catalog'))
    value=get(app,'/api/reels/assets?limit=25&offset=1000',headers)[2]
    assert value['total']==1007 and len(value['assets'])==7
    assert value['summary']=={'total':1007,'scheduled':1000,'published':334,'unused':7}
    assert [row['id'] for row in value['assets']]==[f'{i:032x}' for i in range(6,-1,-1)]
    unused=get(app,'/api/reels/assets?limit=12&status=unused',headers)[2]
    assert unused['total']==len(unused['assets'])==7
    assert all(row['linked_job_count']==0 and row['linked_jobs']==[] for row in unused['assets'])
    searched=get(app,'/api/reels/assets?limit=12&search=%C3%A1O-0012',headers)[2]
    assert searched['total']==1 and searched['assets'][0]['id']==f'{12:032x}'
    clamped=get(app,'/api/reels/assets?limit=25&offset=1000&search=%C3%A1O-0012',headers)[2]
    assert clamped['offset']==0 and clamped['total']==len(clamped['assets'])==1
    literal=get(app,'/api/reels/assets?limit=12&search=%25_',headers)[2]
    assert literal['total']==1 and literal['assets'][0]['id']==f'{12:032x}'
    assert len(literal['assets'][0]['linked_jobs'])==1
    exact=get(app,'/api/reels/assets?limit=1&asset_id='+f'{1001:032x}',headers)[2]
    assert exact['total']==1 and exact['assets'][0]['id']==f'{1001:032x}'
    assert get(app,'/api/reels?limit=12&search=%C3%A1O-0012',headers)[2]['total']==1
    assert get(app,'/api/reels?limit=12&status=completed',headers)[2]['total']==334
    assert get(app,'/api/reels?limit=12&status=pending',headers)[2]['total']==666
    assert get(app,'/api/reels?limit=12&offset=100000000000000000000000',headers)[2]['jobs']==[]


def test_older_completed_job_is_included_in_asset_badge_without_unbounded_history(library):
    app,store,headers=library
    with store.connect() as db:
        source=dict(db.execute('SELECT * FROM reel_jobs WHERE id=1').fetchone())
        columns=[key for key in source if key!='id']
        for index in range(4):
            value={**source,'request_id':str(UUID(int=5000+index)),'status':'scheduled'}
            db.execute('INSERT INTO reel_jobs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',[value[key] for key in columns])
    asset=get(app,'/api/reels/assets?limit=1&asset_id='+f'{0:032x}',headers)[2]['assets'][0]
    assert asset['linked_job_count']==5 and asset['has_published_jobs'] is True
    assert len(asset['linked_jobs'])==3 and all(job['status']=='scheduled' for job in asset['linked_jobs'])


@pytest.mark.parametrize('path',['/api/reels','/api/reels/assets'])
@pytest.mark.parametrize('query',['limit=0','limit=101','offset=-1','status=arbitrary','search='+'x'*201])
def test_window_api_validation(library,path,query):
    app,store,headers=library
    assert get(app,path+'?'+query,headers)[0]==422
    assert store.reel_job_page(1)['total']==1000

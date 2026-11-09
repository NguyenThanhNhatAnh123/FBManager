"""SQL-windowed idea browser reads on synthetic 1,025-item ledgers, no agents or provider I/O."""
from datetime import datetime,timedelta,timezone
import json
from uuid import uuid4,uuid5,NAMESPACE_URL

import pytest

from test_idea_workspace_api import fixture,request


def seed(app,total=1025):
    store=app.state.manager.store
    start=datetime(2030,11,1,tzinfo=timezone.utc)
    ids=[]
    with store.connect() as db:
        for index in range(total):
            iid=str(uuid5(NAMESPACE_URL,'synthetic-paged-idea-'+str(index)));ids.append(iid)
            created=(start+timedelta(seconds=index)).isoformat()
            filename=('Straße Đỏ ' if index==total-1 else 'Idea ')+str(index+1).zfill(4)+'.png'
            state='verified' if index%2 else 'prepared'
            campaign=json.dumps({'campaign_url':'https://blueox179.com/campaign/fixture'+str(index)}) if index%2 else None
            db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?,?,?,?,?)',(iid,format(index+1,'064x'),filename,state,campaign,created))
            for ordinal in range(7):
                vid=str(uuid5(NAMESPACE_URL,iid+'-'+str(ordinal)))
                db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?,?,?,?,?)',
                    (vid,iid,ordinal+1,'metadata_registered' if ordinal==0 else 'planned',format(100000+index,'064x') if ordinal==0 else None,0,'{}',str(uuid4())))
                if ordinal==0:
                    for hours,value in ((24,index),(26,index+100)):
                        observed=(start+timedelta(hours=hours)).isoformat()
                        db.execute('INSERT INTO idea_pipeline_views VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (vid,'900001',str(700000+index),'views',value,'available',observed,start.isoformat(),1,'provider_published_at'))
            for ordinal in range(2):
                db.execute('''INSERT INTO idea_manual_runs(id,request_id,idea_id,artwork_sha256,filename,payload,payload_hash,
                    status,stage,message,cancel_requested,created_at,fence) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (str(uuid4()),str(uuid4()),iid,format(index+1,'064x'),filename,'{"private":"MUST_NOT_BE_SERIALIZED"}',
                     'fixture-hash','completed' if ordinal else 'failed','fixture','Synthetic run',0,created,1))
    return ids


def no_full_reads(app,monkeypatch):
    def forbidden(*_args,**_kwargs):raise AssertionError('A paged browser read must not hydrate the global ledger')
    for obj,name in ((app.state.idea_pipeline,'status'),(app.state.idea_pipeline,'ranking'),
                     (app.state.idea_runs,'status'),(app.state.idea_runs,'list'),(app.state.idea_views,'latest')):
        monkeypatch.setattr(obj,name,forbidden)


def test_summary_and_visible_ideas_only_hydrate_requested_sql_window(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);ids=seed(app);pipeline=app.state.idea_pipeline
    seen=[];original=pipeline._public_idea
    def track(db,row):seen.append(row['id']);return original(db,row)
    monkeypatch.setattr(pipeline,'_public_idea',track);no_full_reads(app,monkeypatch)
    code,_,value=request(app,'/api/idea-pipeline?summary=true',headers=headers)
    assert code==200 and value['pipeline']['idea_count']==1025 and value['pipeline']['ideas']==[]
    assert value['runs']['items']==[] and value['performance']['items']==[] and seen==[]
    assert value['pipeline']['summary']['planned_video_count']==7175
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&offset=1008',headers=headers)
    assert code==200 and value['pipeline']['pagination']=={'total':1025,'limit':12,'offset':1008}
    assert [row['id'] for row in value['pipeline']['ideas']]==ids[1008:1020]
    assert seen==ids[1008:1020] and sum(len(row['variants']) for row in value['pipeline']['ideas'])==84
    assert len(value['runs']['items'])==12 and all(row['status']=='completed' for row in value['runs']['items'])
    assert all(row['idea_id'] in seen for row in value['runs']['items'])
    assert value['performance']['items']==[] and len(json.dumps(value).encode())<110000
    assert 'MUST_NOT_BE_SERIALIZED' not in json.dumps(value)
    code,_,last=request(app,'/api/idea-pipeline?summary=true&limit=12&offset=9999999',headers=headers)
    assert code==200 and last['pipeline']['pagination']=={'total':1025,'limit':12,'offset':1020}
    assert len(last['pipeline']['ideas'])==5
    print('Bounded idea payload bytes:',len(json.dumps(value).encode()),'hydrated ideas:',12,'variants:',84,'runs:',12,'catalog:',1025)


def test_full_catalog_filters_views_sort_and_missing_latest_never_falls_back(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);ids=seed(app);no_full_reads(app,monkeypatch)
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&search=STRASSE',headers=headers)
    assert code==200 and value['pipeline']['pagination']['total']==1
    assert value['pipeline']['ideas'][0]['id']==ids[-1]
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&status=verified&offset=504',headers=headers)
    assert code==200 and value['pipeline']['pagination']['total']==512
    assert len(value['pipeline']['ideas'])==8 and all(row['campaign_state']=='verified' for row in value['pipeline']['ideas'])
    with app.state.manager.store.connect() as db:
        variant=db.execute('SELECT id FROM idea_pipeline_variants WHERE idea_id=? AND ordinal=1',(ids[-1],)).fetchone()[0]
        db.execute('INSERT INTO idea_pipeline_views VALUES(?,?,?,?,?,?,?,?,?,?)',
            (variant,'900001','701024','views',None,'unavailable','2030-11-02T03:00:00+00:00','2030-11-01T00:00:00+00:00',0,'provider_published_at'))
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&sort=views',headers=headers)
    assert code==200 and value['pipeline']['ideas'][0]['id']==ids[-2]
    assert value['pipeline']['ideas'][0]['performance']=={'views':1123,'known':1,'total':7}
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&search=STRASSE&sort=views',headers=headers)
    assert value['pipeline']['ideas'][0]['performance']=={'views':None,'known':0,'total':7}
    code,_,value=request(app,'/api/idea-pipeline?summary=true&limit=12&search=no-results',headers=headers)
    assert code==200 and value['pipeline']['pagination']=={'total':0,'limit':12,'offset':0} and value['pipeline']['ideas']==[]


def test_ranking_is_a_requested_sql_window_and_matches_legacy_medians(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);ids=seed(app);no_full_reads(app,monkeypatch)
    code,_,value=request(app,'/api/idea-pipeline?summary=true&ranking_limit=10&ranking_offset=1000',headers=headers)
    assert code==200 and value['pipeline']['ideas']==[]
    rank=value['ranking'];assert rank['total']==1025 and rank['limit']==10 and rank['offset']==1000
    rows=[row for cohort in rank['cohorts'] for row in cohort['items']]
    assert len(rows)==10 and rows[0]['idea_id']==ids[24] and rows[0]['median_views_24h']==24
    assert all(row['sample_count']==1 and row['median_page_lift'] is not None for row in rows)
    code,_,overshoot=request(app,'/api/idea-pipeline?summary=true&ranking_limit=10&ranking_offset=9999999',headers=headers)
    assert code==200 and overshoot['ranking']['offset']==1020 and overshoot['ranking']['total']==1025
    assert sum(len(cohort['items']) for cohort in overshoot['ranking']['cohorts'])==5
    # An independent, small oracle checks SQL rank semantics for nearest24h,
    # separate metric cohorts and Page-relative medians without large payloads.
    (tmp_path/'small').mkdir();small,small_headers,_=fixture(tmp_path/'small');seed(small,9)
    legacy=small.state.idea_pipeline.ranking()
    code,_,paged=request(small,'/api/idea-pipeline?summary=true&ranking_limit=100',headers=small_headers)
    assert code==200 and paged['ranking']['cohorts']==legacy['cohorts']


@pytest.mark.parametrize('query',['limit=0','limit=101','offset=-1','status=anything','sort=anything','ranking_limit=101','ranking_offset=-1','search='+'a'*201])
def test_invalid_sql_window_is_rejected_before_reading(tmp_path,query):
    app,headers,_=fixture(tmp_path)
    assert request(app,'/api/idea-pipeline?summary=true&'+query,headers=headers)[0]==422

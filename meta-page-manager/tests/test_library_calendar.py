"""Read-only library calendars, counted in SQL before paging; all ledgers synthetic."""
from uuid import uuid4

import pytest

from test_idea_catalog_paging import fixture, request, no_full_reads
from page_manager.errors import AppError


def seed_dates(app, dates):
    store=app.state.manager.store
    ids=[]
    with store.connect() as db:
        for index,stamp in enumerate(dates):
            iid=str(uuid4());ids.append(iid)
            db.execute('INSERT INTO idea_pipeline_ideas VALUES(?,?,?,?,?,?)',
                       (iid,f'{index+1:064x}',f'Idea {index:04}.png','prepared',None,stamp))
            db.execute('INSERT INTO reel_assets VALUES(?,?,?,?,?,?,?)',
                       (f'asset-{index:04}',f'Video {index:04}.mp4','fixture-only',40,f'{index+2000:064x}','video/mp4',stamp))
    return ids


@pytest.mark.parametrize('calendar,total',[('2029',1),('2029-12',1),('2029-12-31',1),('2030-01-01',31),('2030-03-10',2),('2030-11-03',2)])
def test_local_calendar_before_count_and_window_for_both_libraries(tmp_path,monkeypatch,calendar,total):
    app,headers,_=fixture(tmp_path)
    # LA year boundary, the 23-hour spring day and both instances of the fall fold.
    dates=['2030-01-01T07:59:59Z']+['2030-01-01T08:00:00Z']*31+[
        '2030-03-10T08:00:00Z','2030-03-11T06:59:59Z','2030-03-11T07:00:00Z',
        '2030-11-03T08:30:00Z','2030-11-03T09:30:00Z','2030-11-04T08:00:00Z']
    seed_dates(app,dates);no_full_reads(app,monkeypatch)
    seen=[];original=app.state.idea_pipeline._public_idea
    def track(db,row):seen.append(row['id']);return original(db,row)
    monkeypatch.setattr(app.state.idea_pipeline,'_public_idea',track)
    result=app.state.idea_pipeline.catalog(limit=12,offset=9999,calendar=calendar)
    assert {key:result[key] for key in ('total','limit','offset')}=={'total':total,'limit':12,'offset':((total-1)//12)*12}
    assert len(result['items'])==len(seen)==total-((total-1)//12)*12
    result=app.state.manager.store.reel_asset_page(limit=12,offset=9999,calendar=calendar)
    assert result['total']==total and result['offset']==((total-1)//12)*12
    assert len(result['rows'])==len(seen) and result['summary']['total']==len(dates)


def test_calendar_defaults_to_bounded_read_and_combines_search(tmp_path,monkeypatch):
    app,headers,_=fixture(tmp_path);seed_dates(app,['2030-01-01T08:00:00Z']*31)
    no_full_reads(app,monkeypatch)
    for path,items in (('/api/idea-pipeline?summary=true','ideas'),('/api/reels/assets?','assets')):
        code,_,result=request(app,path+'&calendar=2030-01-01',headers=headers)
        assert code==200
        body=result['pipeline'] if items=='ideas' else result
        page=body.get('pagination',body)
        assert page['total']==31 and page['limit']==12 and len(body[items])==12
        code,_,result=request(app,path+'&limit=12&calendar=2030-01-01&search=0030',headers=headers)
        body=result['pipeline'] if items=='ideas' else result
        assert code==200 and body.get('pagination',body)['total']==1 and len(body[items])==1
        code,_,result=request(app,path+'&limit=12&calendar=2030-01-02',headers=headers)
        body=result['pipeline'] if items=='ideas' else result
        assert code==200 and body.get('pagination',body)['total']==0 and body[items]==[]


@pytest.mark.parametrize('values',[{'calendar':'20'},{'calendar':'0999'},{'calendar':'2030-13'},{'calendar':'2030-02-29'},{'calendar':'2030-01-01T'},{'timezone':'Invalid/Zone'}])
def test_invalid_calendar_never_silently_reads_all(tmp_path,values):
    app,_,_=fixture(tmp_path)
    for method in (app.state.idea_pipeline.catalog,app.state.manager.store.reel_asset_page):
        with pytest.raises(AppError) as caught:method(**values)
        assert caught.value.code=='invalid_library_calendar' and caught.value.status_code==422


def test_manual_review_projection_never_claims_worker_qa(tmp_path):
    app,_,_=fixture(tmp_path);iid=seed_dates(app,['2030-01-01T08:00:00Z'])[0]
    pipeline=app.state.idea_pipeline
    with pipeline.store.connect() as db:
        # The production schema is owned by the consumer; this narrow fixture
        # records the same three binding fields and status the reader projects.
        db.execute('CREATE TABLE IF NOT EXISTS idea_pipeline_approved_videos(variant_id TEXT,video_sha256 TEXT,reservation_id TEXT,status TEXT)')
        for index,status in enumerate(('manual_review_pending','approved','ready','withdrawn')):
            vid,rid=str(uuid4()),str(uuid4());sha=f'{index+1:064x}'
            db.execute('INSERT INTO idea_pipeline_variants VALUES(?,?,?,?,?,?,?,?)',(vid,iid,index+1,'metadata_registered',sha,0,'{}',rid))
            db.execute('INSERT INTO idea_pipeline_approved_videos VALUES(?,?,?,?)',(vid,sha,rid,status))
    value=pipeline.idea(iid)
    assert [row['verification'] for row in value['variants']]==['manual_visual_review_pending','worker_qa_approved','worker_qa_approved','metadata_only']

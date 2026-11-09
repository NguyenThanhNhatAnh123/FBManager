"""Bounded local pagination reaches saved rows without scans or Meta calls."""
import json
import tracemalloc
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest

from conftest import FixtureBox
from page_manager.app import create_app
from test_http_security import request, session


def get(app, url, headers):
    parsed = urlsplit(url)

    async def with_query(scope, receive, send):
        await app({**scope, 'query_string': parsed.query.encode()}, receive, send)

    return request(with_query, parsed.path, headers=headers)


def fixture(tmp_path):
    def forbidden_graph():
        pytest.fail('Paging local data cannot call Meta')

    directory = tmp_path / 'videos'
    directory.mkdir()
    app = create_app(tmp_path / 'manager', secret_box=FixtureBox(), graph_factory=forbidden_graph,
                     video_source_directory=directory)
    return app, session(app)


def test_source_pages_reach_older_rows_and_keep_global_counts_and_configuration(tmp_path):
    app, headers = fixture(tmp_path)
    store = app.state.manager.store
    with store.connect() as db:
        db.executemany(
            'INSERT INTO video_source_items(id,sha256,filename,path,signature,size,config_revision,payload,status,message,created_at,updated_at) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            [(str(i), f'{i:064x}', f'clip-{i:03}.mp4', 'private-fixture-path', 'fixture', 40, 1,
              json.dumps({'page_id': '555', 'page_name': 'Fixture Page'}), 'ready', 'Ready locally',
              '2030-11-01T00:00:00Z', '2030-11-01T00:00:00Z') for i in range(1, 124)],
        )
    first = get(app, '/api/video-source?limit=10&offset=0', headers)
    second = get(app, '/api/video-source?limit=10&offset=10', headers)
    last = get(app, '/api/video-source?limit=10&offset=120', headers)
    assert all(value[0] == 200 for value in (first, second, last))
    assert [item['id'] for item in first[2]['items']] == [str(i) for i in range(123, 113, -1)]
    assert [item['id'] for item in second[2]['items']] == [str(i) for i in range(113, 103, -1)]
    assert [item['id'] for item in last[2]['items']] == ['3', '2', '1']
    assert all(value[2]['total'] == 123 and value[2]['limit'] == 10 for value in (first, second, last))
    assert last[2]['offset'] == 120
    assert first[2]['settings'] == last[2]['settings'] and first[2]['stats'] == last[2]['stats']
    assert not store.reel_jobs() and not list((tmp_path / 'videos').iterdir())
    assert 'private-fixture-path' not in json.dumps(last[2])
    status, _, empty = get(app, '/api/video-source?limit=25&offset=1000', headers)
    assert status == 200 and empty['items'] == [] and empty['total'] == 123
    assert len(get(app, '/api/video-source', headers)[2]['items']) == 100


def test_event_pages_are_counted_disjoint_and_compatible_with_existing_store_reads(tmp_path):
    app, headers = fixture(tmp_path)
    store = app.state.manager.store
    with store.connect() as db:
        db.executemany('INSERT INTO events(action,status,message,created_at) VALUES(?,?,?,?)',
                       [('fixture', 'success', f'event-{i:03}', '2030-11-01T00:00:00Z') for i in range(225)])
    first = get(app, '/api/events?limit=25&offset=0', headers)[2]
    last = get(app, '/api/events?limit=25&offset=200', headers)[2]
    assert first['total'] == last['total'] == store.event_count() == 225
    assert first['limit'] == 25 and last['offset'] == 200
    assert [row['id'] for row in last['items']] == list(range(25, 0, -1))
    assert not {row['id'] for row in first['items']} & {row['id'] for row in last['items']}
    assert isinstance(store.events(), list) and len(store.events()) == 50
    assert store.events(25, 200) == last['items']
    status, _, empty = get(app, '/api/events?limit=100&offset=1000000000000000000000000000000', headers)
    assert status == 200 and empty['items'] == [] and empty['total'] == 225
    assert store.event_count() == 225 and not store.reel_jobs()


@pytest.mark.parametrize('route', ['/api/events', '/api/video-source'])
@pytest.mark.parametrize('query', ['limit=0', 'limit=101', 'limit=1.5', 'offset=-1', 'offset=1.5'])
def test_pagination_queries_are_bounded_and_read_only(tmp_path, route, query):
    app, headers = fixture(tmp_path)
    assert get(app, route + '?' + query, headers)[0] == 422
    assert app.state.manager.store.event_count() == 0 and not app.state.manager.store.reel_jobs()


def seed_catalog(app):
    store=app.state.manager.store
    stamp='2030-11-03T16:00:00+00:00'
    with store.connect() as db:
        db.executemany('INSERT INTO connections(id,label,token_type,encrypted_token,status,permissions,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                       [(i,f'Connection {i:03}','system_user','fixture-encrypted-secret','working',json.dumps([{'permission':'fixture_'+str(j),'status':'granted'} for j in range(15)]),stamp,stamp) for i in range(1,131)])
        db.executemany('INSERT INTO pages(id,name,details,posts_checked_at) VALUES(?,?,?,?)',
                       [(str(100000+i),f'Page {i:04}',json.dumps({'about':'x'*4096}),stamp) for i in range(1,1001)])
        db.executemany('INSERT INTO connection_pages(connection_id,page_id,sources,active,token_revision,tasks) VALUES(?,?,?,1,1,?)',
                       [(cid,str(100000+i),'["accounts"]','["ANALYZE"]') for i in range(1,1001) for cid in (1,2)])
        posts=[('100001',f'100001_{i}',str(i)+' '+'p'*4096,stamp) for i in range(1,1301)]
        db.executemany('INSERT INTO posts(page_id,post_id,message,created_time) VALUES(?,?,?,?)',posts)
        db.execute('UPDATE pages SET posts_latest_ids=? WHERE id=?',(json.dumps([f'100001_{i}' for i in range(1,301)]),'100001'))
    return store


def test_sql_core_windows_have_bounded_payloads_and_never_hydrate_full_catalog(tmp_path,monkeypatch):
    from page_manager import service
    app,headers=fixture(tmp_path);store=seed_catalog(app);manager=app.state.manager
    monkeypatch.setattr(service,'now',lambda:'2030-11-03T16:00:00+00:00')
    monkeypatch.setattr(store,'catalog',lambda:pytest.fail('Core read cannot hydrate every Page, binding and post'))
    tracemalloc.start();small=manager.pages(limit=10,offset=990);_,small_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    tracemalloc.start();legacy=manager.pages();_,full_peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert small['total']==1000 and len(small['items'])==10 and small['items'][0]['name']=='Page 0991'
    assert all(len(row['connections'])==2 for row in small['items'])
    small_bytes=len(json.dumps(small).encode());full_bytes=len(json.dumps(legacy).encode())
    assert small_bytes<full_bytes/50 and small_peak<full_peak/5
    code,_,connections=get(app,'/api/connections?limit=10&offset=120',headers)
    assert code==200 and connections['total']==130 and len(connections['items'])==10
    code,_,options=get(app,'/api/connections/options?limit=10&offset=120',headers)
    assert code==200 and options['total']==130 and len(options['items'])==10
    assert set(options['items'][0])=={'id','label','status','connected','has_token'}
    assert len(json.dumps(options))<len(json.dumps(connections))/3
    detail=manager.page('100001',limit=10,offset=290)
    assert detail['posts_total']==300 and detail['posts_offset']==290 and len(detail['posts'])==10
    assert len(manager.page('100001')['posts'])==25,'Legacy detail keeps its existing default window'
    assert all('fixture-encrypted-secret' not in json.dumps(value) for value in (small,options,connections,detail))
    monkeypatch.setattr(manager,'_pages',lambda *args,**kwargs:pytest.fail('Overview uses only SQL aggregates'))
    assert manager.overview()['stats']['pages']==1000
    print({'core_pages_total':1000,'window_rows':10,'window_json_bytes':small_bytes,'legacy_json_bytes':full_bytes,'window_python_peak_bytes':small_peak,'legacy_python_peak_bytes':full_peak,'cached_post_window_rows':len(detail['posts']),'cached_posts_total':detail['posts_total']})


def test_sql_today_filters_preserve_timezone_partial_coverage_and_canonical_counts(tmp_path,monkeypatch):
    from page_manager import service
    app,headers=fixture(tmp_path);store=seed_catalog(app)
    monkeypatch.setattr(service,'now',lambda:'2030-11-03T16:00:00+00:00')
    with store.connect() as db:
        db.execute('UPDATE pages SET name=? WHERE id=?',('Straße Funny','100001'))
        db.execute('UPDATE pages SET posts_latest_ids=?,posts_has_more=1,posts_oldest=? WHERE id=?',
                   (json.dumps(['100001_1','100001_1','100001_2','100001_3']),'2030-11-02T12:00:00+00:00','100001'))
        db.execute('UPDATE posts SET created_time=? WHERE post_id=?',('2030-11-03T07:00:00+00:00','100001_1'))
        db.execute('UPDATE posts SET created_time=? WHERE post_id=?',('2030-11-04T08:00:00+00:00','100001_2'))
        db.execute('UPDATE posts SET created_time=? WHERE post_id=?',('2030-11-03T06:59:59+00:00','100001_3'))
        db.execute('UPDATE pages SET posts_error=? WHERE id=?',('partial','100002'))
        db.execute('UPDATE pages SET posts_checked_at=NULL WHERE id=?',('100003',))
    value=app.state.manager.pages(limit=10,search='STRASSE',today='posted')
    assert value['total']==1 and value['items'][0]['posts_today']==1 and value['items'][0]['posts_today_complete']
    unknown=app.state.manager.pages(limit=10,today='unknown')
    assert unknown['total']==2 and {row['id'] for row in unknown['items']}=={'100002','100003'}
    assert app.state.manager.pages(limit=10,today='zero')['total']==997
    # Two sources per Page must not double the filtered or overview total.
    assert app.state.manager.pages(limit=10,connection_id=2)['total']==1000
    overview=app.state.manager.overview()['stats'];assert overview['pages']==1000 and overview['pages_with_posts_today']==1 and overview['today_count_known'] is False


def test_iana_day_bounds_include_dst_and_trusted_node_fallback(monkeypatch):
    from page_manager import timezones
    value='2030-11-03T16:00:00+00:00'
    start,end=timezones.day_bounds(value,'America/Los_Angeles')
    assert (datetime.fromisoformat(end.replace('Z','+00:00'))-datetime.fromisoformat(start.replace('Z','+00:00'))).total_seconds()==25*3600
    monkeypatch.setattr(timezones,'ZoneInfo',lambda _:(_ for _ in ()).throw(timezones.ZoneInfoNotFoundError()))
    fallback=timezones.day_bounds(value,'America/Los_Angeles')
    assert [timezones.parse_time(item) for item in fallback]==[timezones.parse_time(start),timezones.parse_time(end)]

"""Offline proxy checks exclude BM assignments even before startup migration."""
import sqlite3

import pytest

from app.services.proxy_scope import ordinary_proxy_where


def make_database(path,roles=False,scopes=False):
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE proxies (id INTEGER PRIMARY KEY)')
        connection.executemany('INSERT INTO proxies VALUES (?)',[(value,) for value in range(1,6)])
        connection.execute('CREATE TABLE profiles (id INTEGER PRIMARY KEY, proxy_id INTEGER)')
        connection.executemany('INSERT INTO profiles VALUES (?,?)',[(1,1),(2,2),(3,None)])
        if roles:
            connection.execute('CREATE TABLE bm_profiles (profile_id INTEGER PRIMARY KEY)')
            connection.executemany('INSERT INTO bm_profiles VALUES (?)',[(2,),(3,)])
        if scopes:
            connection.execute('CREATE TABLE bm_proxies (proxy_id INTEGER PRIMARY KEY, profile_id INTEGER)')
            connection.executemany('INSERT INTO bm_proxies VALUES (?,?)',[(3,2),(4,None)])


@pytest.mark.parametrize('roles,scopes,expected',[
    (False,False,[1,2,3,4,5]),
    (True,False,[1,3,4,5]),
    (False,True,[1,2,5]),
    (True,True,[1,5]),
])
def test_offline_selector_hides_roles_and_permanent_scopes_without_migration(tmp_path,roles,scopes,expected):
    path=tmp_path/'saved.sqlite3'
    make_database(path,roles,scopes)
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
        before=list(connection.iterdump())
        rows=connection.execute('SELECT id FROM proxies'+ordinary_proxy_where(connection)+' ORDER BY id')
        assert [row[0] for row in rows]==expected
        assert list(connection.iterdump())==before and connection.total_changes==0


def test_empty_scope_table_still_hides_existing_bm_assignment(tmp_path):
    path=tmp_path/'pre-adoption.sqlite3'
    make_database(path,roles=True,scopes=True)
    with sqlite3.connect(path) as connection:
        connection.execute('DELETE FROM bm_proxies')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
        rows=connection.execute('SELECT id FROM proxies'+ordinary_proxy_where(connection)+' ORDER BY id')
        assert [row[0] for row in rows]==[1,3,4,5]


def test_database_with_no_bm_or_profile_tables_keeps_the_ordinary_pool(tmp_path):
    path=tmp_path/'old.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE proxies (id INTEGER PRIMARY KEY)')
        connection.execute('INSERT INTO proxies VALUES (1)')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as connection:
        assert ordinary_proxy_where(connection)==''
        assert connection.execute('SELECT id FROM proxies'+ordinary_proxy_where(connection)).fetchall()==[(1,)]

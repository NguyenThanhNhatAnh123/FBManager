"""Real temporary stores: account separation, throttling and revocation."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import pytest
from via_auth.store import IdentityStore, IdentityError


@pytest.fixture
def users(tmp_path):
    current=[2_000_000_000]
    store=IdentityStore(tmp_path/'auth',clock=lambda:current[0])
    credentials=store.bootstrap()
    return store,credentials,current


def test_three_accounts_have_unique_passwords_and_no_plaintext_in_database(users):
    store,credentials,_=users
    assert [(r['username'],r['role']) for r in store.list_users()]==[('admin','admin'),('operator1','operator'),('operator2','operator')]
    assert len({u['password'] for u in credentials})==3
    with sqlite3.connect(store.path) as db:
        encoded=[r[0] for r in db.execute('SELECT password_hash FROM users')]
    assert all(value.startswith('scrypt-v1$') for value in encoded)
    assert not any(u['password'] in value for u in credentials for value in encoded)
    with pytest.raises(IdentityError,match='users_already_initialized'):
        store.bootstrap()


def test_password_change_revokes_old_sessions_and_logout_only_current_session(users):
    store,credentials,_=users
    old=credentials[1]
    a=store.login(old['username'],old['password'],'127.0.0.1')
    b=store.login(old['username'],old['password'],'127.0.0.1')
    assert a!=b and store.resolve(a)['must_change_password']
    c=store.change_password(store.resolve(a)['id'],old['password'],'A private new password 2026')
    assert store.resolve(a) is None and store.resolve(b) is None
    assert not store.resolve(c)['must_change_password']
    d=store.login(old['username'],'A private new password 2026','127.0.0.1')
    store.logout(c)
    assert store.resolve(c) is None and store.resolve(d)


def test_session_idle_timeout_and_absolute_timeout(users):
    store,credentials,clock=users
    u=credentials[0]
    token=store.login(u['username'],u['password'],'127.0.0.1')
    clock[0]+=1801
    assert store.resolve(token) is None
    token=store.login(u['username'],u['password'],'127.0.0.1')
    for _ in range(12*60):
        clock[0]+=60
        actor=store.resolve(token)
    assert actor is None


def test_parallel_login_admission_is_reserved_before_password_work(users):
    store,_,_=users
    # Exercise only atomic admission: concurrent attempts cannot all pass an
    # initially empty bucket while the expensive verifier is awaiting work.
    def reserve(_):
        try:
            store._reserve_login('operator1','127.0.0.1')
            return True
        except IdentityError as error:
            assert error.status==429
            return False
    with ThreadPoolExecutor(max_workers=16) as pool:
        results=list(pool.map(reserve,range(24)))
    assert sum(results)==10


def test_admin_disable_and_stale_user_edits_are_atomic(users):
    store,credentials,_=users
    token=store.login(credentials[1]['username'],credentials[1]['password'],'127.0.0.1')
    user=store.list_users()[1]
    store.update_user(1,user['id'],{'revision':user['revision'],'active':False})
    assert store.resolve(token) is None
    with pytest.raises(IdentityError,match='user_changed'):
        store.update_user(1,user['id'],{'revision':user['revision'],'active':True})
    admin=store.list_users()[0]
    with pytest.raises(IdentityError,match='last_admin_required'):
        store.update_user(1,admin['id'],{'revision':admin['revision'],'active':False})

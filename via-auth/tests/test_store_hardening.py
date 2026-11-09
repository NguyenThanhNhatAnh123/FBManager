"""All credentials, ACL changes and filesystem links are inside pytest temp roots."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from via_auth import store as module
from via_auth.store import IdentityError, IdentityStore, protect_private


@pytest.fixture
def identity(tmp_path):
    store=IdentityStore(tmp_path/'identity')
    users=store.bootstrap()
    return store,users


@pytest.mark.parametrize('name',['identity.sqlite3','identity.sqlite3-wal','identity.sqlite3-shm','identity.sqlite3-journal'])
def test_database_and_sidecar_hardlinks_are_rejected_before_sqlite_open(tmp_path,monkeypatch,name):
    directory=tmp_path/'identity';directory.mkdir()
    outside=tmp_path/'unrelated-file';outside.write_bytes(b'untouched-fixture')
    os.link(outside,directory/name)
    def forbidden(*args,**kwargs):pytest.fail('Unsafe SQLite path must not be opened')
    monkeypatch.setattr(module.sqlite3,'connect',forbidden)
    monkeypatch.setattr(module,'protect_private',lambda *_:pytest.fail('Reject links before any parent ACL propagation'))
    with pytest.raises(IdentityError) as error:IdentityStore(directory).prepare()
    assert error.value.code=='unsafe_identity_path'
    assert outside.read_bytes()==b'untouched-fixture'


def test_existing_service_key_hardlink_cannot_be_read_or_protected_as_a_private_key(tmp_path):
    store=IdentityStore(tmp_path/'identity');store.prepare()
    unrelated=tmp_path/'unrelated-key';unrelated.write_text('fixture-not-a-service-key',encoding='ascii')
    os.link(unrelated,store.service_path)
    with pytest.raises(IdentityError):store.ensure_service_key()
    with pytest.raises(IdentityError):store.service_actor('fixture-not-a-service-key')
    assert unrelated.read_text(encoding='ascii')=='fixture-not-a-service-key'


def test_unsafe_preexisting_service_key_blocks_parent_acl_propagation(tmp_path,monkeypatch):
    directory=tmp_path/'identity';directory.mkdir()
    unrelated=tmp_path/'unrelated-key';unrelated.write_text('untouched-fixture',encoding='ascii')
    os.link(unrelated,directory/'local-service.key')
    monkeypatch.setattr(module,'protect_private',lambda *_:pytest.fail('Reject key links before setting a parent ACL'))
    with pytest.raises(IdentityError):IdentityStore(directory).prepare()
    assert not (directory/'identity.sqlite3').exists()


@pytest.mark.parametrize('ancestor',[False,True])
def test_directory_redirect_is_not_resolved_before_the_boundary_check(tmp_path,ancestor):
    target=tmp_path/'target';target.mkdir();link=tmp_path/'redirect'
    try:link.symlink_to(target,target_is_directory=True)
    except OSError:
        if os.name!='nt':raise
        # Junctions do not require Windows symlink privilege. Both ends stay in tmp_path.
        result=subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True)
        if result.returncode:pytest.skip('This account cannot create a temporary directory redirect')
    directory=link/'identity' if ancestor else link
    store=IdentityStore(directory)
    assert store.directory==directory.absolute()
    try:
        with pytest.raises(IdentityError):store.prepare()
        assert not (target/'identity.sqlite3').exists()
        assert not (target/'identity'/'identity.sqlite3').exists()
    finally:
        # Remove only the checked redirect, never recurse into the target.
        if getattr(link,'is_junction',lambda:False)():link.rmdir()
        else:link.unlink()


def test_path_is_rechecked_after_initial_prepare(tmp_path):
    store=IdentityStore(tmp_path/'identity');store.prepare()
    store.path.unlink()
    unrelated=tmp_path/'unrelated-file';unrelated.write_bytes(b'untouched-fixture')
    os.link(unrelated,store.path)
    with pytest.raises(IdentityError):store.count()
    assert unrelated.read_bytes()==b'untouched-fixture'


def test_acl_failure_blocks_database_creation_and_provisioning(tmp_path,monkeypatch):
    def denied(_):raise IdentityError('private_permissions_failed',500)
    monkeypatch.setattr(module,'protect_private',denied)
    store=IdentityStore(tmp_path/'identity')
    with pytest.raises(IdentityError):store.bootstrap()
    assert not store.path.exists() and not store.service_path.exists()


def _windows_sddl(path):
    import ctypes
    from ctypes import wintypes
    advapi=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    get_security=advapi.GetNamedSecurityInfoW
    get_security.argtypes=[wintypes.LPWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p)]
    get_security.restype=wintypes.DWORD
    convert=advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes=[ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(wintypes.LPWSTR),ctypes.c_void_p]
    convert.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    descriptor=ctypes.c_void_p();text=wintypes.LPWSTR()
    assert get_security(str(path),1,4,None,None,None,None,ctypes.byref(descriptor))==0
    try:
        assert convert(descriptor,1,4,ctypes.byref(text),None)
        try:return text.value
        finally:kernel.LocalFree(text)
    finally:kernel.LocalFree(descriptor)


@pytest.mark.skipif(os.name!='nt',reason='Windows DACL regression')
def test_complete_private_dacl_removes_preexisting_explicit_everyone_grant(tmp_path):
    directory=tmp_path/'identity';directory.mkdir()
    result=subprocess.run(['icacls',str(directory),'/grant','*S-1-1-0:(OI)(CI)RX'],capture_output=True)
    assert result.returncode==0
    assert ';;;WD)' in _windows_sddl(directory) or ';;;S-1-1-0)' in _windows_sddl(directory)
    protect_private(directory)
    secured=_windows_sddl(directory)
    assert secured.startswith('D:P') and ';;;WD)' not in secured and ';;;S-1-1-0)' not in secured
    # New credential files inherit only the private parent; prepare also protects the DB itself.
    store=IdentityStore(directory);store.ensure_service_key()
    for path in (store.path,store.service_path):
        secured=_windows_sddl(path)
        assert secured.startswith('D:P') and ';;;WD)' not in secured and ';;;S-1-1-0)' not in secured


def test_login_password_change_and_admin_reset_share_the_same_resource_cap(identity,monkeypatch):
    store,users=identity
    with store.connect() as db:ids=[row[0] for row in db.execute('SELECT id FROM users ORDER BY id')]
    held=[]
    try:
        for _ in range(4):assert module._PASSWORD_SLOTS.acquire(blocking=False);held.append(True)
        monkeypatch.setattr(module,'password_matches',lambda *_:pytest.fail('No scrypt when the shared cap is full'))
        for operation in (
            lambda:store.login(users[0]['username'],users[0]['password'],'127.0.0.1'),
            lambda:store.change_password(ids[0],users[0]['password'],'FixtureReplacementPassword82'),
            lambda:store.update_user(ids[0],ids[1],{'revision':1,'reset_password':True}),
        ):
            with pytest.raises(IdentityError) as error:operation()
            assert error.value.code=='login_rate_limited' and error.value.status==429
    finally:
        for _ in held:module._PASSWORD_SLOTS.release()


def test_failed_password_change_releases_its_resource_slot(identity):
    store,users=identity
    with pytest.raises(IdentityError):store.change_password(1,users[0]['password'],'short')
    held=[]
    try:
        for _ in range(4):assert module._PASSWORD_SLOTS.acquire(blocking=False);held.append(True)
    finally:
        for _ in held:module._PASSWORD_SLOTS.release()


@pytest.mark.parametrize('flag',['reset_password','revoke_sessions'])
@pytest.mark.parametrize('value',[None,0,1,'true'])
def test_security_update_flags_require_actual_booleans_without_changing_the_user(identity,flag,value):
    store,_=identity;before=store.list_users()
    with pytest.raises(IdentityError) as error:store.update_user(1,2,{'revision':1,flag:value})
    assert error.value.code=='invalid_input' and store.list_users()==before


def test_admin_cannot_reset_their_own_password_and_lose_the_only_live_credential(identity):
    store,users=identity
    session=store.login(users[0]['username'],users[0]['password'],'127.0.0.1')
    before=store.list_users()
    with pytest.raises(IdentityError) as error:store.update_user(1,1,{'revision':1,'reset_password':True})
    assert error.value.code=='self_reset_not_allowed' and store.list_users()==before
    assert store.resolve(session) is not None

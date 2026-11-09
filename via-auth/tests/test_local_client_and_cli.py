import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import pytest
from via_auth import IdentityStore
from via_auth.client import local_opener


def test_host_client_credentials_never_follow_redirects_or_other_origins(tmp_path):
    store=IdentityStore(tmp_path/'auth')
    store.ensure_service_key()
    opener=local_opener(directory=store.directory)
    credential=next(handler for handler in opener.handlers if handler.__class__.__name__=='Credential')
    request=credential.http_request(urllib.request.Request('http://127.0.0.1:8000/api/session'))
    assert request.get_header('X-via-service-token')==store.service_path.read_text()
    assert 'X-via-service-token' not in request.headers
    assert 'X-via-service-token' in request.unredirected_hdrs
    for url in ('https://outside.invalid','http://127.0.0.1:9000/api/session','http://:secret@127.0.0.1:8000/api/session'):
        with pytest.raises(ValueError):credential.http_request(urllib.request.Request(url))
    redirect=next(handler for handler in opener.handlers if handler.__class__.__name__=='NoRedirect')
    assert redirect.redirect_request(request,None,302,'Found',{},'https://outside.invalid') is None


def test_local_recovery_can_reissue_lost_initial_password_without_logging_it(tmp_path):
    script=Path(__file__).resolve().parents[1]/'manage_users.py'
    data=tmp_path/'identity'
    def command(*args):
        return subprocess.run([sys.executable,'-I','-B',str(script),*args,'--data-dir',str(data)],
                              capture_output=True,text=True,encoding='utf-8',check=False)
    first=command('bootstrap')
    assert first.returncode==0 and json.loads(first.stdout)['created']==3
    original=(data/'initial-users.txt').read_text(encoding='utf-8')
    old_password=original.split('Mật khẩu tạm: ',1)[1].splitlines()[0]
    store=IdentityStore(data)
    old_session=store.login('admin',old_password,'127.0.0.1')
    # Simulate a provisioning output that was lost; keep the initialized DB.
    (data/'initial-users.txt').unlink()
    store.service_path.unlink()
    recovered=command('recover','--username','admin')
    assert recovered.returncode==0
    metadata=json.loads(recovered.stdout)
    assert metadata['password_change_required'] is True and store.service_path.is_file()
    new=Path(metadata['credential_file']).read_text(encoding='utf-8')
    password=new.split('Mật khẩu tạm: ',1)[1].splitlines()[0]
    assert password not in recovered.stdout+recovered.stderr and old_password not in first.stdout+first.stderr
    assert store.resolve(old_session) is None
    token=store.login('admin',password,'127.0.0.1')
    assert store.resolve(token)['must_change_password'] is True and store.count()==3

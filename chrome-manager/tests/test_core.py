import os
import pytest
from pydantic import ValidationError
from app.core.proxy_parser import parse_proxy, parse_import
from app.core.schemas import Settings, TaskInput, Action, ProfileInput, origin
from app.core.security import encrypt_credentials, decrypt_credentials

@pytest.mark.parametrize('text,expected', [
    ('127.0.0.1', ('http', '127.0.0.1', 80, '', '')),
    ('1.2.3.4:8080:user', ('http','1.2.3.4',8080,'user','')),
    ('1.2.3.4:8080:user:pass', ('http','1.2.3.4',8080,'user','pass')),
    ('https://user:p%40ss@proxy.test:443', ('https','proxy.test',443,'user','p@ss')),
    ('socks5://user@proxy.test', ('socks5','proxy.test',1080,'user','')),
    ('socks5://[::1]:1080', ('socks5','::1',1080,'','')),
])
def test_proxy_formats(text, expected):
    proxy = parse_proxy(text)
    assert (proxy.protocol,proxy.host,proxy.port,proxy.username,proxy.password) == expected
    roundtrip = parse_proxy(proxy.url())
    assert roundtrip == proxy

@pytest.mark.parametrize('value', [
    '', 'ftp://host:80','http://host:0','host:65536','host:-1','http://host:80/path',
    'http://host?x=1', 'host name:80', 'http://-invalid:80', 'http://host..test:80',
    'http://:pass@host:80', 'http://host:wrong', 'http://user:%0A@host:80',
])
def test_proxy_validation(value):
    with pytest.raises(ValueError):
        parse_proxy(value)

def test_csv_and_partial_import_never_echo_secrets():
    content = 'host,port,username,password,protocol\nproxy.test,8080,bob,private-secret,http\nbad,99999,other,another-secret,http'
    values, errors = parse_import(content)
    assert len(values) == 1
    assert values[0][1].password == 'private-secret'
    assert errors[0]['line'] == 3
    assert 'secret' not in str(errors)
    values, _ = parse_import('proxy\nhttp://one.test:80\nsocks5://two.test:1080')
    assert len(values) == 2

def test_duplicate_identity_does_not_depend_on_password():
    assert parse_proxy('host:80:user:old').identity == parse_proxy('host:80:user:new').identity
    assert parse_proxy('host:80:user:old').identity != parse_proxy('host:80:other:old').identity

@pytest.mark.skipif(os.name != 'nt', reason='Windows DPAPI')
def test_dpapi_roundtrip_and_ciphertext():
    encrypted = encrypt_credentials('account-user', 'very-private-password')
    assert 'account-user' not in encrypted and 'very-private-password' not in encrypted
    assert decrypt_credentials(encrypted) == ('account-user','very-private-password')
    assert encrypt_credentials('', '') is None
    assert decrypt_credentials(None) == ('','')

@pytest.mark.parametrize('value', ['javascript:alert(1)','file:///C:/secret','https://user:pass@host','data:text/html,test'])
def test_urls_disallow_credentials_and_non_web_schemes(value):
    with pytest.raises(ValueError):
        origin(value)

def test_origin_normalization():
    assert origin('https://EXAMPLE.com:443/path') == 'https://example.com'
    assert origin('http://[::1]:8765/path') == 'http://[::1]:8765'

def test_limits_and_no_arbitrary_code():
    assert Settings().max_browsers == 5 and Settings().require_proxy
    for data in ({'max_browsers':101},{'action_delay_ms':0},{'max_memory_percent':100}):
        with pytest.raises(ValidationError):
            Settings(**data)
    with pytest.raises(ValidationError):
        Action(type='evaluate', value='process.exit()')
    with pytest.raises(ValidationError):
        TaskInput(name='No consent',authorized=False,actions=[{'type':'open_url','url':'https://example.com'}])
    with pytest.raises(ValidationError):
        TaskInput(name='No URL',authorized=True,actions=[{'type':'read_title'}])
    with pytest.raises(ValidationError):
        ProfileInput(name='\x00invalid')


def test_bulk_txt_windows_newlines_and_bom():
    rows = [f'192.0.2.10:{10000+i}:account_{i:03d}:test_password_{i:03d}' for i in range(100)]
    values, errors = parse_import('\ufeff' + '\r\n'.join(rows) + '\r\n')
    assert errors == [] and len(values) == 100
    assert [value.port for _, value in values] == list(range(10000,10100))
    assert all(value.protocol == 'http' for _, value in values)
    assert values[0][1].username == 'account_000'
    assert values[-1][1].password == 'test_password_099'

def test_bulk_txt_rejects_more_than_1000_entries():
    with pytest.raises(ValueError, match='1000'):
        parse_import('\n'.join(f'192.0.2.10:{10000+i}' for i in range(1001)))


def test_import_selects_transport_without_overriding_explicit_scheme():
    rows,errors=parse_import('192.0.2.10:1080:account:password\nhttp://other.test:8080',default_protocol='socks5')
    assert errors==[]
    assert rows[0][1].protocol=='socks5' and rows[0][1].username=='account'
    assert rows[1][1].protocol=='http'
    rows,errors=parse_import('host,port,username,password\nproxy.test,1080,account,password',default_protocol='socks5')
    assert errors==[] and rows[0][1].protocol=='socks5'

"""Regression for opening a saved Outlook session in the ordinary Chrome profile."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.api import routes
from app.core.config import Config
from app.database.session import Database
from app.models.entities import Profile,OutlookLogin
from app.services.manual_browser import ManualBrowser
from app.services.outlook import OUTLOOK_URL


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in a fixture test')


def test_open_after_success_uses_saved_profile_and_exact_outlook_url(tmp_path,monkeypatch):
    config=Config(root=tmp_path,data_dir=tmp_path/'data');config.prepare()
    database=Database(config.data_dir/'manager.sqlite3')
    directory=config.profiles_dir/'Chrome_003'
    with database.session.begin() as session:
        session.add(Profile(id=3,name='Chrome_003',profile_path=str(directory)))
        session.flush()
        session.add(OutlookLogin(profile_id=3,email='fixture.user@hotmail.com',status='signed_in',message='Fixture verified'))
    manual=ManualBrowser(SimpleNamespace(authenticator_deployment={},channel=''),
                         SimpleNamespace(chromium=SimpleNamespace(executable_path='fixture-chrome.exe')),
                         directory,None,[],lambda _:None)
    calls=[]
    mode={'value':'automation'}
    order=[]
    async def process(*arguments,**kwargs):
        calls.append((arguments,kwargs))
        class Forwarder:
            async def wait(self):return 0
        return Forwarder()
    async def wait_for(operation,timeout):
        assert timeout==15
        return await operation
    async def ensure_open(profile_id):
        assert profile_id==3
        assert mode['value'] is None
        order.append('open')
        return manual
    async def saved(profile_id):
        assert profile_id==3
        order.append('save')
        mode['value']=None
    monkeypatch.setattr('app.services.manual_browser.asyncio.create_subprocess_exec',process)
    monkeypatch.setattr('app.services.manual_browser.asyncio.wait_for',wait_for)
    service=SimpleNamespace(db=database,tasks=SimpleNamespace(profile_jobs={}),
                            browsers=SimpleNamespace(extension_profiles=set(),mode=lambda _:mode['value'],ensure_open=ensure_open),
                            outlook=SimpleNamespace(wait_for_saved_session=saved))
    request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(services=service)))
    try:
        assert immediate(routes.open_outlook(3,request))=={'ok':True}
        assert len(calls)==1
        assert order==['save','open']
        arguments,options=calls[0]
        assert arguments==('fixture-chrome.exe',f'--user-data-dir={directory}','--new-tab',OUTLOOK_URL)
        assert not any('remote-debugging' in argument or 'password' in argument for argument in arguments)
        with database.session() as session:
            saved=session.get(OutlookLogin,3)
            assert saved.status=='signed_in' and saved.email=='fixture.user@hotmail.com'
    finally:
        database.engine.dispose()


@pytest.mark.parametrize('url',[
    'http://outlook.live.com/mail/',
    'https://outlook.live.com.evil.example/mail/',
    'https://user:secret@outlook.live.com/mail/',
    'https://outlook.live.com/mail/?redirect=https://reader.example/',
])
def test_outlook_destination_remains_an_exact_allowlist(url):
    manual=ManualBrowser(SimpleNamespace(authenticator_deployment={},channel=''),
                         SimpleNamespace(chromium=SimpleNamespace(executable_path='fixture-chrome.exe')),
                         Path('fixture-profile'),None,[],lambda _:None)
    with pytest.raises(ValueError,match='Unsupported'):
        immediate(manual.open_url(url))

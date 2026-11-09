from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services.manual_browser import launch_arguments, chrome_executable, ManualBrowser
from app.core.first_open import schedule_first_open,pending_first_open,complete_first_open,STORE_URL,REQUEST_FILE


def test_manual_command_keeps_storage_and_proxy_without_automation_protocol():
    directory=Path('C:/Manager/profiles/Chrome_001')
    args=launch_arguments(directory,{'server':'http://127.0.0.1:41000'},['--proxy-bypass-list=<-loopback>'])
    assert f'--user-data-dir={directory}' in args
    assert '--proxy-server=http://127.0.0.1:41000' in args
    assert args[-1]=='about:blank'
    assert not any(token in argument for argument in args for token in ('remote-debugging','enable-automation','AutomationControlled','user-agent','password'))


def test_manual_command_rejects_proxy_credentials_in_process_arguments():
    with pytest.raises(ValueError,match='credentials'):
        launch_arguments(Path('profile'),{'server':'http://private-user:private-secret@proxy:8000'},[])


def test_private_runtime_path_is_shared_with_existing_profile_configuration():
    browser=SimpleNamespace(chromium=SimpleNamespace(executable_path='C:/Manager/.browsers/current/chrome.exe'))
    config=SimpleNamespace(authenticator_deployment={'version':'8.0.1'},channel='')
    assert chrome_executable(config,browser)==Path(browser.chromium.executable_path)


def test_manual_navigation_rejects_arbitrary_scripts_before_starting_process():
    config=SimpleNamespace(authenticator_deployment={},channel='')
    browser=SimpleNamespace(chromium=SimpleNamespace(executable_path='chrome.exe'))
    manual=ManualBrowser(config,browser,Path('profile'),None,[],lambda _:None)
    operation=manual.open_url('javascript:alert(1)')
    try:
        with pytest.raises(ValueError,match='Unsupported'):
            operation.send(None)
    finally:
        operation.close()


def test_requested_store_page_is_consumed_once_without_editing_chrome_preferences(tmp_path):
    directory=tmp_path/'profiles'/'Chrome_006'
    schedule_first_open(directory,tmp_path/'profiles',6)
    assert pending_first_open(directory)==STORE_URL
    complete_first_open(directory)
    assert pending_first_open(directory) is None
    assert sorted(path.name for path in directory.iterdir())==[REQUEST_FILE]


def test_first_open_rejects_outside_profile_root_and_arbitrary_destinations(tmp_path):
    with pytest.raises(ValueError):schedule_first_open(tmp_path/'outside',tmp_path/'profiles',6)
    directory=tmp_path/'profiles'/'Chrome_006';directory.mkdir(parents=True)
    (directory/REQUEST_FILE).write_text('{"version":1,"url":"javascript:alert(1)","opened_at":null}')
    assert pending_first_open(directory) is None


@pytest.mark.parametrize('open_start_page,failing',[(True,False),(False,False),(True,True)])
def test_manual_start_opens_requested_store_only_on_normal_open(tmp_path,monkeypatch,open_start_page,failing):
    directory=tmp_path/'profiles'/'Chrome_006'
    schedule_first_open(directory,tmp_path/'profiles',6)
    executable=tmp_path/'chrome.exe';executable.touch()
    config=SimpleNamespace(authenticator_deployment={},channel='')
    browser=SimpleNamespace(chromium=SimpleNamespace(executable_path=str(executable)))
    opened=[]
    async def process(*_,**__):return SimpleNamespace(returncode=None)
    async def sleep(_):pass
    def task(coroutine):coroutine.close();return None
    async def open_url(self,url):
        opened.append(url)
        if failing:raise ValueError('Could not forward URL')
    monkeypatch.setattr('app.services.manual_browser.asyncio.create_subprocess_exec',process)
    monkeypatch.setattr('app.services.manual_browser.asyncio.sleep',sleep)
    monkeypatch.setattr('app.services.manual_browser.asyncio.create_task',task)
    monkeypatch.setattr(ManualBrowser,'open_url',open_url)
    def execute():
        operation=ManualBrowser(config,browser,directory,None,[],lambda _:None).start(open_start_page)
        try:operation.send(None)
        except StopIteration:pass
        finally:operation.close()
    if failing:
        with pytest.raises(ValueError):execute()
    else:execute()
    assert opened==([STORE_URL] if open_start_page else [])
    assert (pending_first_open(directory) is None)==(open_start_page and not failing)
    if open_start_page and not failing:
        execute()
        assert opened==[STORE_URL,'https://www.facebook.com/']

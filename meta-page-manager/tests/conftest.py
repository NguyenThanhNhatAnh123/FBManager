import base64
from pathlib import Path
import sys
import pytest
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def pytest_configure(config):
    config.addinivalue_line('markers','identity: exercise the real Via identity middleware')


@pytest.fixture(autouse=True)
def business_fixture_identity(request,monkeypatch):
    """Mock only the outer identity in legacy business/local-boundary fixtures.

    Shared identity and mounted integration tests exercise the real middleware
    separately. This hook is never imported by the production application.
    """
    if request.node.get_closest_marker('identity'):
        return
    from page_manager.app import IdentityGate
    async def local_fixture(self,scope,receive,send):
        return await self.app(scope,receive,send)
    monkeypatch.setattr(IdentityGate,'__call__',local_fixture)


def immediate(coroutine):
    try:coroutine.send(None)
    except StopIteration as result:return result.value
    finally:coroutine.close()
    raise AssertionError('Unexpected asynchronous I/O in offline fixture')


class FixtureBox:
    def encrypt(self,value):return 'fixture:'+base64.b64encode(value.encode()).decode()
    def decrypt(self,value):return base64.b64decode(value.removeprefix('fixture:')).decode()


def mark_page_information_updated(store,page_id,connection_id,revision=1):
    """Synthetic accepted edit receipt for positive publishing fixtures only."""
    key=str(uuid4())
    store.edit_begin(connection_id,revision,page_id,key,'metadata','a'*64)
    store.edit_finish(key,'accepted','Synthetic Page information edit accepted')

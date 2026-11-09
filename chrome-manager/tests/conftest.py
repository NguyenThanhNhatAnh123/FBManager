import pytest

@pytest.fixture(autouse=True)
def business_fixture_identity(request,monkeypatch):
    """Existing suites test the inner local-session boundary and business services.

    Real login/roles/revocation tests carry the identity marker and run the outer
    middleware unchanged; no production setting disables authentication.
    """
    if request.node.get_closest_marker('identity'):
        return
    from app.main import IdentityGate
    async def local_fixture(self,scope,receive,send):
        return await self.app(scope,receive,send)
    monkeypatch.setattr(IdentityGate,'__call__',local_fixture)

def pytest_addoption(parser):
    parser.addoption('--run-browser', action='store_true', default=False, help='Run real Chrome integration tests.')

def pytest_collection_modifyitems(config, items):
    if not config.getoption('--run-browser'):
        marker = pytest.mark.skip(reason='Use --run-browser to launch actual Chrome.')
        for item in items:
            if 'browser' in item.keywords:
                item.add_marker(marker)

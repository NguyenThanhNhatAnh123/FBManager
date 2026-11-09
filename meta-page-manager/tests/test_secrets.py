import os
import pytest
from page_manager.security import SecretBox,CONTEXT


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI test')
def test_dpapi_roundtrip_app_context_and_no_plaintext_fallback():
    box=SecretBox();secret='FixtureDpapiSecretToken82';encrypted=box.encrypt(secret)
    assert encrypted.startswith('mpm1:') and secret not in encrypted and box.decrypt(encrypted)==secret
    assert b'Meta Page Manager' in CONTEXT
    with pytest.raises(ValueError):box.decrypt(secret)


def test_missing_dpapi_does_not_fall_back_to_plaintext(monkeypatch):
    import page_manager.security as module
    def unavailable(*_):raise RuntimeError('fixture-private-platform-error')
    monkeypatch.setattr(module,'_protect',unavailable)
    with pytest.raises(RuntimeError):SecretBox().encrypt('FixtureDpapiSecretToken82')
    with pytest.raises(ValueError) as error:SecretBox().decrypt('mpm1:Zml4dHVyZQ==')
    assert 'fixture-private' not in str(error.value)

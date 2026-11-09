"""Bundle scope/integrity tests use generated fixture profiles, never live OTP data."""
import json
import sqlite3
from pathlib import Path
import pytest
from app.core.extension_bundle import EXTENSION_ID, prepare_from_installed, load_deployment, manifest_identity
from app.core.config import Config
from app.core.profile_backup import backup_before_chromium, backup_io_path

PUBLIC_MANIFEST_KEY = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAjo5++7m6mlJGqKOnlYehr9tjIqahMZBJUG7PLa7dSRk6bDUu2pVodO1TQWviHlrDTLP+zfoVbDBS8v8cjloK5Tn90nzC6a957dPzOfyC1WUNYNDlGM0BCmZKVP/MWB3d0ffOmTwaxh0L47aLH5nTW0AUmuwCWCBEEl4Acuyp7rwLNGlazBpaom1Qb5ckn29gCJVVVIZ6wudmcrG/FPTNJXQbg8N6wObGrgGOaxmowbkzJmIfKTyHlYOKLAjZ7aJi0W6jsy47/aV+ojvn4gO+ka6BcRhUeWgoQxqEky119f3OWiVP46SJVbAi0pkknThUjDvX11lATGjB5EvJZGyotwIDAQAB'

@pytest.fixture
def package(tmp_path):
    profile=tmp_path/'profiles'/'Chrome_001'
    source=profile/'Default'/'Extensions'/EXTENSION_ID/'8.0.1_0'
    source.mkdir(parents=True)
    (source/'manifest.json').write_text(json.dumps({'key':PUBLIC_MANIFEST_KEY,'version':'8.0.1','manifest_version':3}))
    (source/'background.js').write_text('// fixture software only')
    private=profile/'Default'/'Local Extension Settings'/EXTENSION_ID
    private.mkdir(parents=True)
    (private/'private-test-data').write_text('fixture-private-account-state')
    data=tmp_path/'data';data.mkdir()
    with sqlite3.connect(data/'manager.sqlite3') as db:
        db.execute('CREATE TABLE profiles(id INTEGER PRIMARY KEY,profile_path TEXT)')
        db.execute('INSERT INTO profiles VALUES(1,?)',(str(profile),))
    return tmp_path,data,source


def test_bundle_contains_software_only_and_keeps_stable_extension_id(package):
    root,data,source=package
    plan=prepare_from_installed(root,data)
    assert manifest_identity(json.loads((source/'manifest.json').read_text()))==EXTENSION_ID
    assert plan['enabled'] is False and plan['scope']=='manager_profiles_only'
    bundle=root/plan['bundle']
    assert set(plan['files'])=={'manifest.json','background.js'}
    assert not (bundle/'private-test-data').exists()
    assert all('fixture-private-account-state' not in p.read_text() for p in bundle.rglob('*') if p.is_file())
    assert not (data/'authenticator-auto.json').exists()


def test_activation_is_validated_and_cannot_silently_accept_changed_software(package):
    root,data,_=package
    plan=prepare_from_installed(root,data)
    plan['enabled']=True
    (data/'authenticator-auto.json').write_text(json.dumps(plan))
    assert load_deployment(root,data)['version']=='8.0.1'
    (root/plan['bundle']/'background.js').write_text('changed software')
    with pytest.raises(ValueError,match='verification failed'):
        load_deployment(root,data)


def test_bundle_scope_rejects_path_escape(package):
    root,data,_=package
    plan=prepare_from_installed(root,data)
    plan.update(enabled=True,bundle='../outside')
    (data/'authenticator-auto.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError,match='Unsafe'):
        load_deployment(root,data)


def test_config_switches_only_manager_process_to_private_chromium(package,monkeypatch):
    root,data,_=package
    plan=prepare_from_installed(root,data)
    plan['enabled']=True
    (data/'authenticator-auto.json').write_text(json.dumps(plan))
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH','previous-test-value')
    config=Config(root=root,data_dir=data,channel='chrome')
    assert config.channel==''
    assert config.authenticator_deployment['extension_id']==EXTENSION_ID
    import os
    assert os.environ['PLAYWRIGHT_BROWSERS_PATH']==str(root/'.browsers')


def test_wrong_extension_identity_is_rejected(package):
    root,data,source=package
    manifest=json.loads((source/'manifest.json').read_text())
    manifest['key']='YW5vdGhlci1leHRlbnNpb24='
    (source/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='No official Authenticator'):
        prepare_from_installed(root,data)


def test_engine_change_backup_keeps_profile_data_separate_and_database_readable(package):
    root,data,_=package
    relative=backup_before_chromium(root,data)
    backup=root/relative
    assert backup.is_relative_to(root/'.runtime'/'backups')
    original=root/'profiles'/'Chrome_001'/'Default'/'Local Extension Settings'/EXTENSION_ID/'private-test-data'
    copied=backup/'profiles'/'1'/'Default'/'Local Extension Settings'/EXTENSION_ID/'private-test-data'
    assert original.read_text()==Path(backup_io_path(copied)).read_text()=='fixture-private-account-state'
    with sqlite3.connect(backup/'manager.sqlite3') as db:
        assert db.execute('SELECT COUNT(*) FROM profiles').fetchone()[0]==1


def test_store_generated_metadata_is_excluded_but_source_and_code_integrity_remain(package):
    root,data,source=package
    metadata=source/'_metadata'
    metadata.mkdir()
    (metadata/'computed_hashes.json').write_text('{"fixture":"computed"}')
    (metadata/'verified_contents.json').write_text('{"fixture":"verified"}')
    plan=prepare_from_installed(root,data)
    assert len(plan['excluded_store_metadata'])==2
    assert '_metadata/computed_hashes.json' not in plan['files']
    assert '_metadata/verified_contents.json' not in plan['files']
    assert (metadata/'computed_hashes.json').exists()
    bundle=root/plan['bundle']
    assert not (bundle/'_metadata'/'computed_hashes.json').exists()
    plan['enabled']=True
    (data/'authenticator-auto.json').write_text(json.dumps(plan))
    assert load_deployment(root,data)['enabled'] is True
    (bundle/'background.js').write_text('changed fixture software')
    with pytest.raises(ValueError,match='verification failed'):
        load_deployment(root,data)


def test_previous_bundle_metadata_can_be_migrated_without_replacing_code(package):
    root,data,source=package
    metadata=source/'_metadata';metadata.mkdir()
    (metadata/'computed_hashes.json').write_text('{"fixture":"computed"}')
    plan=prepare_from_installed(root,data)
    bundle=root/plan['bundle']
    old_metadata=bundle/'_metadata';old_metadata.mkdir()
    (old_metadata/'computed_hashes.json').write_text('{"fixture":"computed"}')
    updated=prepare_from_installed(root,data)
    assert updated['files']==plan['files']
    assert not (old_metadata/'computed_hashes.json').exists()
    assert (metadata/'computed_hashes.json').exists()


def test_command_line_install_metadata_survives_browser_close_without_embedded_manifest(package):
    from app.services.extensions import inspect_authenticator
    root,data,_=package
    plan=prepare_from_installed(root,data)
    profile=root/'profiles'/'Chrome_003'
    (profile/'Default').mkdir(parents=True)
    bundle=root/plan['bundle']
    row={'location':8,'path':str(bundle),'disable_reasons':[]}
    (profile/'Default'/'Secure Preferences').write_text(json.dumps({'extensions':{'settings':{EXTENSION_ID:row}}}))
    result=inspect_authenticator(profile,root/'profiles',bundle_directory=bundle)
    assert result['status']=='Installed' and result['versions']==['8.0.1'] and not result['runtime_verified']
    untouched=root/'profiles'/'Chrome_004';untouched.mkdir()
    assert inspect_authenticator(untouched,root/'profiles',bundle_directory=bundle)['status']=='Not installed'

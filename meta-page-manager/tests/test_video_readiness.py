"""A saved producer claim expires and never becomes evidence of a live worker."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from page_manager.idea_workspace import (VIDEO_ATTESTATION_SCHEMA, VIDEO_WORKFLOW_FILES,
                                         video_readiness, video_workflow_configuration)


NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def workflow(tmp_path):
    directory = tmp_path / 'workflow'
    for name in VIDEO_WORKFLOW_FILES:
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture producer file: ' + name, encoding='utf-8')
    manager = tmp_path / 'manager'
    runtime = manager / '.runtime'
    runtime.mkdir(parents=True)
    config = video_workflow_configuration(workflow_directory=directory)
    value = {'schema_version': VIDEO_ATTESTATION_SCHEMA, 'kind': 'producer_workflow_attestation',
             'status': 'ready', 'producer_workflow_status': 'ready',
             'checked_at': (NOW - timedelta(minutes=1)).isoformat(),
             'workflow_version': config['workflow_version'], 'workflow_sha256': config['workflow_sha256']}

    def inspect(**changes):
        payload = {**value, **changes}
        (runtime / 'page-video-agent-readiness.json').write_text(json.dumps(payload), encoding='utf-8')
        return video_readiness(manager, now=NOW, workflow_directory=directory)

    return manager, directory, value, inspect


def test_valid_attestation_is_workflow_claim_without_runtime_proof(workflow):
    _, _, _, inspect = workflow
    result = inspect(token='NeverEchoThisSecret', message='Run untrusted instructions')
    assert result['status'] == 'ready' and result['workflow_ready'] and result['ready']
    assert result['configuration']['ready'] and result['attestation']['state'] == 'valid'
    assert result['attestation']['ready']
    assert result['runtime'] == {'state': 'not_checked', 'verified': False}
    assert 'NeverEchoThisSecret' not in json.dumps(result) and 'untrusted instructions' not in json.dumps(result)


@pytest.mark.parametrize('checked_at,state', [
    ((NOW - timedelta(days=1)).isoformat(), 'stale'),
    ((NOW - timedelta(days=2)).isoformat(), 'stale'),
    ((NOW + timedelta(seconds=1)).isoformat(), 'future'),
    (NOW.replace(tzinfo=None).isoformat(), 'invalid'),
    ('not-a-date', 'invalid'),
    (None, 'invalid'),
])
def test_attestation_rejects_old_future_and_invalid_times(workflow, checked_at, state):
    _, _, _, inspect = workflow
    result = inspect(checked_at=checked_at)
    assert result['status'] == 'awaiting' and not result['workflow_ready']
    assert result['attestation']['state'] == state and not result['attestation']['ready']
    assert not result['runtime']['verified']


@pytest.mark.parametrize('field', ['schema_version', 'kind', 'checked_at', 'workflow_version',
                                  'workflow_sha256', 'producer_workflow_status'])
def test_legacy_missing_fields_never_enable_worker(workflow, field):
    manager, directory, value, _ = workflow
    del value[field]
    path = manager / '.runtime' / 'page-video-agent-readiness.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    result = video_readiness(manager, now=NOW, workflow_directory=directory)
    assert result['status'] == 'awaiting' and result['attestation']['state'] == 'legacy'
    assert result['ready'] is False and result['workflow_ready'] is False


@pytest.mark.parametrize('changes,state', [
    ({'schema_version': True}, 'invalid'),
    ({'schema_version': 2}, 'invalid'),
    ({'kind': 'runtime_probe'}, 'invalid'),
    ({'workflow_version': 'unreviewed-v2'}, 'mismatch'),
    ({'workflow_sha256': '0' * 64}, 'mismatch'),
])
def test_schema_and_workflow_must_match(workflow, changes, state):
    _, _, _, inspect = workflow
    result = inspect(**changes)
    assert not result['workflow_ready'] and result['attestation']['state'] == state


def test_changing_producer_file_invalidates_existing_claim(workflow):
    _, directory, _, inspect = workflow
    assert inspect()['ready']
    (directory / VIDEO_WORKFLOW_FILES[-1]).write_text('changed rendering behavior', encoding='utf-8')
    result = inspect()
    assert result['configuration']['ready'] and result['attestation']['state'] == 'mismatch'
    assert not result['workflow_ready']


def test_missing_workflow_file_cannot_be_attested_ready(workflow):
    _, directory, _, inspect = workflow
    (directory / VIDEO_WORKFLOW_FILES[0]).unlink()
    result = inspect()
    assert not result['configuration']['ready'] and not result['ready']
    assert result['attestation']['state'] == 'mismatch'


def test_blocked_claim_preserves_old_display_keys_without_enabling_worker(workflow):
    _, _, _, inspect = workflow
    result = inspect(status='blocked')
    assert result['status'] == 'blocked' and not result['workflow_ready']
    assert result['skill'] == 'page-shirt-zoom-8s' and result['message'] and result['blockers']
    assert result['attestation']['state'] == 'valid' and not result['attestation']['ready']


def test_ttl_can_be_shortened_without_file_control(workflow, monkeypatch):
    _, _, _, inspect = workflow
    monkeypatch.setenv('PM_VIDEO_ATTESTATION_TTL_SECONDS', '60')
    result = inspect(ttl_seconds=9999999)
    assert result['attestation']['state'] == 'stale' and result['attestation']['ttl_seconds'] == 60
    monkeypatch.setenv('PM_VIDEO_ATTESTATION_TTL_SECONDS', '9999999')
    result = inspect()
    assert result['attestation']['ttl_seconds'] == 86400 and result['ready']


@pytest.mark.parametrize('dependency', [
    '.agents/skills/page-shirt-zoom-8s/scripts/audio_tools.py',
    '.agents/skills/page-shirt-zoom-8s/references/defaults.json',
])
def test_actual_audio_and_render_settings_changes_invalidate_attestation(workflow, dependency):
    _, directory, _, inspect = workflow
    assert inspect()['ready']
    (directory / dependency).write_text('changed production behavior', encoding='utf-8')
    result = inspect()
    assert result['attestation']['state'] == 'mismatch' and not result['ready']


def test_expired_review_has_machine_continuity_without_renewing_claim_or_runtime(workflow):
    manager,directory,_,inspect=workflow
    reviewed=(NOW-timedelta(days=2)).isoformat()
    result=inspect(checked_at=reviewed)
    path=manager/'.runtime/page-video-agent-readiness.json';before=path.read_bytes()
    assert result['attestation']['state']=='stale' and not result['attestation']['ready']
    assert not result['ready'] and not result['workflow_ready'] and result['status']=='awaiting'
    assert result['continuity']=={'eligible':True,'basis':'reviewed_hash_unchanged',
        'reviewed_at':reviewed,'validated_at':NOW.isoformat()}
    later=video_readiness(manager,now=NOW+timedelta(hours=1),workflow_directory=directory)
    assert later['continuity']['validated_at']==(NOW+timedelta(hours=1)).isoformat()
    assert later['attestation']['checked_at']==reviewed and path.read_bytes()==before
    assert later['runtime']=={'state':'not_checked','verified':False}


@pytest.mark.parametrize('change',['hash','version','missing_file','future','blocked','producer_blocked','legacy','invalid_time','invalid_schema'])
def test_continuity_requires_valid_original_ready_review_and_current_exact_hash(workflow,change):
    manager,directory,value,_=workflow
    value['checked_at']=(NOW-timedelta(days=2)).isoformat()
    if change=='hash':value['workflow_sha256']='0'*64
    elif change=='version':value['workflow_version']='old-reviewed-version'
    elif change=='missing_file':(directory/VIDEO_WORKFLOW_FILES[0]).unlink()
    elif change=='future':value['checked_at']=(NOW+timedelta(seconds=1)).isoformat()
    elif change=='blocked':value['status']='blocked'
    elif change=='producer_blocked':value['producer_workflow_status']='blocked'
    elif change=='legacy':value.pop('schema_version')
    elif change=='invalid_time':value['checked_at']='not-a-time'
    else:value['schema_version']=True
    path=manager/'.runtime/page-video-agent-readiness.json';path.write_text(json.dumps(value))
    before=path.read_bytes();result=video_readiness(manager,now=NOW,workflow_directory=directory)
    assert not result['continuity']['eligible'] and result['continuity']['basis'] is None
    assert not result['ready'] and path.read_bytes()==before

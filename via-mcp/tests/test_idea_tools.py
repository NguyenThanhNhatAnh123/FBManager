"""Narrow idea MCP tools: fake HTTP, local temporary journals, no live agents."""
from copy import deepcopy
import json
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from test_bridge_security import CSRF, make_client
from test_page_tools import CAT, OP, FakeClient, call
from via_mcp.common import ToolError
from via_mcp.journal import Journal
from via_mcp.page_tools import IDEA_DIRECTORY, IDEA_OUTPUT_DIRECTORY, LEGACY_IDEA_DIRECTORY, LEGACY_IDEA_OUTPUT_DIRECTORY
from via_mcp.registry import Registry


IDEA = '79b5cfb8-4f7c-4896-a385-5edb3566e28c'
VARIANT = '029f363b-27c5-498c-96ae-5f8be545db34'
SOURCE = {'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/zitat25', 'verified': False}


def status():
    return {'pipeline': {'settings': {'enabled': False, 'upload_ready': False, 'video_ready': False,
        'campaign_source': deepcopy(SOURCE), 'black_campaign_source': dict(SOURCE, campaign_url='https://blueox179.com/campaign/ge2610'), 'revision': 2},
        'directory': IDEA_DIRECTORY, 'output_directory': IDEA_OUTPUT_DIRECTORY, 'shop': 'blueox179.com', 'ready': False,
        'idea_count': 1, 'ideas': [{'id': IDEA, 'artwork_sha256': 'a' * 64, 'filename': 'one/design.png',
            'campaign_state': 'prepared', 'campaign': None, 'variants': [{'id': VARIANT, 'idea_id': IDEA,
            'ordinal': 1, 'status': 'planned', 'video_sha256': None, 'near_duplicate': False,
            'verification': 'metadata_only', 'reservation_id': OP, 'provenance': {'path': 'PRIVATE'}}]}],
        'jobs': [{'worker_id': 'PRIVATE', 'lease_until': 'PRIVATE'}]},
        'upload': {'installed': True, 'available': False, 'configured_blueox': True, 'active_domain': 'other-shop.test',
                   'authenticated': None, 'source': 'zitat25', 'ready': False, 'blockers': ['SECRET PRIVATE'], 'token': 'SECRET'},
        'video_agent': {'status': 'awaiting', 'skill': None, 'thread_id': 'PRIVATE', 'message': 'SECRET', 'blockers': ['PRIVATE']},
        'policy': {'source_requested': SOURCE['campaign_url'], 'black_source_requested': 'https://blueox179.com/campaign/ge2610',
            'trigger': 'manual', 'caption_mode': 'product_link_only',
            'production_enabled': False, 'ranking_mode': 'preview_only', 'metrics_collection': 'explicit_refresh',
            'daily_time': '06:00', 'timezone': 'America/Los_Angeles', 'variants_per_idea': 7, 'max_reels_per_page_per_day': 1},
        'ranking': {'mode': 'preview_only', 'target_age_hours': 24, 'age_window_hours': [22, 26],
            'method': 'median_views_per_idea_same_metric_near_24h', 'cohorts': [],
            'unmeasured': [{'id': IDEA, 'filename': 'one/design.png', 'token': 'SECRET'}],
            'proposed_allocation': {'proven': .7, 'exploration': .3, 'active': False}, 'raw_response': 'SECRET'},
        'last_scan': {'registered': 1, 'waiting': 0, 'skipped': 0, 'warning': 'C:\\PRIVATE\\secret.png'},
        'scan_error': 'SECRET PRIVATE'}


def run_status(**extra):
    return {'id': 'ab9531c1-6531-45b5-9716-fb6916920fc7', 'request_id': OP, 'idea_id': IDEA,
            'filename': 'one/design.png', 'status': 'running', 'stage': 'producer', 'message': 'SECRET PRIVATE C:\\credentials',
            'created_at': '2030-11-01T12:00:00+00:00', 'started_at': '2030-11-01T12:00:01+00:00',
            'finished_at': None, 'can_cancel': True, 'trigger_mode': 'manual_web_run_idea',
            'run_directory': 'PRIVATE', 'payload': {'token': 'SECRET'}, **extra}


def performance_item(**extra):
    return {'idea_id': IDEA, 'variant_id': VARIANT, 'page_id': '123', 'video_id': '456',
            'metric': 'views', 'value': 0, 'metric_verified': True, 'availability': 'available',
            'observed_at': '2030-11-01T12:00:00+00:00', 'published_time_provenance': 'job_completion',
            'token': 'SECRET', 'path': 'PRIVATE', **extra}


def rolling_progress():
    return {'mode':'rolling_v1','variant_count':2,'product':'passed','items':[
        {'ordinal':1,'image':'passed','video':'passed','qa':'running'},
        {'ordinal':2,'image':'passed','video':'running','qa':'pending'}]}


def test_worker_rolling_progress_is_bounded_and_never_grants_server_qa():
    raw=status();raw['runs']={'items':[run_status(variant_count=2,pipeline=rolling_progress())]}
    result,_=call('pages_idea_status',responses=[raw])
    run=result['runs']['items'][0]
    assert run['pipeline']==rolling_progress()
    assert run['status']=='running' and run['stage']=='producer'
    assert result['pipeline']['ideas'][0]['variants'][0]['verification']=='metadata_only'
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('change',['unknown_state','private_field','wrong_count','wrong_ordinal','boolean_count','boolean_ordinal'])
def test_malformed_rolling_progress_is_not_forwarded(change):
    raw=status();progress=rolling_progress()
    if change=='unknown_state':progress['items'][0]['qa']='published'
    elif change=='private_field':progress['items'][0]['path']='PRIVATE'
    elif change=='wrong_count':progress['variant_count']=3
    elif change=='wrong_ordinal':progress['items'][0]['ordinal']=2
    elif change=='boolean_count':progress['variant_count']=True
    else:progress['items'][0]['ordinal']=True
    raw['runs']={'items':[run_status(variant_count=2,pipeline=progress)]}
    with pytest.raises(ToolError):call('pages_idea_status',responses=[raw])


def test_orchestration_config_projection_cannot_leak_stage_prompts_or_paths():
    raw=status()
    policy={'mode':'rolling_v1','ready':True,'image_workers':1,'render_workers':1,'qa_workers':1,
        'queue_capacity':2,'max_model_workers':2,'product_scope':'once_per_idea_before_images',
        'handoff_policy':'complete_allocated_set','stages':[{'prompt':'SECRET','path':'PRIVATE'}]}
    raw['execution']={'mode':'manual','supported':False,'blockers':[], 'orchestration':policy}
    result,_=call('pages_idea_status',responses=[raw])
    projected=result['execution']['orchestration']
    assert projected['mode']=='rolling_v1' and projected['max_model_workers']==2
    assert 'stages' not in projected
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


def performance(items=None):
    rows = [performance_item()] if items is None else items
    known = sum(row['availability'] == 'available' for row in rows)
    return {'items': rows, 'known': known, 'total': len(rows), 'unavailable': len(rows) - known,
            'automatic_interval_seconds': 3600, 'running': False,
            'last_refresh_at': '2030-11-01T12:00:00+00:00', 'limitations': ['SECRET PRIVATE'],
            'raw_error': {'cookie': 'SECRET'}, 'worker_path': 'PRIVATE'}


def test_three_narrow_tools_only_accept_uuid_or_empty_input():
    assert len(CAT) == 27
    names = ('pages_idea_status', 'pages_idea_scan', 'pages_idea_views_refresh')
    assert CAT[names[0]].read_only
    for name in names:
        descriptor = CAT[name].descriptor()
        assert not descriptor['annotations']['destructiveHint']
        assert descriptor['inputSchema']['additionalProperties'] is False
        assert set(descriptor['inputSchema']['properties']) <= {'operation_id'}
    assert not CAT[names[1]].open_world and CAT[names[2]].open_world
    for name in names[1:]:
        assert not CAT[name].read_only
        assert CAT[name].descriptor()['inputSchema']['required'] == ['operation_id']
        for extra in ({'directory': IDEA_DIRECTORY}, {'enabled': True}, {'token': 'SECRET'},
                      {'claim': True}, {'path': IDEA_DIRECTORY}, {'worker_id': 'agent'}, {'metric_verified': True}):
            with pytest.raises(ValidationError): CAT[name].model.model_validate({'operation_id': OP, **extra})


def test_status_projects_only_public_metadata_and_safe_readiness():
    raw = status(); raw.update(token='SECRET', raw_bridge_response='PRIVATE')
    result, calls = call('pages_idea_status', responses=[raw])
    assert calls == [('pages', 'GET', '/api/idea-pipeline', {'read_only': True})]
    assert result['pipeline']['settings']['black_campaign_source']['campaign_url'].endswith('/ge2610')
    assert result['pipeline']['ideas'][0]['variants'][0]['verification'] == 'metadata_only'
    assert result['workspace_binding']=='project'
    assert result['upload']['active_shop'] == 'other'
    assert 'session_unverified' in result['upload']['blocker_codes']
    assert result['last_scan']['warning_present'] and result['scan_error_present']
    wire = json.dumps(result)
    assert all(value not in wire for value in ('SECRET', 'PRIVATE', 'worker_id', 'lease_until', 'thread_id', 'provenance', 'evidence', 'other-shop.test', 'raw_bridge_response'))


def readiness_agent():
    return {'status': 'ready', 'ready': True, 'workflow_ready': True, 'skill': 'page-shirt-zoom-8s',
            'configuration': {'ready': True, 'workflow_version': 'page-shirt-zoom-8s-v1',
                'workflow_sha256': 'a' * 64, 'reason': None, 'path': 'PRIVATE', 'token': 'SECRET'},
            'attestation': {'state': 'valid', 'ready': True, 'checked_at': '2030-11-01T12:00:00+00:00',
                'expires_at': '2030-11-01T13:00:00+00:00', 'ttl_seconds': 3600,
                'worker_id': 'PRIVATE', 'evidence': {'token': 'SECRET'}},
            'runtime': {'state': 'not_checked', 'verified': False, 'command': 'PRIVATE', 'stderr': 'SECRET'},
            'thread_id': 'PRIVATE', 'message': 'SECRET'}


def continuity_agent():
    agent=readiness_agent()
    agent.update(status='awaiting',ready=False,workflow_ready=False)
    agent['configuration']['workflow_version']='page-shirt-zoom-8s-v3'
    agent['attestation'].update(state='stale',ready=False)
    agent['continuity']={'eligible':True,'basis':'reviewed_hash_unchanged',
        'reviewed_at':'2030-11-01T12:00:00+00:00','validated_at':'2030-11-03T12:00:00+00:00','private':'SECRET'}
    return agent


def test_stale_review_with_matching_machine_hash_continuity_remains_visible_without_runtime_upgrade():
    raw=status();raw['video_agent']=continuity_agent()
    result,_=call('pages_idea_status',responses=[raw]);agent=result['video_agent']
    assert agent['status']=='awaiting' and not agent['ready'] and not agent['workflow_ready']
    assert agent['attestation']['state']=='stale' and not agent['attestation']['ready']
    assert agent['runtime']=={'state':'not_checked','verified':False}
    assert agent['continuity']=={key:raw['video_agent']['continuity'][key]
        for key in ('eligible','basis','reviewed_at','validated_at')}
    assert 'SECRET' not in json.dumps(result)


def test_ineligible_continuity_is_explicit_but_legacy_absence_stays_compatible():
    raw=status();raw['video_agent']=readiness_agent()
    raw['video_agent']['continuity']={'eligible':False,'basis':None,'reviewed_at':None,'validated_at':None}
    result,_=call('pages_idea_status',responses=[raw])
    assert result['video_agent']['continuity']==raw['video_agent']['continuity']
    raw['video_agent'].pop('continuity')
    assert 'continuity' not in call('pages_idea_status',responses=[raw])[0]['video_agent']


def test_fresh_review_also_projects_machine_continuity_without_live_worker_claim():
    raw=status();agent=readiness_agent();raw['video_agent']=agent
    agent['continuity']={'eligible':True,'basis':'reviewed_hash_unchanged',
        'reviewed_at':agent['attestation']['checked_at'],'validated_at':'2030-11-01T12:30:00+00:00'}
    projected=call('pages_idea_status',responses=[raw])[0]['video_agent']
    assert projected['ready'] and projected['attestation']['ready']
    assert projected['continuity']==agent['continuity'] and not projected['runtime']['verified']


@pytest.mark.parametrize('change',['bool','basis','naive','reverse','different_review','missing_config','false_with_proof','wrong_claim'])
def test_continuity_projection_rejects_malformed_or_unbound_claims(change):
    raw=status();agent=continuity_agent();raw['video_agent']=agent;value=agent['continuity']
    if change=='bool':value['eligible']=1
    if change=='basis':value['basis']='SECRET'
    if change=='naive':value['validated_at']='2030-11-03T12:00:00'
    if change=='reverse':value['validated_at']='2030-10-31T12:00:00+00:00'
    if change=='different_review':value['reviewed_at']='2030-11-01T11:00:00+00:00'
    if change=='missing_config':agent.pop('configuration')
    if change=='false_with_proof':value['eligible']=False
    if change=='wrong_claim':agent['attestation'].update(state='missing',checked_at=None,expires_at=None)
    with pytest.raises(ToolError) as error:call('pages_idea_status',responses=[raw])
    assert error.value.code=='invalid_response'


@pytest.mark.parametrize('message,code',[
    ('Tool upload đang có input, review hoặc batch chưa xong. Giữ dữ liệu hiện có và xử lý trước khi chạy idea mới.','uploader_workspace_busy'),
    ('Chưa đọc được trạng thái vùng làm việc tool upload; kiểm tra tool trước khi tạo ảnh/video.','uploader_workspace_unknown')])
def test_execution_workspace_blockers_preserve_exact_safe_reason(message,code):
    raw=status();raw['execution']={'mode':'manual','supported':False,'blockers':[message]}
    result,_=call('pages_idea_status',responses=[raw])
    assert result['execution']['blocker_codes']==[code]


def test_attested_workflow_and_configured_cli_remain_distinct_from_unchecked_runtime():
    raw = status(); raw['video_agent'] = readiness_agent()
    raw['execution'] = {'mode': 'manual', 'supported': True, 'blockers': [], 'cli_configured': True,
        'cli_runtime_verified': False, 'cli_configuration_source': 'environment',
        'cli_command': 'PRIVATE', 'env': {'token': 'SECRET'}, 'stderr': 'SECRET'}
    result, calls = call('pages_idea_status', responses=[raw])
    agent = result['video_agent']
    assert agent['skill'] == 'page-shirt-zoom-8s'
    assert agent['status'] == 'ready' and agent['attestation']['ready'] is True
    assert agent['configuration'] == {'ready': True, 'workflow_version': 'page-shirt-zoom-8s-v1',
                                     'workflow_sha256': 'a' * 64, 'reason': None}
    assert agent['attestation'] == {key: raw['video_agent']['attestation'][key]
        for key in ('state', 'ready', 'checked_at', 'expires_at', 'ttl_seconds')}
    assert agent['runtime'] == {'state': 'not_checked', 'verified': False}
    assert result['execution'] == {'mode': 'manual', 'supported': True, 'blocker_codes': [], 'blocker_count': 0,
        'cli_configured': True, 'cli_runtime_verified': False, 'cli_configuration_source': 'environment'}
    assert calls == [('pages', 'GET', '/api/idea-pipeline', {'read_only': True})]
    assert all(word not in json.dumps(result) for word in ('SECRET', 'PRIVATE', 'thread_id', 'worker_id', 'evidence', 'stderr', 'cli_command'))
    assert 'attestation' in CAT['pages_idea_status'].description and 'runtime verification' in CAT['pages_idea_status'].description


def test_older_agent_readiness_does_not_imply_a_live_worker():
    raw = status(); raw['video_agent'].update(status='ready', workflow_ready=True)
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['video_agent']['runtime'] == {'state': 'not_checked', 'verified': False}
    assert 'attestation' not in result['video_agent']


def test_previous_eight_second_workflow_version_remains_readable_during_upgrade():
    raw = status(); raw['video_agent'] = readiness_agent()
    raw['video_agent']['skill'] = 'pod-shirt-pipeline'
    raw['video_agent']['configuration']['workflow_version'] = 'pod-shirt-pipeline-7x8s-v1'
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['video_agent']['skill'] == 'pod-shirt-pipeline'
    assert result['video_agent']['configuration']['workflow_version'] == 'pod-shirt-pipeline-7x8s-v1'
    assert result['video_agent']['runtime']['verified'] is False


def test_missing_attestation_and_unavailable_workflow_preserve_safe_reason_codes():
    raw = status(); agent = readiness_agent(); raw['video_agent'] = agent
    agent.update(status='awaiting', ready=False, workflow_ready=False)
    agent['configuration'].update(ready=False, workflow_sha256=None, reason='workflow_files_unavailable')
    agent['attestation'].update(state='missing', ready=False, checked_at=None, expires_at=None)
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['video_agent']['configuration']['reason'] == 'workflow_files_unavailable'
    assert result['video_agent']['attestation']['state'] == 'missing'
    assert result['video_agent']['runtime']['verified'] is False


@pytest.mark.parametrize('change', ['skill', 'version', 'hash', 'raw_reason', 'config_bool', 'state', 'ready_state',
    'ttl_bool', 'ttl_large', 'timestamp_path', 'timestamp_naive', 'expiry_mismatch', 'missing_timestamp',
    'runtime_state', 'runtime_verified', 'workflow_claim_conflict', 'status_claim_conflict', 'cli_bool', 'cli_source'])
def test_readiness_projection_rejects_malformed_or_overclaimed_values(change):
    raw = status(); agent = readiness_agent(); raw['video_agent'] = agent
    raw['execution'] = {'mode': 'manual', 'supported': True, 'blockers': [],
        'cli_configured': True, 'cli_runtime_verified': False, 'cli_configuration_source': 'default'}
    if change == 'skill': agent['skill'] = 'PRIVATE'
    if change == 'version': agent['configuration']['workflow_version'] = 'PRIVATE'
    if change == 'hash': agent['configuration']['workflow_sha256'] = 'SECRET'
    if change == 'raw_reason': agent['configuration']['reason'] = 'C:\\PRIVATE\\secret'
    if change == 'config_bool': agent['configuration']['ready'] = 1
    if change == 'state': agent['attestation']['state'] = 'PRIVATE'
    if change == 'ready_state': agent['attestation']['state'] = 'stale'
    if change == 'ttl_bool': agent['attestation']['ttl_seconds'] = True
    if change == 'ttl_large': agent['attestation']['ttl_seconds'] = 86401
    if change == 'timestamp_path': agent['attestation']['checked_at'] = 'C:\\PRIVATE'
    if change == 'timestamp_naive': agent['attestation']['checked_at'] = '2030-11-01T12:00:00'
    if change == 'expiry_mismatch': agent['attestation']['expires_at'] = '2030-11-01T14:00:00+00:00'
    if change == 'missing_timestamp': agent['attestation']['checked_at'] = None
    if change == 'runtime_state': agent['runtime']['state'] = 'running'
    if change == 'runtime_verified': agent['runtime']['verified'] = True
    if change == 'workflow_claim_conflict': agent['workflow_ready'] = False
    if change == 'status_claim_conflict': agent['status'] = 'awaiting'
    if change == 'cli_bool': raw['execution']['cli_configured'] = 'true'
    if change == 'cli_source': raw['execution']['cli_configuration_source'] = 'C:\\PRIVATE'
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response'


def test_live_manual_status_preserves_qa_approval_and_link_only_policy_without_run_actions():
    raw = status()
    variant = raw['pipeline']['ideas'][0]['variants'][0]
    variant.update(status='metadata_registered', video_sha256='b' * 64, verification='worker_qa_approved')
    raw.update(execution={'mode': 'manual', 'supported': True, 'blockers': []}, runs={'items': [run_status()]})
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['pipeline']['ideas'][0]['variants'][0]['verification'] == 'worker_qa_approved'
    assert result['policy']['trigger'] == 'manual' and result['policy']['caption_mode'] == 'product_link_only'
    assert result['execution']['supported'] and result['runs']['items'][0]['request_id'] == OP
    assert result['runs']['items'][0]['status'] == 'running'
    assert all(value not in json.dumps(result) for value in ('SECRET', 'PRIVATE', 'run_directory', 'payload', 'message'))
    assert len(CAT) == 27 and not any(name.startswith('pages_idea_run') or name == 'pages_idea_cancel' for name in CAT)


def test_manual_runs_and_execution_are_bounded_and_blocker_prose_is_not_echoed():
    raw = status()
    raw.update(execution={'mode': 'manual', 'supported': False, 'blockers': ['SECRET PRIVATE C:\\credentials',
        'Bộ kiểm tra và nhận video chưa sẵn sàng.']}, runs={'items': [run_status()] * 150})
    result, _ = call('pages_idea_status', responses=[raw])
    assert len(result['runs']['items']) == 100
    assert result['execution']['blocker_codes'] == ['additional_check_required', 'consumer_unavailable']
    assert all(value not in json.dumps(result) for value in ('SECRET', 'PRIVATE', 'credentials', 'run_directory'))


def test_actual_latest_performance_preserves_zero_unknown_and_metric_cohorts():
    raw = status()
    raw['policy']['metrics_collection'] = 'hourly_and_explicit'
    raw['performance'] = performance([performance_item(), performance_item(variant_id=OP,
        metric='blue_reels_play_count', value=None, availability='unavailable', metric_verified=False)])
    result, _ = call('pages_idea_status', responses=[raw])
    latest = result['performance']
    assert latest['known'] == latest['unavailable'] == 1 and latest['total'] == 2
    assert latest['items'][0]['value'] == 0 and latest['items'][1]['value'] is None
    assert latest['items'][0]['published_time_provenance'] == 'job_completion'
    assert {row['metric'] for row in latest['cohorts']} == {'views', 'blue_reels_play_count'}
    assert all(len(row['items']) == 1 for row in latest['cohorts'])
    assert 'total_views' not in latest and latest['automatic_interval_seconds'] == 3600
    assert result['policy']['metrics_collection'] == 'hourly_and_explicit'
    assert all(word not in json.dumps(latest) for word in ('SECRET', 'PRIVATE', 'cookie', 'raw_error', 'worker_path'))
    assert len(CAT) == 27


@pytest.mark.parametrize('empty', [None, []])
def test_performance_optional_none_or_no_observations_is_not_fake_zero(empty):
    raw = status(); raw['performance'] = None if empty is None else performance([])
    result, _ = call('pages_idea_status', responses=[raw])
    if empty is None: assert result['performance'] is None
    else:
        assert result['performance']['items'] == [] and result['performance']['known'] == 0
        assert result['performance']['cohorts'] == []


@pytest.mark.parametrize('change', ['reach', 'impressions', 'string_views', 'bool_views', 'negative_views',
    'unknown_zero', 'unverified_known', 'proxy_hidden', 'unsafe_timestamp', 'wrong_total',
    'duplicate_observation', 'too_many', 'wrong_interval', 'running_string'])
def test_latest_performance_rejects_mixed_or_malformed_counts(change):
    raw = status(); raw['performance'] = performance()
    latest = raw['performance']; item = latest['items'][0]
    if change in ('reach', 'impressions'): item['metric'] = change
    if change == 'string_views': item['value'] = '123'
    if change == 'bool_views': item['value'] = True
    if change == 'negative_views': item['value'] = -1
    if change == 'unknown_zero': item.update(availability='unavailable', metric_verified=False)
    if change == 'unverified_known': item['metric_verified'] = False
    if change == 'proxy_hidden': item['published_time_provenance'] = 'exact_published_time'
    if change == 'unsafe_timestamp': item['observed_at'] = 'C:\\PRIVATE\\log'
    if change == 'wrong_total': latest['total'] = 2
    if change == 'duplicate_observation': latest['items'] *= 2; latest.update(known=2, total=2)
    if change == 'too_many': latest['items'] *= 101; latest.update(known=101, total=101)
    if change == 'wrong_interval': latest['automatic_interval_seconds'] = True
    if change == 'running_string': latest['running'] = 'false'
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('change', ['planned_qa', 'near_duplicate_qa', 'automatic', 'unknown_run_status',
    'raw_date', 'cancel_completed', 'caption_prose'])
def test_manual_status_does_not_promote_metadata_or_accept_unknown_execution(change):
    raw = status(); raw.update(execution={'mode': 'manual', 'supported': True, 'blockers': []}, runs={'items': [run_status()]})
    variant = raw['pipeline']['ideas'][0]['variants'][0]
    if change == 'planned_qa': variant['verification'] = 'worker_qa_approved'
    if change == 'near_duplicate_qa': variant.update(status='metadata_registered', video_sha256='b' * 64,
        verification='worker_qa_approved', near_duplicate=True)
    if change == 'automatic': raw['execution']['mode'] = 'automatic'
    if change == 'unknown_run_status': raw['runs']['items'][0]['status'] = 'published_everywhere'
    if change == 'raw_date': raw['runs']['items'][0]['created_at'] = 'SECRET'
    if change == 'cancel_completed': raw['runs']['items'][0]['status'] = 'completed'
    if change == 'caption_prose': raw['policy']['caption_mode'] = 'prose_and_hashtags'
    with pytest.raises(ToolError): call('pages_idea_status', responses=[raw])


def test_historical_operator_recovered_run_does_not_hide_whole_idea_status():
    raw = status()
    raw['runs'] = {'items': [run_status(status='completed', stage='operator_recovered', can_cancel=False,
        finished_at='2030-11-01T13:00:00+00:00', diagnostics={'code': None, 'exit_code': None, 'duration_ms': None})]}
    result, _ = call('pages_idea_status', responses=[raw])
    run = result['runs']['items'][0]
    assert run['status'] == run['stage'] == 'completed' and run['can_cancel'] is False
    assert run['diagnostics'] == {'code': None, 'exit_code': None, 'duration_ms': None}


@pytest.mark.parametrize('count',[2,14])
def test_allocated_automatic_runs_are_projected_without_private_batch_payloads(count):
    from uuid import uuid4
    raw=status();idea=raw['pipeline']['ideas'][0];prototype=idea['variants'][0]
    idea['variants']=[dict(prototype,id=str(uuid4()),reservation_id=str(uuid4()),ordinal=i+1) for i in range(count)]
    raw['runs']={'items':[run_status(trigger_mode='automatic_idea_batch',variant_count=count,
        automatic_batch={'private':'PRIVATE'},audio_assignments=[{'credential':'SECRET'}])]}
    raw['policy'].update(production_enabled=True,variants_per_idea=None,variant_count_mode='allocated_per_run',
        legacy_default_variant_count=7,variant_count_min=1,variant_count_max=14,trigger='authorized_run')
    result,_=call('pages_idea_status',responses=[raw])
    assert result['runs']['items'][0]['variant_count']==count
    assert result['runs']['items'][0]['trigger_mode']=='automatic_idea_batch'
    assert len(result['pipeline']['ideas'][0]['variants'])==count
    assert result['policy']['variants_per_idea'] is None and result['policy']['production_enabled'] is True
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('source', [None, 'https://blueox179.com/campaign/zitat25'])
def test_verified_product_url_does_not_invent_missing_legacy_campaign_provenance(source):
    raw = status(); idea = raw['pipeline']['ideas'][0]
    idea.update(campaign_state='verified', campaign={'shop': 'blueox179.com',
        'campaign_url': 'https://blueox179.com/campaign/fixture-product', 'campaign_source': source,
        'evidence': {'path': 'PRIVATE', 'token': 'SECRET'}})
    result, _ = call('pages_idea_status', responses=[raw])
    campaign = result['pipeline']['ideas'][0]['campaign']
    assert campaign['campaign_url'] == 'https://blueox179.com/campaign/fixture-product'
    assert campaign['campaign_source'] == source
    assert campaign['campaign_source_status'] == ('unknown' if source is None else 'recorded')
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('field,value', [('campaign_url', 'https://evil.test/campaign/product'),
    ('campaign_source', 'https://evil.test/campaign/source'),
    ('campaign_source', 'https://blueox179.com/campaign/source?token=SECRET')])
def test_legacy_campaign_tolerance_keeps_exact_url_and_domain_guards(field,value):
    raw = status(); idea = raw['pipeline']['ideas'][0]
    campaign = {'shop': 'blueox179.com', 'campaign_url': 'https://blueox179.com/campaign/fixture-product',
                'campaign_source': None, field: value}
    idea.update(campaign_state='verified', campaign=campaign)
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('stage', ['new_legacy_stage', 'SECRET PRIVATE C:\\credentials', ''])
def test_unknown_cached_stage_is_bounded_literal_instead_of_raw_content(stage):
    raw = status(); raw['runs'] = {'items': [run_status(stage=stage)]}
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['runs']['items'][0]['stage'] == 'unknown'
    assert all(word not in json.dumps(result) for word in ('SECRET', 'PRIVATE', 'credentials', 'new_legacy_stage'))


@pytest.mark.parametrize('code', ['worker_running', 'worker_exit_nonzero', 'worker_timeout', 'worker_output_invalid',
    'worker_launch_failed', 'consumer_failed', 'run_validation_failed', 'cancelled_before_launch', 'completed'])
def test_run_diagnostics_keep_safe_codes_and_bounded_numbers_without_raw_errors(code):
    raw = status(); raw['runs'] = {'items': [run_status(diagnostics={'code': code, 'exit_code': 3221225786,
        'duration_ms': 7200000, 'message': 'SECRET', 'stderr': 'PRIVATE', 'command': 'C:\\PRIVATE'})]}
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['runs']['items'][0]['diagnostics'] == {'code': code, 'exit_code': 3221225786, 'duration_ms': 7200000}
    assert all(word not in json.dumps(result) for word in ('SECRET', 'PRIVATE', 'stderr', 'command'))


def test_unknown_diagnostic_code_does_not_echo_private_prose_or_hide_status():
    raw = status(); raw['runs'] = {'items': [run_status(diagnostics={'code': 'SECRET C:\\PRIVATE', 'exit_code': 0, 'duration_ms': 0})]}
    result, _ = call('pages_idea_status', responses=[raw])
    assert result['runs']['items'][0]['diagnostics'] == {'code': 'unknown', 'exit_code': 0, 'duration_ms': 0}
    assert 'SECRET' not in json.dumps(result) and 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('diagnostics', [None, {'code': []}, {'exit_code': True}, {'exit_code': 2**32},
    {'exit_code': -(2**31)-1}, {'duration_ms': -1}, {'duration_ms': True}, {'duration_ms': 2**53}])
def test_run_diagnostics_reject_malformed_types_and_out_of_range_numbers(diagnostics):
    raw = status(); raw['runs'] = {'items': [run_status(diagnostics=diagnostics)]}
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response'


def test_idea_status_limits_items_variants_and_ranking():
    raw = status()
    raw['pipeline']['ideas'] *= 150
    raw['pipeline']['idea_count'] = 150
    raw['ranking']['unmeasured'] *= 150
    raw['ranking']['cohorts'] = [{'metric': 'views', 'items': [{'idea_id': IDEA, 'filename': 'design.png',
        'sample_count': 1, 'median_views_24h': 0, 'median_page_lift': None, 'confidence': 'insufficient',
        'allocation_group': 'exploration', 'secret': 'SECRET'} for _ in range(150)]}]
    result, _ = call('pages_idea_status', responses=[raw])
    assert len(result['pipeline']['ideas']) == len(result['ranking']['unmeasured']) == 100
    item = result['ranking']['cohorts'][0]['items'][0]
    assert len(result['ranking']['cohorts'][0]['items']) == 100 and item['median_views_24h'] == 0
    assert item['median_page_lift'] is None and 'secret' not in item


@pytest.mark.parametrize('field,path', [('directory', 'C:\\PRIVATE'), ('output_directory', 'C:\\OTHER')])
@pytest.mark.parametrize('name,args', [('pages_idea_status', {}), ('pages_idea_scan', {'operation_id': OP})])
def test_fixed_workspace_paths_cannot_be_replaced(field, path, name, args):
    raw = status(); raw['pipeline'][field] = path
    with pytest.raises(ToolError) as caught: call(name, args, responses=[raw])
    assert caught.value.code == 'binding_mismatch'
    assert caught.value.uncertain is False  # Read/preflight failed before a POST.
    assert 'PRIVATE' not in str(caught.value)


@pytest.mark.parametrize('filename', ['C:\\PRIVATE\\image.png', '/private/image.png', '../image.png',
    'folder/../../image.png', '//server/image.png', 'https://example.test/image.png', 'safe.png\x00SECRET'])
def test_filename_is_relative_and_bounded(filename):
    raw = status(); raw['pipeline']['ideas'][0]['filename'] = filename
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response' and filename not in str(error.value)


@pytest.mark.parametrize('url', ['https://evil.test/campaign/zitat25', 'https://user:pass@blueox179.com/campaign/zitat25',
    'https://blueox179.com/campaign/zitat25?token=SECRET', 'https://blueox179.com.evil.test/campaign/zitat25'])
def test_source_link_must_be_exact_blueox_campaign(url):
    raw = status(); raw['pipeline']['settings']['campaign_source']['campaign_url'] = url
    with pytest.raises(ToolError): call('pages_idea_status', responses=[raw])


@pytest.mark.parametrize('change', ['metric', 'nan', 'auto', 'verified_video', 'readiness', 'root'])
def test_invalid_or_overclaimed_provider_states_are_not_echoed(change):
    raw = status()
    if change == 'metric': raw['ranking']['cohorts'] = [{'metric': 'reach', 'items': []}]
    if change == 'nan': raw['pipeline']['idea_count'] = float('nan')
    if change == 'auto': raw['policy']['production_enabled'] = True
    if change == 'verified_video': raw['pipeline']['ideas'][0]['variants'][0]['verification'] = 'fully_verified'
    if change == 'readiness': raw['upload']['ready'] = True
    if change == 'root': raw['pipeline']['shop'] = 'wildy05.com'
    with pytest.raises(ToolError) as error: call('pages_idea_status', responses=[raw])
    assert error.value.code == 'invalid_response'


def test_scan_posts_empty_body_and_uuid_journal_replay_never_resubmits(tmp_path):
    client = FakeClient(status(),status())
    journal = Journal(tmp_path)
    first = Registry(client, None, journal).call('pages_idea_scan', {'operation_id': OP})
    again = Registry(client, None, journal).call('pages_idea_scan', {'operation_id': OP})
    assert first['ok'] and again['replayed']
    assert client.calls == [('pages','GET','/api/idea-pipeline',{'read_only':True}),
        ('pages', 'POST', '/api/idea-pipeline/scan', {'body': {}, 'read_only': False})]


@pytest.mark.parametrize('name', ['pages_idea_scan', 'pages_idea_views_refresh'])
def test_uncertainty_is_journaled_and_not_retried(tmp_path, name):
    responses=([status()] if name=='pages_idea_scan' else [])+[ToolError('Fixture failure', 'manager_unavailable', uncertain=True)]
    client = FakeClient(*responses)
    journal = Journal(tmp_path)
    first = Registry(client, None, journal).call(name, {'operation_id': OP})
    again = Registry(client, None, journal).call(name, {'operation_id': OP})
    assert first['uncertain'] and again['code'] == 'operation_uncertain' and len(client.calls) == (2 if name=='pages_idea_scan' else 1)


def test_view_refresh_keeps_unknown_distinct_from_zero_and_strips_raw_responses(tmp_path):
    raw = {'targets': 2, 'refreshed': 1, 'unavailable': 1, 'raw': {'token': 'SECRET'}, 'limitations': ['PRIVATE']}
    client = FakeClient(raw); registry = Registry(client, None, Journal(tmp_path))
    first = registry.call('pages_idea_views_refresh', {'operation_id': OP})
    again = registry.call('pages_idea_views_refresh', {'operation_id': OP})
    assert first['result']['targets'] == 2 and first['result']['unavailable'] == 1
    assert first['result']['provider_mutations'] is False and first['result']['production_started'] is False
    assert again['replayed'] and len(client.calls) == 1
    assert client.calls[0] == ('pages', 'POST', '/api/idea-pipeline/views/refresh', {'body': {}, 'read_only': False})
    assert all(text not in json.dumps(first) for text in ('SECRET', 'PRIVATE'))


@pytest.mark.parametrize('raw', [{'targets': 2, 'refreshed': 1, 'unavailable': 0},
    {'targets': True, 'refreshed': 1, 'unavailable': 0}, {'targets': 101, 'refreshed': 0, 'unavailable': 101}])
def test_bad_refresh_counts_are_uncertain(raw):
    with pytest.raises(ToolError) as error: call('pages_idea_views_refresh', {'operation_id': OP}, responses=[raw])
    assert error.value.code == 'invalid_response' and error.value.uncertain


@pytest.mark.parametrize('name,route,response', [
    ('pages_idea_scan', '/pages/api/idea-pipeline/scan', status()),
    ('pages_idea_views_refresh', '/pages/api/idea-pipeline/views/refresh', {'targets': 0, 'refreshed': 0, 'unavailable': 0}),
])
def test_real_http_bridge_uses_pages_csrf_and_no_mutation_replay(tmp_path, make_client, name, route, response):
    def handler(request):
        if name=='pages_idea_scan' and request.method=='GET' and request.url.path=='/pages/api/idea-pipeline':
            return httpx.Response(200,json=status())
        assert request.method == 'POST' and request.url.path == route
        assert request.headers['X-MPM-CSRF'] == CSRF and json.loads(request.content) == {}
        return httpx.Response(200, json=response)
    client, seen, _ = make_client(handler)
    registry = Registry(client, None, Journal(tmp_path))
    assert registry.call(name, {'operation_id': OP})['ok']
    assert registry.call(name, {'operation_id': OP})['replayed']
    expected=[('GET','/pages/api/session')]+([('GET','/pages/api/idea-pipeline')] if name=='pages_idea_scan' else [])+[('POST',route)]
    assert [(request.method, request.url.path) for request in seen] == expected


def legacy_status():
    raw=status();raw['pipeline'].update(directory=LEGACY_IDEA_DIRECTORY,output_directory=LEGACY_IDEA_OUTPUT_DIRECTORY)
    return raw


def test_historical_paired_workspace_keeps_completed_and_cancelled_runs_readable():
    raw=legacy_status();raw['runs']={'items':[
        run_status(status='completed',stage='operator_recovered',can_cancel=False,finished_at='2030-11-01T12:01:00+00:00'),
        run_status(id=str(uuid4()),request_id=str(uuid4()),status='cancelled',stage='cancelled',can_cancel=False)]}
    raw['pipeline']['directory']=LEGACY_IDEA_DIRECTORY.lower().replace('\\','/')
    result,calls=call('pages_idea_status',responses=[raw])
    assert result['workspace_binding']=='legacy_read_only'
    assert result['pipeline']['directory']==LEGACY_IDEA_DIRECTORY and result['pipeline']['output_directory']==LEGACY_IDEA_OUTPUT_DIRECTORY
    assert [row['status'] for row in result['runs']['items']]==['completed','cancelled']
    assert len(calls)==1 and calls[0][1]=='GET'
    assert 'PRIVATE' not in json.dumps(result) and 'SECRET' not in json.dumps(result)


@pytest.mark.parametrize('pair',[(IDEA_DIRECTORY,LEGACY_IDEA_OUTPUT_DIRECTORY),(LEGACY_IDEA_DIRECTORY,IDEA_OUTPUT_DIRECTORY)])
@pytest.mark.parametrize('name',['pages_idea_status','pages_idea_scan'])
def test_mixed_project_and_historical_workspace_pair_is_rejected_without_post(pair,name):
    raw=status();raw['pipeline'].update(directory=pair[0],output_directory=pair[1]);client=FakeClient(raw)
    args={'operation_id':OP} if name.endswith('_scan') else {}
    with pytest.raises(ToolError) as caught:CAT[name].handler(type('Context',(),{'client':client})(),CAT[name].model.model_validate(args))
    assert caught.value.code=='binding_mismatch' and not caught.value.uncertain
    assert len(client.calls)==1 and client.calls[0][1]=='GET'


def test_legacy_scan_is_read_preflight_only_and_journal_never_posts_or_replays(tmp_path):
    client=FakeClient(legacy_status());registry=Registry(client,None,Journal(tmp_path/'legacy-scan'))
    first=registry.call('pages_idea_scan',{'operation_id':OP});again=registry.call('pages_idea_scan',{'operation_id':OP})
    assert not first['ok'] and first['code']=='binding_mismatch' and first['uncertain'] is False
    assert again['replayed'] and len(client.calls)==1 and client.calls[0][1]=='GET'


def test_post_response_switch_to_historical_workspace_is_uncertain_and_held(tmp_path):
    client=FakeClient(status(),legacy_status());registry=Registry(client,None,Journal(tmp_path/'switched'))
    first=registry.call('pages_idea_scan',{'operation_id':OP});again=registry.call('pages_idea_scan',{'operation_id':OP})
    assert first['code']=='binding_mismatch' and first['uncertain'] is True
    assert again['code']=='operation_uncertain' and [row[1] for row in client.calls]==['GET','POST']


def test_legacy_http_status_is_readable_but_scan_never_sends_post(tmp_path,make_client):
    def handler(request):
        assert request.method=='GET' and request.url.path=='/pages/api/idea-pipeline'
        return httpx.Response(200,json=legacy_status())
    client,seen,_=make_client(handler);registry=Registry(client,None,Journal(tmp_path/'legacy-http'))
    assert registry.call('pages_idea_status',{})['result']['workspace_binding']=='legacy_read_only'
    scan=registry.call('pages_idea_scan',{'operation_id':OP})
    assert scan['code']=='binding_mismatch' and scan['uncertain'] is False
    assert all(request.method=='GET' for request in seen)

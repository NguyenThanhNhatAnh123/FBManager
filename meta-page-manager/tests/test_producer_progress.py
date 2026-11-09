"""Synthetic progress files only; no workers, media, HTTP or application DB."""
import copy
import json
import os
from types import SimpleNamespace

import pytest

from page_manager import producer_progress as module
from conftest import immediate


RUN = '12345678-1234-4123-8123-123456789abc'


@pytest.fixture
def progress(tmp_path):
    folder = tmp_path / RUN
    folder.mkdir()
    value = {'schema_version': 1, 'run_id': RUN, 'lease_fence': 3, 'variant_count': 2,
             'mode': 'rolling_v1', 'product': 'running', 'items': [
                 {'ordinal': 1, 'image': 'passed', 'video': 'running', 'qa': 'pending'},
                 {'ordinal': 2, 'image': 'running', 'video': 'pending', 'qa': 'pending'}]}
    return folder, value


def write(progress, value=None):
    folder, original = progress
    (folder / 'pipeline-progress.json').write_text(json.dumps(original if value is None else value), encoding='utf-8')


def read(progress, **kwargs):
    return module.pipeline_progress(progress[0], kwargs.get('run_id', RUN), kwargs.get('fence', 3), kwargs.get('count', 2))


def test_closed_snapshot_and_atomic_update_are_advisory(progress):
    write(progress)
    assert read(progress) == {'mode': 'rolling_v1', 'variant_count': 2, 'product': 'running',
                              'items': progress[1]['items']}
    assert not {'run_id', 'lease_fence', 'path', 'ready_for_handoff'} & read(progress).keys()
    changed = copy.deepcopy(progress[1])
    changed['items'][0]['qa'] = 'needs_review'
    temporary = progress[0] / 'progress.partial'
    temporary.write_text(json.dumps(changed), encoding='utf-8')
    temporary.replace(progress[0] / 'pipeline-progress.json')
    assert read(progress)['items'][0]['qa'] == 'needs_review'


def test_manual_review_pending_is_advisory_qa_only_after_real_stages(progress):
    value = progress[1]
    value['items'][0].update(image='passed', video='passed', qa='manual_review_pending')
    write(progress)
    assert read(progress)['items'][0]['qa'] == 'manual_review_pending'
    value['items'][0]['video'] = 'running'; write(progress)
    assert read(progress) is None
    value['items'][0].update(video='passed', image='manual_review_pending'); write(progress)
    assert read(progress) is None


@pytest.mark.parametrize('field,value', [
    ('schema_version', '1'), ('schema_version', True), ('schema_version', 2),
    ('run_id', '12345678-1234-4123-8123-123456789abd'),
    ('lease_fence', 4), ('lease_fence', True), ('lease_fence', 3.0),
    ('variant_count', 1), ('variant_count', 2.0), ('mode', 'batch'),
    ('product', 'verified'), ('product', {'pending': True}),
    ('items', []), ('items', None), ('private_prompt', 'must not leak')])
def test_rejects_changed_identity_schema_and_extra_fields(progress, field, value):
    changed = copy.deepcopy(progress[1]); changed[field] = value
    write(progress, changed)
    assert read(progress) is None


@pytest.mark.parametrize('change', [
    lambda value: value.pop('product'),
    lambda value: value['items'].reverse(),
    lambda value: value['items'][1].update(ordinal=1),
    lambda value: value['items'][0].update(ordinal=True),
    lambda value: value['items'][0].update(image='https://secret.test'),
    lambda value: value['items'][0].update(video=[]),
    lambda value: value['items'][0].update(qa=True),
    lambda value: value['items'][0].update(path='private.png'),
    lambda value: value['items'][0].pop('qa'),
])
def test_rejects_incomplete_or_unordered_variant_sets(progress, change):
    changed = copy.deepcopy(progress[1]); change(changed)
    write(progress, changed)
    assert read(progress) is None


@pytest.mark.parametrize('kwargs', [
    {'run_id': 'not-a-uuid'}, {'run_id': RUN.upper()}, {'run_id': '00000000-0000-0000-0000-000000000000'},
    {'fence': True}, {'fence': 0}, {'count': True}, {'count': 0}, {'count': 15},
])
def test_rejects_invalid_trusted_arguments(progress, kwargs):
    write(progress)
    assert read(progress, **kwargs) is None


@pytest.mark.parametrize('count', [1, 14])
def test_count_boundaries(progress, count):
    value = progress[1]
    value['variant_count'] = count
    value['items'] = [{'ordinal': i, 'image': 'pending', 'video': 'pending', 'qa': 'pending'} for i in range(1, count + 1)]
    write(progress)
    assert len(read(progress, count=count)['items']) == count


@pytest.mark.parametrize('raw', [b'', b'{', b'\xff', b'[]', b'null', b' ' * (module.MAX_BYTES + 1)],
                         ids=['empty', 'partial', 'invalid-utf8', 'array', 'null', 'oversize'])
def test_missing_malformed_or_oversize_file_is_absent(progress, raw):
    assert read(progress) is None
    (progress[0] / 'pipeline-progress.json').write_bytes(raw)
    assert read(progress) is None


def test_duplicate_json_key_does_not_override_identity(progress):
    write(progress)
    path = progress[0] / 'pipeline-progress.json'
    path.write_text(path.read_text().replace('"lease_fence": 3', '"lease_fence": 99, "lease_fence": 3'), encoding='utf-8')
    assert read(progress) is None


def test_hardlink_and_directory_rejected(progress):
    write(progress)
    path = progress[0] / 'pipeline-progress.json'
    alias = progress[0] / 'alias.json'
    os.link(path, alias)
    assert read(progress) is None
    alias.unlink(); path.unlink(); path.mkdir()
    assert read(progress) is None


@pytest.mark.parametrize('target', ['file', 'parent'])
def test_reparse_file_or_parent_rejected(progress, monkeypatch, target):
    write(progress)
    original = module._linked
    blocked = progress[0] if target == 'parent' else progress[0] / 'pipeline-progress.json'
    monkeypatch.setattr(module, '_linked', lambda path, info: path == blocked or original(path, info))
    assert read(progress) is None


def test_file_mutated_during_read_rejected(progress, monkeypatch):
    write(progress)
    original, calls = module.os.fstat, []
    def changed(descriptor):
        info = original(descriptor); calls.append(descriptor)
        if len(calls) != 2:
            return info
        return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                               st_size=info.st_size + 1, st_mtime_ns=info.st_mtime_ns)
    monkeypatch.setattr(module.os, 'fstat', changed)
    assert read(progress) is None


def test_progress_reader_never_creates_files(tmp_path):
    folder = tmp_path / 'absent'
    assert module.pipeline_progress(folder, RUN, 3, 2) is None
    assert list(tmp_path.iterdir()) == []


def test_actual_rolling_writer_snapshot_is_readable(progress):
    from page_manager.producer_stages import StreamingProducer
    producer = StreamingProducer({'run_id': RUN, 'lease_fence': 3, 'variant_count': 2,
        'variants': [{}, {}]}, progress[0], guard=lambda: None,
        run_model=lambda *_: None, run_render=lambda *_: None)
    immediate(producer._progress())
    assert read(progress) == {'mode': 'rolling_v1', 'variant_count': 2,
        'product': 'pending', 'items': [
            {'ordinal': n, 'image': 'pending', 'video': 'pending', 'qa': 'pending'} for n in (1, 2)]}

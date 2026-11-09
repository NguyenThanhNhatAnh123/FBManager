"""Project defaults and exact historical run lookup; no live storage changes."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from page_manager import project_paths as paths
from page_manager.idea_pipeline import IDEA_DIRECTORY,OUTPUT_DIRECTORY
from page_manager.video_source import DEFAULT_DIRECTORY
from page_manager.idea_run_results import IdeaRunResults,_Hold
from page_manager.project_assets import project_assets


def test_defaults_share_one_repository_root_without_creating_storage():
    expected=Path(__file__).resolve().parents[2]
    assert paths.VIA_ROOT==expected and paths.PRODUCER_ROOT==expected/'page-producer'
    assert IDEA_DIRECTORY==expected/'media/ideas' and DEFAULT_DIRECTORY==expected/'media/videos'
    assert paths.AUDIO_DIRECTORY==expected/'media/audio' and OUTPUT_DIRECTORY==paths.PRODUCER_ROOT
    assert paths.FFMPEG_PATH==expected/'tools/ffmpeg/bin/ffmpeg.exe'
    assert paths.BRIDGE_PATH==expected/'page-producer/page-manager-bridge/bridge.py'


def test_only_existing_exact_uuid_folder_can_use_legacy_root(tmp_path):
    primary,legacy=tmp_path/'project/runs',tmp_path/'old-ABC/runs'
    key=str(uuid4());folder=legacy/key;folder.mkdir(parents=True)
    choose=lambda value:paths.select_existing_run_directory(value,primary_runs=primary,legacy_runs=legacy)
    assert choose(key)==folder
    assert choose(str(uuid4())) is None
    assert choose('../'+key) is None and choose('00000000-0000-0000-0000-000000000000') is None
    (primary/key).mkdir(parents=True)
    assert choose(key) is None,'The same UUID in both trusted roots is ambiguous'
    (primary/key).rmdir();(folder/'run-input.json').write_text('{}')
    assert choose(key)==folder


def test_consumer_uses_own_trusted_bridge_for_each_existing_root(tmp_path):
    primary,legacy=tmp_path/'new/runs',tmp_path/'legacy/runs'
    key=str(uuid4());old=legacy/key;old.mkdir(parents=True)
    consumer=object.__new__(IdeaRunResults)
    consumer.runs_directory=primary;consumer.legacy_runs_directory=legacy
    consumer.bridge=object();consumer.legacy_bridge=object()
    run={'id':key,'run_directory':str(old)}
    assert consumer._run_bridge(run,old) is consumer.legacy_bridge
    with pytest.raises(_Hold):consumer._run_bridge(run,tmp_path/'arbitrary'/key)
    new=primary/key;new.mkdir(parents=True)
    with pytest.raises(_Hold):consumer._run_bridge(run,old)
    old.rmdir();run['run_directory']=str(new)
    assert consumer._run_bridge(run,new) is consumer.bridge


def test_project_catalog_does_not_claim_native_or_service_tools_are_vendored():
    value=project_assets()
    assert value['verification']=='configuration_only'
    assert value['project_root']==str(paths.VIA_ROOT)
    rows={row['id']:row for row in value['items']}
    assert len(rows)==len(value['items'])
    for row in value['items']:
        if row['kind']=='local':assert Path(row['path']).is_relative_to(paths.VIA_ROOT)
        else:assert row['path'] is None and row['present'] is None
    assert rows['imagegen']['kind']=='native_codex' and rows['uploader']['kind']=='service'
    assert rows['page-workflow']['path']==str(paths.PRODUCER_ROOT/'.agents/skills/page-shirt-zoom-8s/SKILL.md')

"""Relocation fixtures and runtime selection only; no provider or business work."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import wave

import httpx
import pytest

from page_manager import idea_runs, podsach_bridge, producer_runtime
from page_manager.idea_recovery import trusted_bridge


VIA = Path(__file__).resolve().parents[2]
SKILL = Path('page-producer/.agents/skills/page-shirt-zoom-8s')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def relocated(tmp_path):
    # A different drive-shaped parent and spaces expose accidental cwd/home use.
    root = tmp_path / 'D-drive' / 'Shared Via server'
    for relative in (SKILL / 'scripts/audio_tools.py', SKILL / 'scripts/render_video.py',
                     SKILL / 'references/defaults.json',
                     Path('page-producer/page-manager-bridge/bridge.py'),
                     Path('meta-page-manager/scripts/run_manual_portrait_renderer.py')):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(VIA / relative, target)
    return root


def test_relocated_audio_catalog_and_renderer_defaults_stay_inside_new_project(relocated, monkeypatch):
    audio = load(relocated / SKILL / 'scripts/audio_tools.py', 'relocated_audio')
    assert audio.VIA_ROOT == relocated
    assert audio.DEFAULT_FFBIN == relocated / 'tools/ffmpeg/bin'
    assert audio.SOURCE_DIRECTORY == relocated / 'media/audio'
    assert audio.SOURCE_MANIFEST == relocated / 'media/audio/source-library.json'
    audio.SOURCE_DIRECTORY.mkdir(parents=True)
    tone = audio.SOURCE_DIRECTORY / 'synthetic-tone.wav'
    with wave.open(str(tone), 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        stream.writeframes(b'\0\0' * 800)
    raw = tone.read_bytes()
    audio.SOURCE_MANIFEST.write_text(json.dumps({'schema_version': 1, 'tracks': [
        {'filename': tone.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}))
    assert list(audio.imported_sources()) == [tone.name]
    assert audio.source_audio(tone) == tone
    outside = relocated.parent / tone.name
    outside.write_bytes(raw)
    with pytest.raises(ValueError): audio.source_audio(outside)
    monkeypatch.setitem(sys.modules, 'audio_tools', audio)
    renderer = load(relocated / SKILL / 'scripts/render_video.py', 'relocated_renderer')
    assert renderer.DEFAULT_FFBIN == audio.DEFAULT_FFBIN
    adapter = load(relocated / 'meta-page-manager/scripts/run_manual_portrait_renderer.py', 'relocated_adapter')
    assert adapter.RENDERER == relocated / SKILL / 'scripts/render_video.py'
    assert isinstance(adapter.renderer_tree(adapter.RENDERER.read_bytes()), ast.Module)


def test_relocated_trusted_bridge_keeps_root_and_traversal_guards(relocated):
    path = relocated / 'page-producer/page-manager-bridge/bridge.py'
    bridge = trusted_bridge(path)
    producer = relocated / 'page-producer'
    assert bridge.ABC_ROOT == producer
    assert bridge.build_result.__defaults__ == (producer,)
    files = bridge.Files(producer)
    assert files.path(str(producer / 'run/artifact.png')) == producer / 'run/artifact.png'
    with pytest.raises(bridge.BridgeError) as error: bridge.Files(relocated)
    assert error.value.code == 'ROOT'
    with pytest.raises(bridge.BridgeError) as error: files.path(str(relocated / 'outside.png'))
    assert error.value.code == 'OUTSIDE_ROOT'
    with pytest.raises(bridge.BridgeError) as error: files.path(str(producer / 'run/../outside.png'))
    assert error.value.code == 'PATH'


def test_portable_pins_only_add_the_reviewed_path_changes(relocated):
    adapter = load(relocated / 'meta-page-manager/scripts/run_manual_portrait_renderer.py', 'pinned_adapter')
    raw = adapter.RENDERER.read_bytes()
    historical = raw.replace(b"DEFAULT_FFBIN = Path(__file__).resolve().parents[5] / 'tools/ffmpeg/bin'",
                            b"DEFAULT_FFBIN = Path(r'C:\\Users\\Admin\\Via\\tools\\ffmpeg\\bin')")
    assert historical != raw
    assert hashlib.sha256(historical).hexdigest() == '9811628c2601ea73b32c02ce3ac73b6b9a36869ae228e42273dc6e915b18a8e5'
    adapter.renderer_tree(historical)
    with pytest.raises(ValueError): adapter.renderer_tree(raw + b'\n# unreviewed')
    bridge = (relocated / 'page-producer/page-manager-bridge/bridge.py').read_bytes()
    historical = bridge.replace(b'ABC_ROOT = Path(__file__).resolve().parents[1]',
                                b'ABC_ROOT = Path(r"C:\\Users\\Admin\\Via\\page-producer")')
    assert hashlib.sha256(historical).hexdigest() == '2dacc53474f1da7da93306cdd0626432f399ba8a3e3e702c797c4404bbb55200'


def test_cli_prefers_explicit_then_path_then_current_install(tmp_path, monkeypatch):
    native = tmp_path / 'path-codex.exe'; native.write_bytes(b'inert executable fixture')
    installed = tmp_path / 'installed-codex.exe'; installed.write_bytes(b'inert executable fixture')
    override = tmp_path / 'explicit-codex.exe'; override.write_bytes(b'inert executable fixture')
    monkeypatch.delenv('PM_CODEX_CLI', raising=False)
    monkeypatch.setattr(idea_runs.shutil, 'which', lambda _name: str(native))
    monkeypatch.setattr(idea_runs, 'CLI', installed)
    assert idea_runs._cli_path() == native
    monkeypatch.setenv('PM_CODEX_CLI', str(override))
    assert idea_runs._cli_path() == override
    monkeypatch.setenv('PM_CODEX_CLI', 'codex.exe --shell-text')
    assert idea_runs._cli_path() is None
    monkeypatch.delenv('PM_CODEX_CLI')
    monkeypatch.setattr(idea_runs.shutil, 'which', lambda _name: None)
    assert idea_runs._cli_path() == installed


def test_camera_python_uses_project_runtime_and_invalid_override_blocks(tmp_path, monkeypatch):
    python = tmp_path / 'python.exe'; python.write_bytes(b'inert executable fixture')
    monkeypatch.delenv('PM_CAMERA_PYTHON', raising=False)
    assert producer_runtime.camera_python() == Path(sys.executable)
    assert producer_runtime.camera_python(python) == python
    monkeypatch.setenv('PM_CAMERA_PYTHON', str(python))
    assert producer_runtime.camera_python() == python
    for value in ('python.exe', 'python.exe -c anything', str(tmp_path / 'missing.exe'), ''):
        monkeypatch.setenv('PM_CAMERA_PYTHON', value)
        assert producer_runtime.camera_python() is None


def test_executable_links_and_relative_paths_are_not_accepted(tmp_path):
    original = tmp_path / 'original.exe'; original.write_bytes(b'inert executable fixture')
    linked = tmp_path / 'linked.exe'; linked.hardlink_to(original)
    assert producer_runtime.executable_path(linked) is None
    assert producer_runtime.executable_path(Path('relative.exe')) is None
    assert producer_runtime.executable_path(tmp_path / 'part/../original.exe') is None


def test_current_user_codex_install_uses_runtime_profile_and_architecture(tmp_path, monkeypatch):
    monkeypatch.setenv('APPDATA', str(tmp_path))
    monkeypatch.setattr(producer_runtime.platform, 'machine', lambda: 'ARM64')
    path = producer_runtime.current_user_codex_cli()
    assert path.is_relative_to(tmp_path)
    assert 'codex-win32-arm64' in path.parts and 'aarch64-pc-windows-msvc' in path.parts


def test_uploader_install_override_is_absolute_and_keeps_public_paths_private(tmp_path, monkeypatch):
    for name in ('dashboard.py', 'upload_batch.py', 'config.py'):
        (tmp_path / name).write_text('# inert fixture')
    (tmp_path / 'shops.json').write_text(json.dumps({'active': 'blueox179.com',
        'shops': {'blueox179.com': {'fallback': 'fixture'}}}))
    def unavailable(request): raise httpx.ConnectError('fixture unavailable', request=request)
    monkeypatch.setenv('PM_PODSACH_TOOL_ROOT', str(tmp_path))
    bridge = podsach_bridge.PodsachBridge(transport=httpx.MockTransport(unavailable))
    value = bridge.inspect()
    assert value['installed'] is True and value['configured_blueox'] is True
    assert str(tmp_path) not in json.dumps(value)
    monkeypatch.setenv('PM_PODSACH_TOOL_ROOT', 'relative-uploader')
    value = podsach_bridge.PodsachBridge(transport=httpx.MockTransport(unavailable)).inspect()
    assert value['installed'] is False and value['configured_blueox'] is False


def test_uploader_installed_after_startup_is_detected(tmp_path, monkeypatch):
    root = tmp_path / 'installed-later'
    monkeypatch.setenv('PM_PODSACH_TOOL_ROOT', str(root))
    def unavailable(request): raise httpx.ConnectError('fixture unavailable', request=request)
    bridge = podsach_bridge._LocalPodsachBridge(transport=httpx.MockTransport(unavailable))
    assert bridge.inspect()['installed'] is False
    root.mkdir()
    for name in ('dashboard.py', 'upload_batch.py', 'config.py'):
        (root / name).write_text('# inert fixture')
    assert bridge.inspect()['installed'] is True

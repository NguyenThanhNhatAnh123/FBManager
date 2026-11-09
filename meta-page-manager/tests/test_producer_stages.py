"""Offline orchestration fixtures. Fake MP4 bytes/QA are never production proof."""
import asyncio
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest

from page_manager import producer_stages as module


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def production(tmp_path, monkeypatch):
    bridge = module.load_bridge()
    # Only the synthetic harness changes the bridge's fixed project guard.
    bridge.ABC_ROOT = tmp_path
    monkeypatch.setattr(module, 'load_bridge', lambda: bridge)
    def build(count=3):
        folder = tmp_path / str(uuid4()); folder.mkdir()
        source = folder / 'source.png'; Image.new('RGB', (90, 160), 'white').save(source)
        source_hash = 'a' * 64
        approval = {'approval_id': 'b' * 64, 'pcm_sha256': 'c' * 64,
            'source_sha256': source_hash, 'render_signature': 'd' * 64,
            'sample_rate': 44100, 'channels': 2, 'duration_seconds': 8.0}
        data = {'schema_version': '1.0', 'run_id': folder.name, 'idea_id': str(uuid4()),
            'artwork_sha256': sha(source), 'source_path': str(source), 'lease_fence': 4,
            'source_snapshot': {}, 'variant_count': count, 'execution_pipeline': 'rolling_v1',
            'variants': [{'variant_id': str(uuid4()), 'reservation_id': str(uuid4()), 'ordinal': n}
                         for n in range(1, count + 1)],
            'trigger_mode': 'manual_web_run_idea', 'caption_mode': 'product_link_only',
            'audio_mix_policy': 'human_approved_exact_pcm_v1',
            'audio_assignments': [{'source_file': str(tmp_path / 'inert-source.mp3'), 'source_sha256': source_hash} for _ in range(count)],
            'approved_audio_mixes': {source_hash: approval}}
        (folder / 'run-input.json').write_text(json.dumps(data), encoding='utf-8')
        return folder, data
    return build


class OfflineStages:
    """Injected model/renderer receipts for ordering and contract tests only."""
    def __init__(self, folder, data):
        self.folder, self.data = folder, data
        self.events = []; self.tasks = []; self.models = 0; self.maximum_models = 0
        self.first_qa = asyncio.Event(); self.qa_should_fail = False

    async def model(self, task):
        self.tasks.append(task); self.events.append((task.stage, task.ordinal, 'start'))
        self.models += 1; self.maximum_models = max(self.maximum_models, self.models)
        try:
            await asyncio.sleep(.001)
            if task.stage == 'product':
                return {'status': 'passed', 'reason': 'ready', 'campaign': {
                    'kind': 'existing', 'url': 'https://blueox179.com/campaign/offline-fixture',
                    'source_url': None, 'shirt_color': None, 'evidence': {
                        'observed_at': '2026-01-01T00:00:00Z', 'artwork_matches': True,
                        'quote_matches': True, 'public_url': 'https://blueox179.com/campaign/offline-fixture'}}}
            binding = {k: v['const'] for k, v in task.schema['properties']['binding']['properties'].items()}
            if task.stage == 'image':
                # Prove later images are backpressured until the first QA finishes.
                if task.ordinal == 4 and 'review_policy' not in self.data: assert self.first_qa.is_set()
                output = task.workspace / 'image.png'
                Image.new('RGB', (90, 160), (task.ordinal * 17, 55, 90)).save(output)
                return {'status': 'passed', 'reason': 'ready', 'binding': binding,
                    'image_path': str(output), 'image_sha256': sha(output),
                    'concept_id': f'synthetic-scene-{task.ordinal}',
                    'image_skill': 'realistic-shirt-photos-closeup-9x16',
                    'origin': 'generated_native', 'print_box': [10, 20, 70, 100]}
            if self.qa_should_fail:
                return {'status': 'needs_review', 'reason': 'qa_pending', 'binding': binding,
                    **{k: False for k in ('original_artwork_compared', 'complete_print_visible',
                    'artwork_within_print_box', 'independent_source_photo', 'new_scene_reviewed')}, 'frames_reviewed': []}
            if task.ordinal == 1: self.first_qa.set()
            return {'status': 'passed', 'reason': 'ready', 'binding': binding,
                **{k: True for k in ('original_artwork_compared', 'complete_print_visible',
                'artwork_within_print_box', 'independent_source_photo', 'new_scene_reviewed')},
                'frames_reviewed': list(module.FRAMES)}
        finally:
            self.models -= 1; self.events.append((task.stage, task.ordinal, 'done'))

    async def render(self, image, ordinal):
        self.events.append(('video', ordinal, 'start')); await asyncio.sleep(.001)
        path = self.folder / 'videos' / f'{ordinal:02}.mp4'
        path.write_bytes(f'INERT SYNTHETIC FIXTURE; NOT A VIDEO {ordinal}'.encode())
        review = self.folder / 'review' / f'{ordinal:02}'; review.mkdir()
        frames = []
        for number in module.FRAMES:
            frame = review / f'frame-{number:03}.png'
            Image.new('RGB', (9, 16), (ordinal, number % 255, 1)).save(frame); frames.append(str(frame))
        assignment = self.data['audio_assignments'][ordinal - 1]
        approved = self.data['approved_audio_mixes'][assignment['source_sha256']]
        self.events.append(('video', ordinal, 'done'))
        return {'video_path': str(path), 'video_sha256': sha(path), 'audio_source_sha256': assignment['source_sha256'],
            'technical': {'width': 1080, 'height': 1920, 'fps': 30, 'frames': 240, 'duration_seconds': 8.0,
                'video_codec': 'h264', 'audio_codec': 'aac', 'full_decode_passed': True,
                'all_frames_artwork_visible': True, 'subpixel_zoom_verified': True,
                'minimum_margin_fraction': .04, 'motion_rms_pixels': .01},
            'audio': {'review_method': 'approved_mix_hash_match', 'checks': {'exact_pcm_match': True},
                'binding': {'approval_id': approved['approval_id'], 'actual_pcm_sha256': approved['pcm_sha256'],
                    'approved_pcm_sha256': approved['pcm_sha256'], 'audio_source_sha256': assignment['source_sha256']}},
            'frames': frames}


def execute(folder, data, stages, **kwargs):
    async def run():
        return await module.StreamingProducer(data, folder, guard=lambda: None,
            run_model=stages.model, run_render=stages.render, **kwargs).run()
    return asyncio.run(run())


def manual_production(production, count=2):
    folder, data = production(count)
    data['review_policy'] = 'manual_review_v1'
    (folder / 'run-input.json').write_text(json.dumps(data), encoding='utf-8')
    return folder, data


class OfflineManualStages(OfflineStages):
    async def model(self, task):
        assert task.stage in ('image', 'product'), 'Manual flow must never launch a QA model'
        return await super().model(task)

    async def render(self, image, ordinal):
        value = await super().render(image, ordinal)
        value['technical'].pop('motion_rms_pixels')
        value['technical'].pop('subpixel_zoom_verified')
        value['technical']['motion_registration'] = 'not_run'
        value['frames'] = []
        return value


def test_manual_receipt_commits_while_next_image_is_still_running(production):
    folder, data = manual_production(production)
    async def run():
        stages = OfflineManualStages(folder, data)
        release = asyncio.Event(); next_started = asyncio.Event(); committed = asyncio.Event()
        async def model(task):
            if task.stage == 'image' and task.ordinal == 2:
                next_started.set(); await release.wait()
            return await stages.model(task)
        async def progress(value):
            if value['items'][0]['qa'] == 'manual_review_pending': committed.set()
        task = asyncio.create_task(module.StreamingProducer(data, folder, guard=lambda: None,
            run_model=model, run_render=stages.render, progress=progress).run())
        await asyncio.wait_for(asyncio.gather(next_started.wait(), committed.wait()), 5)
        assert not task.done() and not (folder / 'worker-output.json').exists()
        doc = module.load_bridge().manual_review_item(str(folder / 'handoff/01.json'), root=folder)['receipt']
        assert doc['ordinal'] == 1 and doc['variant_count'] == 2 and doc['ai_qa'] is False
        assert doc['status'] == 'manual_review_pending' and doc['visual_review'] == doc['distinct_review'] == 'pending'
        assert not (folder / 'handoff/02.json').exists()
        release.set(); result = await asyncio.wait_for(task, 5)
        assert result['status'] == 'manual_review_pending' and result['reason'] == 'ready'
        assert result['ai_qa'] is False and len(result['handoff_paths']) == 2
        assert not any(t.stage == 'qa' for t in stages.tasks)
        assert stages.maximum_models == 1
        assert not any((folder / name).exists() for name in ('job.json', 'manifest.json', 'result.json'))
        assert not list((folder / 'qa').iterdir())
    asyncio.run(run())


def test_manual_partial_handoff_survives_later_generation_hold(production):
    folder, data = manual_production(production)
    async def run():
        stages = OfflineManualStages(folder, data); committed = asyncio.Event()
        async def progress(value):
            if value['items'][0]['qa'] == 'manual_review_pending': committed.set()
        async def model(task):
            if task.stage == 'image' and task.ordinal == 2:
                await committed.wait()
                value = await stages.model(task)
                return {**value, 'status': 'needs_review', 'reason': 'source_unverified'}
            return await stages.model(task)
        output = await module.StreamingProducer(data, folder, guard=lambda: None,
            run_model=model, run_render=stages.render, progress=progress).run()
        assert output['status'] == 'needs_review' and output['reason'] == 'source_unverified'
        assert output['handoff_paths'] == [str(folder / 'handoff/01.json')]
        assert module.load_bridge().manual_review_item(output['handoff_paths'][0], root=folder)['receipt']['ai_qa'] is False
    asyncio.run(run())


@pytest.mark.parametrize('bad', ['https://blueox179.com/campaign/offline-fixture', 'https://blueox179.com/campaign/zitat25'])
def test_manual_existing_product_source_url_must_be_null(production, bad):
    folder, data = manual_production(production, 1); stages = OfflineManualStages(folder, data)
    original = stages.model
    async def model(task):
        value = await original(task)
        if task.stage == 'product': value['campaign']['source_url'] = bad
        return value
    stages.model = model
    assert execute(folder, data, stages)['status'] == 'needs_review'
    assert not list((folder / 'images').iterdir())


@pytest.mark.parametrize('template,colour', [('zitat25', 'black'), ('ge2610', None)])
def test_manual_product_uses_exact_frozen_template_enum(production, template, colour):
    folder, data = manual_production(production, 1)
    url = 'https://blueox179.com/campaign/' + template
    data['source_snapshot'] = {'campaign_source': {'campaign_url': url, 'verified': True, 'shop': 'blueox179.com'}}
    (folder / 'run-input.json').write_text(json.dumps(data))
    stages = OfflineManualStages(folder, data); original = stages.model
    async def model(task):
        value = await original(task)
        if task.stage == 'product': value['campaign'].update(kind='new', source_url=url, shirt_color=colour)
        return value
    stages.model = model
    assert execute(folder, data, stages)['status'] == 'manual_review_pending'


def test_model_cannot_prepopulate_coordinator_handoff(production):
    folder, data = manual_production(production, 1); stages = OfflineManualStages(folder, data)
    original = stages.model
    async def model(task):
        value = await original(task)
        if task.stage == 'product': (folder / 'handoff/01.json').write_text('{}')
        return value
    stages.model = model
    result = execute(folder, data, stages)
    assert result['status'] == 'needs_review' and result['handoff_paths'] == []
    assert not list((folder / 'images').iterdir())


@pytest.mark.parametrize('policy', [None, 'skip', True])
def test_unknown_or_null_review_policy_cannot_enter_manual_flow(production, policy):
    folder, data = production(1); data['review_policy'] = policy
    with pytest.raises(module.StageError):
        module.StreamingProducer(data, folder, guard=lambda: None, run_model=None, run_render=None)


def test_manual_portrait_adapter_only_changes_pinned_aspect_guard():
    import ast
    path = Path(module.__file__).resolve().parents[2] / 'scripts/run_manual_portrait_renderer.py'
    spec = importlib.util.spec_from_file_location('manual_portrait_fixture', path)
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    raw = adapter.RENDERER.read_bytes(); tree = adapter.renderer_tree(raw)
    guards = [n.test for n in ast.walk(tree) if isinstance(n, ast.If) and '40000000' in ast.unparse(n.test)]
    assert len(guards) == 1
    code = compile(ast.Expression(guards[0]), '<synthetic geometry>', 'eval')
    def rejected(w, h, frames=1): return eval(code, {'getattr': getattr, 'image': SimpleNamespace(n_frames=frames), 'width': w, 'height': h})
    assert not rejected(941, 1672) and not rejected(1024, 1536)
    assert rejected(1536, 1024) and rejected(1024, 1536, 2) and rejected(0, 1536)
    assert rejected(10000, 11000)
    with pytest.raises(ValueError): adapter.renderer_tree(raw + b'\n# unreviewed change')


def test_actual_synthetic_portrait_composition_keeps_audio_pcm_and_source_bytes(tmp_path, monkeypatch):
    """Real local FFmpeg, synthetic pictures/tone only; no human approval record."""
    import math
    import struct
    import subprocess
    import sys
    import wave
    from PIL import ImageDraw
    ff = module.FFMPEG_DIRECTORY
    if not (ff / 'ffmpeg.exe').is_file(): pytest.skip('Project FFmpeg is not installed')
    adapter_path = Path(module.__file__).resolve().parents[2] / 'scripts/run_manual_portrait_renderer.py'
    spec = importlib.util.spec_from_file_location('manual_portrait_render_fixture', adapter_path)
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    helper_path = adapter.RENDERER.parent / 'audio_tools.py'
    spec = importlib.util.spec_from_file_location('audio_tools', helper_path)
    helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
    # Isolated test-only catalog injection; production source pin is untouched.
    monkeypatch.setattr(helper, 'source_audio', lambda path: Path(path))
    monkeypatch.setitem(sys.modules, 'audio_tools', helper)
    monkeypatch.setattr(sys, 'argv', list(sys.argv)); monkeypatch.setattr(sys, 'path', list(sys.path))
    audio = tmp_path / 'synthetic-tone.wav'
    with wave.open(str(audio), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(44100)
        w.writeframes(b''.join(struct.pack('<h', round(3000 * math.sin(2 * math.pi * 440 * n / 44100))) for n in range(44100 * 8)))
    portrait = tmp_path / 'synthetic-portrait.png'
    picture = Image.new('RGB', (1024, 1536), 'lavender'); ImageDraw.Draw(picture).rectangle((250, 350, 750, 1050), fill='navy'); picture.save(portrait)
    source_sha = sha(portrait); composed = tmp_path / 'portrait.mp4'
    adapter.main(['--image', str(portrait), '--audio', str(audio), '--output', str(composed),
                  '--print-box', '240', '340', '760', '1060'])
    report = json.loads(composed.with_suffix('.verification.json').read_text())
    assert sha(portrait) == source_sha and report['image_source_sha256'] == source_sha
    assert report['size'] == [1080, 1920] and report['frames'] == 240 and report['decode'] == 'passed'
    assert report['all_frames_print_box_inside'] is True and min(report['minimum_print_margin_fractions']) >= .04
    reference = tmp_path / 'reference.m4a'
    subprocess.run([str(ff / 'ffmpeg.exe'), '-v', 'error', '-i', str(audio), '-af', helper.audio_filter(8),
        '-t', '8', '-c:a', 'aac', '-b:a', '192k', str(reference)], check=True, timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert helper.pcm_hash(composed, ff) == helper.pcm_hash(reference, ff)


def test_manual_renderer_skips_heavy_camera_and_qa_frames(production, tmp_path, monkeypatch):
    folder, data = manual_production(production, 1)
    project = tmp_path / 'synthetic-project'; scripts = project / '.agents/skills/page-shirt-zoom-8s/scripts'
    scripts.mkdir(parents=True)
    (scripts / 'audio_tools.py').write_text('def source_audio(path): return path\n')
    audio = Path(data['audio_assignments'][0]['source_file']); audio.write_bytes(b'inert audio fixture')
    audio_hash = sha(audio); old = data['audio_assignments'][0]['source_sha256']
    data['audio_assignments'][0]['source_sha256'] = audio_hash
    data['approved_audio_mixes'][audio_hash] = {**data['approved_audio_mixes'].pop(old), 'source_sha256': audio_hash}
    image = folder / 'image.png'; Image.new('RGB', (1024, 1536), 'red').save(image)
    approval = data['approved_audio_mixes'][audio_hash]
    proof = {'review_method': 'approved_mix_hash_match', 'checks': {'exact_pcm_match': True}, 'binding': {
        'approval_id': approval['approval_id'], 'actual_pcm_sha256': approval['pcm_sha256'],
        'approved_pcm_sha256': approval['pcm_sha256'], 'audio_source_sha256': audio_hash}}
    calls = []
    class Runtime(module.LocalStageRuntime):
        async def command(self, argv, **kwargs):
            calls.append([str(v) for v in argv])
            if any(Path(str(v)).name == 'run_manual_portrait_renderer.py' for v in argv):
                video = folder / 'videos/01.mp4'; video.parent.mkdir(); video.write_bytes(b'inert MP4 fixture')
                video.with_suffix('.verification.json').write_text(json.dumps({'image_source_sha256': sha(image),
                    'audio_source_sha256': audio_hash, 'all_frames_print_box_inside': True, 'decode': 'passed',
                    'minimum_print_margin_fractions': [.04] * 4}))
                return ''
            if Path(str(argv[0])).name == 'ffprobe.exe':
                return json.dumps({'streams': [{'codec_type': 'video', 'width': 1080, 'height': 1920,
                    'r_frame_rate': '30/1', 'nb_frames': '240', 'codec_name': 'h264'},
                    {'codec_type': 'audio', 'codec_name': 'aac'}], 'format': {'duration': '8.0'}})
            assert 'verify-approved' in argv
            return json.dumps(proof)
    value = asyncio.run(Runtime(data, folder, lambda: None, project=project).render({
        'image_path': str(image), 'image_sha256': sha(image), 'print_box': [100, 200, 800, 1000]}, 1))
    assert len(calls) == 3 and value['frames'] == []
    assert value['technical']['motion_registration'] == 'not_run'
    assert 'subpixel_zoom_verified' not in value['technical']


def test_streams_per_artifact_and_real_bridge_validates_complete_set(production):
    folder, data = production(4)
    async def run():
        stages = OfflineStages(folder, data)
        progress = []
        result = await module.StreamingProducer(data, folder, guard=lambda: None,
            run_model=stages.model, run_render=stages.render, progress=progress.append).run()
        assert result['status'] == result['reason'] == 'ready'
        assert stages.maximum_models <= 2
        assert stages.events.index(('qa', 1, 'done')) < stages.events.index(('image', 4, 'start'))
        assert stages.events.index(('video', 1, 'start')) < stages.events.index(('image', 4, 'start'))
        assert len([t for t in stages.tasks if t.stage == 'product']) == 1
        assert all(p['mode'] == 'rolling_v1' and set(p) == {'schema_version', 'mode', 'run_id',
            'lease_fence', 'variant_count', 'product', 'items'} for p in progress)
        assert all(set(item) == {'ordinal', 'image', 'video', 'qa'} for p in progress for item in p['items'])
        assert progress[-1]['items'] == [{'ordinal': n, 'image': 'passed', 'video': 'passed', 'qa': 'passed'} for n in range(1, 5)]
        handoff = json.loads((folder / 'result.json').read_text())
        assert handoff['status'] == 'verified_for_handoff' and len(handoff['variants']) == 4
        assert all(v['ready_to_publish'] is False for v in handoff['variants'])
        assert json.loads((folder / 'qa/01/caption.json').read_text())['review_method'] == 'exact_product_link_check'
        assert stages.tasks[0].working_directory == folder
        assert all(t.working_directory == t.workspace for t in stages.tasks[1:])
    asyncio.run(run())


def test_product_failure_prevents_all_paid_images_and_render(production):
    folder, data = production(2)
    stages = OfflineStages(folder, data)
    async def blocked(task):
        assert task.stage == 'product'
        return {'status': 'needs_review', 'reason': 'campaign_uncertain', 'campaign': None}
    stages.model = blocked
    result = execute(folder, data, stages)
    assert result['status'] == 'needs_review' and result['reason'] == 'campaign_uncertain'
    assert not list((folder / 'images').iterdir()) and not (folder / 'result.json').exists()
    assert json.loads((folder / 'pipeline-progress.json').read_text())['product'] == 'needs_review'


def test_three_artifacts_overlap_image_render_qa_with_two_models(production):
    """Events hold all three stages active; no ordering relies on sleep duration."""
    folder, data = production(4)
    async def run():
        stages = OfflineStages(folder, data)
        release_qa = asyncio.Event()
        started = {key: asyncio.Event() for key in ('image3', 'render2', 'qa1')}
        model_active = set(); render_active = set(); model_peaks = []
        image4_started = asyncio.Event()
        async def model(task):
            identity = (task.stage, task.ordinal)
            model_active.add(identity); model_peaks.append(len(model_active))
            try:
                if identity == ('image', 3):
                    started['image3'].set(); await release_qa.wait()
                if identity == ('qa', 1):
                    started['qa1'].set(); await release_qa.wait()
                if identity == ('image', 4): image4_started.set()
                return await stages.model(task)
            finally: model_active.remove(identity)
        async def render(image, ordinal):
            render_active.add(ordinal)
            try:
                assert len(render_active) == 1
                if ordinal == 2:
                    started['render2'].set(); await release_qa.wait()
                return await stages.render(image, ordinal)
            finally: render_active.remove(ordinal)
        engine = module.StreamingProducer(data, folder, guard=lambda: None,
            run_model=model, run_render=render)
        running = asyncio.create_task(engine.run())
        try:
            await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started.values())), 5)
            assert model_active == {('image', 3), ('qa', 1)} and render_active == {2}
            assert not image4_started.is_set()
            assert max(model_peaks) == 2
        finally:
            release_qa.set()
        assert (await asyncio.wait_for(running, 5))['status'] == 'ready'
        assert image4_started.is_set() and max(model_peaks) == 2
        assert stages.events.index(('qa', 1, 'done')) < stages.events.index(('image', 4, 'start'))
    asyncio.run(run())


def test_failed_qa_keeps_partial_files_without_complete_handoff(production):
    folder, data = production(4); stages = OfflineStages(folder, data); stages.qa_should_fail = True
    result = execute(folder, data, stages)
    assert result['status'] == 'needs_review' and result['reason'] == 'qa_pending'
    assert (folder / 'videos/01.mp4').exists() and not (folder / 'result.json').exists()
    assert not (folder / 'stages/image-04').exists()
    progress = json.loads((folder / 'pipeline-progress.json').read_text())
    assert progress['items'][0]['qa'] == 'needs_review'


@pytest.mark.parametrize('mutation', ['binding', 'unknown', 'bool_fence', 'style', 'duplicate', 'missing_frame', 'false_check', 'changed_video'])
def test_unbound_or_incomplete_stage_receipts_cannot_promote(production, mutation):
    folder, data = production(2); stages = OfflineStages(folder, data); original = stages.model
    async def tampered(task):
        value = await original(task)
        if task.stage == 'image' and task.ordinal == 2:
            if mutation == 'binding': value['binding']['variant_id'] = str(uuid4())
            if mutation == 'unknown': value['secret'] = 'fixture'
            if mutation == 'bool_fence': value['binding']['lease_fence'] = True
            if mutation == 'style': value['image_skill'] = 'ao-cao-cap'
            if mutation == 'duplicate': value['concept_id'] = 'synthetic-scene-1'
        if task.stage == 'qa' and task.ordinal == 1:
            if mutation == 'missing_frame': value['frames_reviewed'] = [0]
            if mutation == 'false_check': value['artwork_within_print_box'] = False
            if mutation == 'changed_video': (folder / 'videos/01.mp4').write_bytes(b'changed synthetic fixture')
        return value
    stages.model = tampered
    assert execute(folder, data, stages)['status'] == 'needs_review'
    assert not (folder / 'result.json').exists()


def test_preflight_seed_reused_once_without_extra_native_generation(production):
    folder, data = production(2)
    seeds = folder / 'seed-images'; seeds.mkdir(); seed = seeds / '01.png'
    Image.new('RGB', (90, 160), 'green').save(seed)
    data['preflight_images'] = [{'image_path': str(seed), 'image_sha256': sha(seed), 'origin': 'actual-native-fixture'}]
    (folder / 'run-input.json').write_text(json.dumps(data))
    stages = OfflineStages(folder, data); original = stages.model
    async def seeded(task):
        value = await original(task)
        if task.stage == 'image' and task.ordinal == 1:
            value.update(image_path=str(seed), image_sha256=sha(seed), origin='preflight_seed')
        return value
    stages.model = seeded
    assert execute(folder, data, stages)['status'] == 'ready'
    assert sha(folder / 'images/01.png') == sha(seed)
    image_tasks = [t for t in stages.tasks if t.stage == 'image']
    assert seed in image_tasks[0].images and seed not in image_tasks[1].images


@pytest.mark.parametrize('missing', ['audio_assignments', 'approved_audio_mixes', 'audio_mix_policy'])
def test_missing_audio_authorization_stops_before_product(production, missing):
    folder, data = production(1); del data[missing]
    (folder / 'run-input.json').write_text(json.dumps(data))
    stages = OfflineStages(folder, data)
    assert execute(folder, data, stages)['reason'] == 'qa_pending'
    assert stages.tasks == []


def test_cannot_restart_over_existing_aggregate_or_progress(production):
    folder, data = production(1); old = folder / 'pipeline-progress.json'; old.write_text('{"historical":true}')
    stages = OfflineStages(folder, data)
    assert execute(folder, data, stages)['status'] == 'needs_review'
    assert old.read_text() == '{"historical":true}' and not (folder / 'worker-output.json').exists()


def test_product_cannot_prepopulate_aggregate(production):
    folder, data = production(1); stages = OfflineStages(folder, data); original = stages.model
    async def injected(task):
        value = await original(task)
        if task.stage == 'product': (folder / 'manifest.json').write_text('{}')
        return value
    stages.model = injected
    assert execute(folder, data, stages)['status'] == 'needs_review'
    assert not (folder / 'result.json').exists()


def test_guard_revocation_cancels_active_stages_and_preserves_partial(production):
    folder, data = production(2)
    async def run():
        stages = OfflineStages(folder, data); original = stages.model; cancelled = []
        async def waiting(task):
            if task.stage == 'image' and task.ordinal == 2:
                try: await asyncio.sleep(30)
                except asyncio.CancelledError: cancelled.append(2); raise
            return await original(task)
        async def denied(image, ordinal):
            await asyncio.sleep(.01)
            raise asyncio.CancelledError()
        engine = module.StreamingProducer(data, folder, guard=lambda: None, run_model=waiting, run_render=denied)
        with pytest.raises(asyncio.CancelledError): await engine.run()
        assert cancelled == [2]
        assert (folder / 'images/01.png').exists() and not (folder / 'result.json').exists()
    asyncio.run(run())


@pytest.mark.parametrize('ordinal', [1, 14])
def test_allocated_count_bounds_and_exact_pairs_use_real_bridge(production, ordinal):
    folder, data = production(ordinal)
    assert execute(folder, data, OfflineStages(folder, data))['status'] == 'ready'
    job = json.loads((folder / 'job.json').read_text())
    assert job['variant_count'] == ordinal
    assert job['variants'] == [{k: v[k] for k in ('variant_id', 'reservation_id')} for v in data['variants']]


def test_concrete_command_timeout_stops_own_child_without_media_or_network(production):
    folder, data = production(1)
    async def run():
        runtime = module.LocalStageRuntime(data, folder, lambda: None)
        with pytest.raises(module.StageError):
            await runtime.command([module.sys.executable, '-c', 'import time; time.sleep(30)'], timeout=.05)
        assert runtime.children == set()
    asyncio.run(run())


def test_concrete_command_periodic_guard_stops_active_child(production):
    folder, data = production(1)
    async def run():
        checks = []
        def guard():
            checks.append(1)
            if len(checks) >= 2: raise module.StageError('blocked')
        runtime = module.LocalStageRuntime(data, folder, guard)
        with pytest.raises(module.StageError, match='blocked'):
            await runtime.command([module.sys.executable, '-c', 'import time; time.sleep(30)'], timeout=5)
        assert len(checks) >= 2 and runtime.children == set()
    asyncio.run(run())


def test_concrete_command_cancel_reaps_owned_child(production):
    folder, data = production(1)
    async def run():
        runtime = module.LocalStageRuntime(data, folder, lambda: None)
        task = asyncio.create_task(runtime.command([module.sys.executable, '-c', 'import time; time.sleep(30)']))
        for _ in range(100):
            if runtime.children: break
            await asyncio.sleep(.01)
        assert runtime.children
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert runtime.children == set()
    asyncio.run(run())


def test_concrete_model_invocation_uses_exact_stage_profile_and_schema(production, monkeypatch):
    from page_manager import idea_runs, provider_profile
    folder, data = production(1)
    workspace = folder / 'qa'; workspace.mkdir()
    task = module.StageTask('qa', 1, workspace, workspace, 'synthetic prompt',
        module._object({'status': module.STATUS}), workspace / 'stage-output.json', (Path(data['source_path']),))
    monkeypatch.setattr(idea_runs, '_cli_path', lambda: Path('C:/synthetic/codex.exe'))
    stages = []
    def profile(project, *, stage): stages.append(stage); return ['-c', 'features.multi_agent=false']
    monkeypatch.setattr(provider_profile, 'producer_profile_args', profile)
    runtime = module.LocalStageRuntime(data, folder, lambda: None)
    async def command(argv, **kwargs):
        assert argv[:2] == ['C:\\synthetic\\codex.exe', 'exec']
        assert argv[argv.index('-C') + 1] == str(workspace)
        assert argv[argv.index('--image') + 1] == data['source_path']
        assert '--output-schema' in argv and '--ephemeral' in argv and argv[-1] == '-'
        assert kwargs['prompt'] == 'synthetic prompt'
        task.output_path.write_text('{"status":"needs_review"}')
        return ''
    runtime.command = command
    assert asyncio.run(runtime.model(task)) == {'status': 'needs_review'}
    assert stages == ['qa']


@pytest.mark.parametrize('failure', ['missing_frame', 'audio_mismatch', 'bad_technical'])
def test_renderer_facts_cannot_forge_bridge_acceptance(production, failure):
    folder, data = production(1); stages = OfflineStages(folder, data); original = stages.render
    async def render(image, ordinal):
        value = await original(image, ordinal)
        if failure == 'missing_frame': value['frames'].pop()
        if failure == 'audio_mismatch': value['audio']['binding']['actual_pcm_sha256'] = 'f' * 64
        if failure == 'bad_technical': value['technical']['motion_rms_pixels'] = .5
        return value
    stages.render = render
    assert execute(folder, data, stages)['status'] == 'needs_review'
    assert not (folder / 'result.json').exists()


@pytest.fixture
def streaming_script():
    path = Path(__file__).resolve().parents[1] / 'scripts/run_streaming_producer.py'
    spec = importlib.util.spec_from_file_location('test_only_streaming_script', path)
    script = importlib.util.module_from_spec(spec); spec.loader.exec_module(script)
    return script


@pytest.mark.parametrize('token', [b'', b'\n', b'wrong\n', b'ROLLING_V1_START',
    b'ROLLING_V1_START\r\n', b'x' * 64 + b'ROLLING_V1_START\n'])
def test_bootstrap_wrong_or_eof_never_reads_authority_or_initializes_runtime(streaming_script, monkeypatch, token):
    script = streaming_script; calls = []
    class BoundedInput(io.BytesIO):
        def readline(self, size=-1):
            assert size == 64
            calls.append('stdin')
            return super().readline(size)
    monkeypatch.setattr(script.sys, 'stdin', SimpleNamespace(buffer=BoundedInput(token)))
    def forbidden(*args, **kwargs):
        calls.append('forbidden'); raise AssertionError('Bootstrap must precede authority/runtime')
    for name in ('ReadOnlyRunStore', 'load_binding', 'LocalStageRuntime', 'StreamingProducer'):
        monkeypatch.setattr(script, name, forbidden)
    assert script.main(['--run-input', 'C:/inert/run-input.json']) == 2
    assert calls == ['stdin']


def test_bootstrap_exact_token_reaches_read_only_guard_then_mock_engine(streaming_script, monkeypatch, tmp_path):
    script = streaming_script; calls = []
    data = {'run_id': str(uuid4()), 'execution_pipeline': 'rolling_v1',
        'execution_pipeline_sha256': 'a' * 64, 'workflow': {'version': 'fixture-v1', 'sha256': 'b' * 64}}
    frozen = script.canonical(data)
    row = {'payload': frozen, 'payload_hash': hashlib.sha256(frozen.encode()).hexdigest()}
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def execute(self, query, params):
            assert query.startswith('SELECT payload,payload_hash') and params == (data['run_id'],)
            calls.append('guard'); return self
        def fetchone(self): return row
    class Store:
        def __init__(self): calls.append('store')
        def connect(self): return Connection()
    def binding(path, store):
        calls.append('binding'); return {'data': data, 'folder': tmp_path}
    class Runtime:
        def __init__(self, current, folder, guard):
            assert calls[-1] == 'guard'
            calls.append('runtime')
        model = render = None
    class Engine:
        def __init__(self, current, folder, *, guard, run_model, run_render): self.guard = guard
        async def run(self):
            self.guard(); calls.append('engine'); return {'status': 'needs_review'}
    monkeypatch.setattr(script.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'ROLLING_V1_START\n')))
    monkeypatch.setattr(script, 'ReadOnlyRunStore', Store)
    monkeypatch.setattr(script, 'load_binding', binding)
    monkeypatch.setattr(script, 'LocalStageRuntime', Runtime)
    monkeypatch.setattr(script, 'StreamingProducer', Engine)
    monkeypatch.setattr(script, 'orchestration_configuration', lambda *args: {'ready': True, 'sha256': 'a' * 64})
    monkeypatch.setattr(script, 'video_readiness', lambda *args, **kwargs: {'configuration': {
        'ready': True, 'workflow_version': 'fixture-v1', 'workflow_sha256': 'b' * 64}})
    assert script.main(['--run-input', str(tmp_path / 'run-input.json')]) == 0
    assert calls == ['store', 'binding', 'binding', 'guard', 'runtime', 'binding', 'guard', 'engine']


def test_bootstrap_real_harmless_python_entry_exits_on_eof_before_authority(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/run_streaming_producer.py'
    result = module.subprocess.run([module.sys.executable, '-B', str(script), '--run-input',
        str(tmp_path / 'nonexistent-run-input.json')], input=b'', capture_output=True, timeout=15)
    assert result.returncode == 2 and result.stdout == b'' and result.stderr == b''

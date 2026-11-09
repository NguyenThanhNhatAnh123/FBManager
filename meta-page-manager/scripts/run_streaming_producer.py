"""Run one already-authorized rolling producer; never creates a host run."""
import argparse
import asyncio
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))

from page_manager.producer_stages import LocalStageRuntime, StreamingProducer, StageError
from page_manager.producer_orchestration import orchestration_configuration
from page_manager.project_paths import PRODUCER_ROOT
from page_manager.idea_workspace import video_readiness
from uploader_client import ReadOnlyRunStore, load_binding, canonical, parse_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-input', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Host sends this only after assigning the coordinator to its owned
        # Windows Job. No authority reads or child processes precede the gate.
        if sys.stdin.buffer.readline(64) != b'ROLLING_V1_START\n':
            return 2
        store = ReadOnlyRunStore()
        binding = load_binding(args.run_input, store)
        data = binding['data']
        def guard():
            current = load_binding(args.run_input, store)
            if current['data'] != data: raise StageError()
            with store.connect() as db:
                row = db.execute('SELECT payload,payload_hash FROM idea_manual_runs WHERE id=?', (data['run_id'],)).fetchone()
            frozen = parse_json(row['payload'].encode('utf-8')) if row else None
            if (not isinstance(frozen, dict) or frozen.get('execution_pipeline') != 'rolling_v1'
                    or data.get('execution_pipeline') != 'rolling_v1'
                    or frozen.get('review_policy') != data.get('review_policy')
                    or frozen.get('execution_pipeline_sha256') != data.get('execution_pipeline_sha256')
                    or hashlib.sha256(canonical(frozen).encode('utf-8')).hexdigest() != row['payload_hash']):
                raise StageError()
            configuration = orchestration_configuration(ROOT, PRODUCER_ROOT)
            if not configuration['ready'] or configuration['sha256'] != frozen.get('execution_pipeline_sha256'):
                raise StageError()
            workflow = video_readiness(ROOT, workflow_directory=PRODUCER_ROOT).get('configuration', {})
            if (not workflow.get('ready') or frozen.get('workflow') != {
                    'version': workflow.get('workflow_version'), 'sha256': workflow.get('workflow_sha256')}):
                raise StageError()
            seed = frozen.get('preflight_seed')
            expected_seeds = ([{'image_path': str(binding['folder'] / 'seed-images/01.png'),
                'image_sha256': seed['image_sha256'], 'origin': seed['origin']}] if seed else None)
            if data.get('preflight_images') != expected_seeds:
                raise StageError()
        guard()
        runtime = LocalStageRuntime(data, binding['folder'], guard)
        output = asyncio.run(StreamingProducer(data, binding['folder'], guard=guard,
            run_model=runtime.model, run_render=runtime.render).run())
        return 0 if output['status'] in ('ready', 'needs_review', 'manual_review_pending') else 2
    except (Exception, KeyboardInterrupt):
        # No provider error, tool stdout, prompt, credential or private path leaks.
        print('{"status":"needs_review","reason":"producer_validation_failed"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

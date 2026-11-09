"""Private, digest-bound local handoff recovery. Never starts a producer.

Run only after inspecting the stopped producer's same canonical run folder.
It uses the existing consumer and leaves importing/scheduling to the live host.
No HTTP endpoint or worker-supplied QA override is added.

--preflight-only checks the digest, stopped producer and frozen bindings; it is
not an assertion that media/QA passed. A real acceptance uses the validator
whose SHA was pinned before production, independently decodes all media, and
may let the live host create real future schedules. A scheduling-only trial
must have its exact canonical jobs verified and cancelled before their due
time. Failed/uncertain recovery attempts remain held and do not auto-retry.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from page_manager.errors import AppError
from page_manager.idea_pipeline import IdeaPipeline
from page_manager.idea_recovery import IdeaRunRecovery, StoredVideoSource, trusted_bridge
from page_manager.idea_run_results import IdeaRunResults
from page_manager.audio_approvals import AudioApprovals
from page_manager.service import PageManager
from page_manager.project_paths import BRIDGE_PATH,LEGACY_BRIDGE_PATH,LEGACY_RUNS_DIRECTORY,select_existing_run_directory


async def execute(arguments):
    # No app factory/IdeaRuns/FolderVideoSource/TokenReelScheduler construction:
    # their startup recovery and background ticks belong to the live host.
    manager = PageManager(ROOT)
    pipeline = IdeaPipeline(manager)
    source = StoredVideoSource(manager)
    directory=select_existing_run_directory(arguments.run_id)
    if directory is None:raise AppError('Không có duy nhất thư mục lượt chạy đã biết.','recovery_output_invalid',409)
    bridge_path=LEGACY_BRIDGE_PATH if directory.parent==LEGACY_RUNS_DIRECTORY else BRIDGE_PATH
    consumer = IdeaRunResults(manager, pipeline, source, runs_directory=directory.parent,bridge=trusted_bridge(bridge_path))
    consumer.audio_approvals = AudioApprovals(manager.store,ROOT,workflow_directory=pipeline.output_directory)
    recovery = IdeaRunRecovery(manager, pipeline, consumer,runs_directory=directory.parent)
    if arguments.preflight_only:
        recovery.preflight(arguments.run_id, arguments.expected_output_sha256)
        return {'preflight_passed': True, 'full_media_qa_checked': False, 'accepted': False}
    return await recovery.recover(arguments.run_id, arguments.expected_output_sha256)


def main():
    parser = argparse.ArgumentParser(description='Privately recover an existing held handoff; never generate or publish.')
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--expected-output-sha256', required=True)
    parser.add_argument('--preflight-only', action='store_true')
    arguments = parser.parse_args()
    try:
        report = asyncio.run(execute(arguments))
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report.get('accepted') or report.get('preflight_passed') else 2
    except AppError as error:
        print(json.dumps({'accepted': False, 'code': error.code}))
        return 2
    except (KeyboardInterrupt, Exception):
        # Never print provider errors, raw worker output, environment or tokens.
        print(json.dumps({'accepted': False, 'code': 'recovery_operator_stopped'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

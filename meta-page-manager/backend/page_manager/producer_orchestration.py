"""Fixed rolling-production policy; discovery never launches workers.

Keep the legacy media/QA contract unchanged. New requests additionally freeze
the implementation of the rolling coordinator and its per-process profiles.
"""
import hashlib
import json
from pathlib import Path

from .idea_workspace import _regular_bytes


MODE = 'rolling_v1'
MANUAL_REVIEW_POLICY = 'manual_review_v1'
MANAGER_FILES = (
    'backend/page_manager/producer_orchestration.py',
    'backend/page_manager/producer_stages.py',
    'backend/page_manager/producer_runtime.py',
    'backend/page_manager/provider_profile.py',
    'backend/page_manager/agent_roles.py',
    'scripts/run_streaming_producer.py',
    'scripts/run_manual_portrait_renderer.py',
    'scripts/uploader_client.py',
)
PRODUCER_FILES = ('.agents/skills/page-shirt-zoom-8s/references/rolling-v1.md',
                  '.agents/skills/page-shirt-zoom-8s/references/manual-review-v1.md')
VIA_FILES = ('integration-diagnostics/verify_zoom_camera.py',)


def orchestration_configuration(root, project):
    """Hash fixed, regular local source files, never worker-controlled paths."""
    hashes = []
    try:
        for prefix, directory, names in (
            ('manager', Path(root), MANAGER_FILES),
            ('producer', Path(project), PRODUCER_FILES),
            ('via', Path(root).parent, VIA_FILES),
        ):
            for name in names:
                raw = _regular_bytes(directory / name, 2 * 1024 * 1024)
                if not raw:
                    raise ValueError('Empty pipeline file')
                hashes.append((prefix + '/' + name, hashlib.sha256(raw).hexdigest()))
        digest = hashlib.sha256(json.dumps(
            {'mode': MODE, 'files': hashes}, separators=(',', ':'), ensure_ascii=True,
        ).encode('ascii')).hexdigest()
    except (OSError, ValueError, TypeError):
        return {'mode': MODE, 'ready': False, 'sha256': None}
    return {'mode': MODE, 'ready': True, 'sha256': digest}


def orchestration_policy():
    """Display configuration, not a claim that generation or QA has run."""
    return {
        'mode': MODE, 'review_policy': MANUAL_REVIEW_POLICY,
        'image_workers': 1, 'render_workers': 1, 'qa_workers': 0,
        'queue_capacity': 2, 'max_model_workers': 1,
        'product_scope': 'once_per_idea_before_images',
        'handoff_policy': 'per_variant_manual_review_pending',
        'stages': [
            {'id': 'product', 'skills': ['batch-remove-background'],
             'tools': ['run_bound_uploader'], 'provider': 'codex'},
            {'id': 'image', 'skills': ['imagegen', 'realistic-shirt-photos-9x16',
                'realistic-shirt-photos-closeup-9x16', 'ao-cao-cap'],
             'tools': ['native_imagegen'], 'provider': 'codex'},
            {'id': 'video', 'skills': [], 'tools': ['ffmpeg', 'ffprobe',
                'approved_pcm_check'], 'provider': 'local'},
            {'id': 'qa', 'skills': [],
             'tools': ['human_video_review'], 'provider': 'human'},
            {'id': 'handoff', 'skills': [], 'tools': ['offline_bridge', 'trusted_consumer'],
             'provider': 'local'},
        ],
    }

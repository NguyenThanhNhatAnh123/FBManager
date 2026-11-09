"""Inspect fixed producer files without launching Codex, generating or writing anything."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'backend'))

from page_manager.idea_workspace import (VIDEO_ATTESTATION_SCHEMA,
                                         video_readiness, video_workflow_configuration)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manager-root', type=Path, default=ROOT)
    parser.add_argument('--workflow-directory', type=Path)
    parser.add_argument('--attestation-template', action='store_true',
                        help='Print a BLOCKED template; it is not runtime proof and is not saved.')
    args = parser.parse_args()
    configuration = video_workflow_configuration(workflow_directory=args.workflow_directory)
    if args.attestation_template:
        value = {'schema_version': VIDEO_ATTESTATION_SCHEMA,
                 'kind': 'producer_workflow_attestation',
                 'status': 'blocked', 'producer_workflow_status': 'blocked',
                 'checked_at': datetime.now(timezone.utc).isoformat(),
                 'workflow_version': configuration['workflow_version'],
                 'workflow_sha256': configuration['workflow_sha256']}
    else:
        value = video_readiness(args.manager_root, workflow_directory=args.workflow_directory)
    print(json.dumps(value, ensure_ascii=True, indent=2))
    return 0 if configuration['ready'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

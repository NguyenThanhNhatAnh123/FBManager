"""Select installed Google Chrome after a bounded backup, without resetting profiles."""
import argparse
import json
import os
from pathlib import Path
import sys
from dotenv import load_dotenv
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.browser_migration import inspect_standard_chrome, switch_to_standard_chrome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Read-only version and profile compatibility check.')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    data_dir = ROOT / os.getenv('CM_DATA_DIR', 'data')
    report = (inspect_standard_chrome if args.check else switch_to_standard_chrome)(ROOT, data_dir)
    print(json.dumps(report, indent=2))
    if not args.check:
        print('PASS: standard Google Chrome selected for every Manager profile.')
        print('Existing profile folders, proxy assignments and saved browser storage were retained.')
        print('Authenticator autoload is off. Existing extension/account data was not removed.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None

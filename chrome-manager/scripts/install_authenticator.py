"""Read-only Authenticator inventory for Manager profiles. Never writes Chrome policies."""
import argparse
import json
import sys
import webbrowser
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import Config
import sqlite3
from app.services.extensions import STORE_URL, inspect_authenticator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--open-dashboard', action='store_true')
    args = parser.parse_args()
    config = Config()
    database = config.data_dir / 'manager.sqlite3'
    print('Scope: existing Manager profiles only. Chrome personal settings and Windows registry are unchanged.')
    print('Official extension: ' + STORE_URL)
    if database.is_file():
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            rows = [{'profile_id': pid, 'profile_name': name, **inspect_authenticator(profile_path, config.profiles_dir, bundle_directory=config.authenticator_deployment['bundle_path'] if config.authenticator_deployment else None)}
                    for pid, name, profile_path in db.execute('SELECT id,name,profile_path FROM profiles ORDER BY id')]
        print(json.dumps(rows, ensure_ascii=True, indent=2))
    else:
        print('Start the Manager and create profiles first.')
    print('Open Chrome Profiles > Authenticator; use Open Web Store for each assigned profile, then Add to Chrome.')
    print('Chrome installation prompts must be completed in the matching profile. No OTP accounts are copied.')
    if args.open_dashboard:
        webbrowser.open(f'http://127.0.0.1:{config.port}/')


if __name__ == '__main__':
    main()

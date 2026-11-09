"""Prepare/enable app-scoped autoload from already installed Web Store software."""
import argparse
import json
import os
import sys
import re
import subprocess
import shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import Config
from app.core.extension_bundle import prepare_from_installed, load_deployment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    config = Config()
    if not args.enable:
        from filelock import FileLock, Timeout
        try:
            with FileLock(str(config.data_dir / 'manager.lock')).acquire(timeout=0):
                plan = prepare_from_installed(ROOT, config.data_dir)
        except Timeout:
            raise ValueError('Stop the current Manager server with Ctrl+C before running start-auto-authenticator.bat.') from None
        print(f"Prepared official Authenticator {plan['version']} software from Manager profile #{plan['source_profile_id']}.")
        print('Only extension software was copied. No 2FA accounts, cookies or browser storage were copied.')
        print(f"Scope: {plan['profile_count_at_preparation']} existing Manager profiles, plus future Manager profiles. Personal Chrome is unaffected.")
        return
    # Native startup runs this after the standard Playwright browser installer.
    # Never write Windows registry or Chrome security preferences.
    from filelock import FileLock, Timeout
    lock = FileLock(str(config.data_dir / 'manager.lock'))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        raise ValueError('Stop the running Manager with Ctrl+C before enabling automatic Authenticator.') from None
    try:
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str((ROOT / '.browsers').resolve())
        import playwright
        descriptors = Path(playwright.__file__).parent / 'driver' / 'package' / 'browsers.json'
        browser_info = next(item for item in json.loads(descriptors.read_text(encoding='utf-8'))['browsers'] if item['name']=='chromium')
        executable = ROOT / '.browsers' / ('chromium-' + browser_info['revision']) / 'chrome-win64' / 'chrome.exe'
        if not executable.is_file() or not executable.resolve().is_relative_to((ROOT / '.browsers').resolve()):
            raise ValueError('Private Chromium is not installed. The startup script must install it first.')
        plan_path = config.data_dir / 'authenticator-plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        # Test a fresh temporary profile first. Network is blocked by a dead
        # loopback proxy; this does not touch any user's browsing or 2FA data.
        bundle_path = (ROOT / plan['bundle']).resolve()
        if not bundle_path.is_relative_to((ROOT / '.runtime' / 'extensions' / 'authenticator').resolve()):
            raise ValueError('Unsafe prepared bundle path.')
        bundle = str(bundle_path)
        from app.core.extension_bundle import validate_manifest, software_files
        if validate_manifest(Path(bundle))['version'] != plan['version'] or software_files(Path(bundle)) != plan['files']:
            raise ValueError('Prepared software bundle was modified; automatic mode was not enabled.')
        node = shutil.which('node.exe') or shutil.which('node')
        if not node:
            raise ValueError('Node.js is required for the real Authenticator preflight.')
        environment = os.environ.copy()
        environment['CM_TEST_CHROMIUM_PATH'] = str(executable)
        tested = subprocess.run([node, str(ROOT / 'scripts' / 'authenticator_preflight.cjs')],
                                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=90)
        if tested.returncode:
            detail = (tested.stderr or tested.stdout or 'No diagnostic output.').splitlines()[0][:300]
            raise ValueError('Private Chromium preflight failed: ' + detail)
        proof = json.loads((ROOT / '.runtime' / 'auth-autoload-proof.json').read_text(encoding='utf-8'))
        if (proof.get('extensionId') != plan['extension_id'] or proof.get('bundleVersion') != plan['version']
                or proof.get('payloadManifestHash') != plan['files']['manifest.json']
                or Path(proof.get('executable','')).resolve() != executable.resolve()
                or not proof.get('reopenVerified') or len(proof.get('reports',[])) != 3
                or not all(row.get('serviceWorker') and row.get('popupRendered') for row in proof['reports'])):
            raise ValueError('Private Chromium preflight evidence did not match the prepared software.')
        browser_version = proof['browserVersion']
        plan['preflight'] = {'service_worker_seen':True,'popup_loaded':True,'reopen_verified':True,'chromium_version':browser_version}
        print('PASS: automatic Authenticator in two isolated profiles and after reopen; no Web Store clicks.')
        # Refuse a major browser downgrade before opening existing user data.
        with __import__('sqlite3').connect((config.data_dir / 'manager.sqlite3').resolve().as_uri()+'?mode=ro', uri=True) as connection:
            for (raw_path,) in connection.execute('SELECT profile_path FROM profiles'):
                last_version = Path(raw_path) / 'Last Version'
                if last_version.is_file():
                    previous_major = re.match(r'\d+', last_version.read_text(encoding='utf-8').strip())
                    if previous_major and int(previous_major.group()) > int(browser_version.split('.')[0]):
                        raise ValueError('Private Chromium is older than an existing profile. Update Playwright before enabling; original Chrome mode was retained.')
        if config.authenticator_deployment:
            plan['backup'] = config.authenticator_deployment.get('backup')
        else:
            from app.core.profile_backup import backup_before_chromium
            plan['backup'] = backup_before_chromium(ROOT, config.data_dir)
        plan['enabled'] = True
        target = config.data_dir / 'authenticator-auto.json'
        previous = target.read_text(encoding='utf-8') if target.is_file() else None
        target.write_text(json.dumps(plan, indent=2), encoding='utf-8')
        try:
            load_deployment(ROOT, config.data_dir)
        except Exception:
            if previous is not None:
                target.write_text(previous, encoding='utf-8')
            else:
                target.write_text('{"enabled":false}', encoding='utf-8')
            raise
        print('Automatic Authenticator enabled for Manager profiles only. Private Chromium will load it at every launch.')
        print('This configures future launches; it does not mean every profile has already been opened or verified.')
    finally:
        lock.release()


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(str(error)) from None

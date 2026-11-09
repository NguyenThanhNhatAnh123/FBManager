"""Single-profile manual comparison. Does not attach automation to the browser."""
import argparse
import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import psutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from filelock import FileLock, Timeout as LockTimeout
from playwright.async_api import async_playwright
from sqlalchemy import select
from app.core.config import Config
from app.database.session import Database
from app.models.entities import Profile, Proxy, TaskRun
from app.services.proxies import from_row
from app.services.proxy_bridge import BrowserProxyBridge
from app.services.resources import Resources
from app.services.state import State
from app.services.manual_browser import request_window_close


def manager_browser_count(profiles_root):
    directories = set()
    for process in psutil.process_iter(['name', 'cmdline']):
        try:
            if (process.info['name'] or '').lower() not in ('chrome.exe', 'chromium.exe'):
                continue
            arguments = process.info['cmdline'] or []
            if any(argument.startswith('--type=') for argument in arguments):
                continue
            for index, argument in enumerate(arguments):
                value = argument.split('=', 1)[1] if argument.startswith('--user-data-dir=') else arguments[index+1] if argument == '--user-data-dir' and index+1 < len(arguments) else None
                if value:
                    directory = Path(value.strip('"')).resolve()
                    if directory != profiles_root and directory.is_relative_to(profiles_root):
                        directories.add(directory)
        except (psutil.Error, OSError):
            continue
    return len(directories)


async def main(profile_id):
    if os.name != 'nt':
        raise ValueError('This diagnostic is for Windows.')
    config = Config()
    db = Database(config.data_dir / 'manager.sqlite3')
    lock = bridge = browser = None
    try:
        state = State(db)
        resource = Resources()
        resource.sample()
        if reason := resource.blocked_reason(state.settings):
            raise ValueError(reason)
        if manager_browser_count(config.profiles_dir.resolve()) >= state.settings.max_browsers:
            raise ValueError('Browser limit reached. Stop a Manager profile before this one-profile diagnostic.')
        with db.session() as session:
            profile = session.get(Profile, profile_id)
            if not profile or not profile.proxy_id:
                raise ValueError('The selected profile needs an assigned proxy.')
            if session.scalar(select(TaskRun.id).where(TaskRun.profile_id == profile_id, TaskRun.status.in_(['Pending', 'Running'])).limit(1)):
                raise ValueError('Stop the selected profile task first.')
            row = session.get(Proxy, profile.proxy_id)
            if row.status in ('Failed', 'Timeout') and state.settings.proxy_routing == 'all':
                raise ValueError('Fix/check the assigned proxy first. Direct fallback is disabled.')
            proxy = from_row(row)
            profile_name, proxy_id = profile.name, row.id
            directory = Path(profile.profile_path).resolve()
        base = config.profiles_dir.resolve()
        if directory == base or not directory.is_relative_to(base):
            raise ValueError('Unsafe profile directory.')
        lock = FileLock(str(directory / '.manager.lock'))
        lock.acquire(timeout=0)
        # Resolve the exact private runtime using the installed Playwright package,
        # then stop its driver BEFORE launching a genuinely manual browser process.
        if config.authenticator_deployment or not config.channel:
            async with async_playwright() as driver:
                executable = Path(driver.chromium.executable_path)
        else:
            candidates = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                          Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
                          Path(os.environ['LOCALAPPDATA']) / 'Google/Chrome/Application/chrome.exe']
            executable = next((path for path in candidates if path.is_file()), None)
        if not executable or not executable.is_file():
            raise ValueError('The configured browser is not installed.')
        arguments = [f'--user-data-dir={directory}', '--new-window', '--no-first-run', '--no-default-browser-check',
                     '--proxy-bypass-list=<-loopback>', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp']
        if proxy.username or state.settings.proxy_routing == 'facebook_only':
            bridge = BrowserProxyBridge(proxy, directory, lambda message: print(message, flush=True), routing=state.settings.proxy_routing)
            options = await bridge.start()
            arguments.append('--proxy-server=' + options['server'])
        else:
            host = f'[{proxy.host}]' if ':' in proxy.host else proxy.host
            arguments.append(f'--proxy-server={proxy.protocol}://{host}:{proxy.port}')
        if config.authenticator_deployment:
            bundle = config.authenticator_deployment['bundle_path']
            arguments.extend([f'--disable-extensions-except={bundle}', f'--load-extension={bundle}'])
        arguments.append('https://www.facebook.com/')
        browser = await asyncio.create_subprocess_exec(str(executable), *arguments,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        print(f'Manual comparison: {profile_name}, assigned proxy #{proxy_id}.', flush=True)
        print('Use this window manually. Complete any Facebook verification yourself.', flush=True)
        print('No Playwright browser connection, remote debugging, scripts, or automation tasks are active.', flush=True)
        print('Close this diagnostic Chrome window when finished. Then you can reopen the profile in Manager.', flush=True)
        report = {'profile_id': profile_id, 'profile_name': profile_name, 'proxy_id': proxy_id,
                  'same_profile_directory': True, 'same_configured_browser': True,
                  'automation_attached': False, 'proxy_fallback': False, 'proxy_routing': state.settings.proxy_routing}
        (ROOT / 'logs/manual-browser-diagnostic.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        try:
            result = await browser.wait()
        except asyncio.CancelledError:
            request_window_close(browser.pid)
            print('Waiting for this diagnostic Chrome to close normally...', flush=True)
            result = await browser.wait()
        if result:
            raise ValueError('The manual browser exited with an error. Confirm this profile is stopped in Manager.')
    finally:
        # Keep the proxy and directory lock alive until the manual browser exits.
        if browser and browser.returncode is None:
            request_window_close(browser.pid)
            await browser.wait()
        if bridge:
            await bridge.close()
        if lock and lock.is_locked:
            lock.release()
        db.engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile-id', type=int, default=1)
    args = parser.parse_args()
    try:
        if args.profile_id < 1:
            raise ValueError('Profile ID must be positive.')
        asyncio.run(main(args.profile_id))
    except LockTimeout:
        print('Stop this profile in Manager first, then run the diagnostic again.', flush=True)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as error:
        # Only messages authored in this file are suitable for the console.
        safe = str(error) if isinstance(error, ValueError) else 'Diagnostic startup failed. Check the browser, proxy, and profile lock.'
        print(safe, flush=True)
        sys.exit(1)

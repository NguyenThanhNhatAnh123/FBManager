"""Per-profile setup for the official Authenticator; no Windows policy writes."""
import asyncio
import json
import re
from pathlib import Path
from sqlalchemy import select
from playwright.async_api import TimeoutError as BrowserTimeout
from app.models.entities import Profile

EXTENSION_ID = 'bhghoamapcdpbohphigoooaddinpkbai'
STORE_URL = f'https://chromewebstore.google.com/detail/authenticator/{EXTENSION_ID}'


def inspect_authenticator(profile_path, profiles_root, context=None, bundle_directory=None):
    """Read installation metadata only; never access chrome.storage or OTP data."""
    source, root = Path(profile_path), Path(profiles_root).resolve()
    path = source.resolve()
    if source.is_symlink() or not path.is_relative_to(root) or path == root:
        raise ValueError('Unsafe profile directory.')
    runtime = bool(context and any(worker.url.startswith(f'chrome-extension://{EXTENSION_ID}/') for worker in context.service_workers))
    files = False
    bundle_registered = False
    versions = []
    extension_root = path / 'Default' / 'Extensions' / EXTENSION_ID
    for manifest_path in extension_root.glob('*/manifest.json'):
        try:
            resolved = manifest_path.resolve()
            if not resolved.is_relative_to(path) or resolved.stat().st_size > 1_000_000:
                continue
            manifest = json.loads(resolved.read_text(encoding='utf-8'))
            if isinstance(manifest, dict) and isinstance(manifest.get('version'), str):
                files = True
                versions.append(manifest['version'][:40])
        except (OSError, ValueError):
            continue
    state, disabled, row = None, False, None
    # Chrome owns these files. Read only the target extension's install metadata.
    for filename in ('Secure Preferences', 'Preferences'):
        prefs = path / 'Default' / filename
        try:
            if not prefs.resolve().is_relative_to(path) or prefs.stat().st_size > 8_000_000:
                continue
            row = json.loads(prefs.read_text(encoding='utf-8')).get('extensions', {}).get('settings', {}).get(EXTENSION_ID)
            if isinstance(row, dict):
                state, disabled = row.get('state'), bool(row.get('disable_reasons'))
                break
        except (OSError, ValueError, AttributeError):
            continue
    if bundle_directory and isinstance(row, dict):
        bundle = Path(bundle_directory).resolve()
        allowed = root.parent / '.runtime' / 'extensions' / 'authenticator'
        registered_path = row.get('path', '')
        if bundle.is_relative_to(allowed.resolve()) and registered_path and Path(registered_path).resolve() == bundle:
            try:
                from app.core.extension_bundle import validate_manifest
                manifest = validate_manifest(bundle)
                files = True
                bundle_registered = True
                versions.append(manifest['version'])
            except (ValueError, OSError):
                pass
    registered = bundle_registered or isinstance(row, dict) and (state == 1 or isinstance(row.get('manifest'), dict) and isinstance(row.get('location'), int))
    status = 'Verified' if runtime else 'Disabled' if files and (disabled or state == 0) else 'Installed' if files and registered else 'Files detected' if files else 'Not installed'
    return {'status': status, 'runtime_verified': runtime, 'files_detected': files, 'versions': sorted(set(versions))}


def web_store_error_message(error):
    if isinstance(error, BrowserTimeout):
        return 'Chrome Web Store did not load within 45 seconds through this profile proxy.'
    # Only a bounded Chrome error token may leave exception memory, never URLs,
    # headers, user names or credentials from an exception body.
    match = re.search(r'net::ERR_[A-Z_]{1,60}', str(error))
    if match:
        return f'Chrome Web Store navigation failed ({match.group(0)}). Check this profile proxy.'
    return 'Could not open Chrome Web Store through this profile. Check its proxy and retry.'


class ExtensionManager:
    def __init__(self, db, state, config, browsers, tasks):
        self.db, self.state, self.config, self.browsers, self.tasks = db, state, config, browsers, tasks
        self.jobs, self.pages, self.errors = {}, {}, {}

    def rows(self,profile_ids=None):
        with self.db.session() as session:
            result = []
            query=select(Profile).order_by(Profile.id)
            if profile_ids is not None:query=query.where(Profile.id.in_(profile_ids))
            for profile in session.scalars(query):
                try:
                    deployment = self.config.authenticator_deployment
                    info = inspect_authenticator(profile.profile_path, self.config.profiles_dir, self.browsers.contexts.get(profile.id), deployment['bundle_path'] if deployment else None)
                    if deployment and info['status'] in ('Not installed', 'Files detected'):
                        info['status'] = 'Auto on next launch'
                    if deployment and info['runtime_verified'] and not info['versions']:
                        info['versions'] = [deployment['version']]
                except ValueError:
                    info = {'status': 'Unavailable', 'runtime_verified': False, 'files_detected': False, 'versions': []}
                result.append({'profile_id': profile.id, 'profile_name': profile.name, **info,
                               'setup_status': 'Opening' if profile.id in self.jobs else ('Authenticator open' if self.config.authenticator_deployment else 'Web Store open') if profile.id in self.pages else None,
                               'error': self.errors.get(profile.id), 'has_proxy': bool(profile.proxy_id)})
            return result

    def request_setup(self, profile_id):
        if profile_id in getattr(self.browsers,'reel_profiles',set()):
            raise ValueError('Dừng lượt đăng Reel trước khi cài extension cho profile này.')
        if profile_id in getattr(self.browsers,'recipient_profiles',set()):
            raise ValueError('Dừng lượt nhận Page trước khi cài extension cho profile này.')
        if profile_id in self.browsers.outlook_profiles:
            raise ValueError('Stop the Outlook sign-in before installing an extension.')
        if profile_id in self.browsers.facebook_profiles:
            raise ValueError('Stop the Facebook check before installing an extension.')
        if self.config.headless and not self.config.authenticator_deployment:
            raise ValueError('Authenticator installation requires visible Chrome. Set CM_HEADLESS=false and restart.')
        if profile_id in self.tasks.profile_jobs:
            raise ValueError('Stop the automation task before installing an extension in this profile.')
        self.browsers.validate_start(profile_id)
        if profile_id in self.jobs or profile_id in self.pages:
            return
        self.errors.pop(profile_id, None)
        self.browsers.extension_profiles.add(profile_id)
        job = asyncio.create_task(self._open(profile_id))
        self.jobs[profile_id] = job
        def done(task):
            if self.jobs.get(profile_id) is task:
                self.jobs.pop(profile_id, None)
            if profile_id not in self.pages:
                self.browsers.extension_profiles.discard(profile_id)
            if not task.cancelled():
                task.exception()
            self.state.emit()
        job.add_done_callback(done)
        self.state.emit()

    async def _open(self, profile_id):
        page = None
        try:
            context = await self.browsers.ensure_open(profile_id)
            if getattr(context, 'is_manual', False):
                target = f'chrome-extension://{EXTENSION_ID}/view/popup.html' if self.config.authenticator_deployment else STORE_URL
                await context.open_url(target)
                self.state.log('Authenticator opened in this manual browser. Installation metadata is available in Manager.', profile_id)
                return
            page = await context.new_page()
            self.pages[profile_id] = page
            def closed(*_):
                if self.pages.get(profile_id) is page:
                    self.pages.pop(profile_id, None)
                    self.browsers.extension_profiles.discard(profile_id)
                    self.state.emit()
            page.on('close', closed)
            target = f'chrome-extension://{EXTENSION_ID}/view/popup.html' if self.config.authenticator_deployment else STORE_URL
            response = await page.goto(target, wait_until='domcontentloaded', timeout=45000)
            if response and response.status >= 400:
                self.errors[profile_id] = f'Chrome Web Store returned HTTP {response.status} through this profile proxy.'
                raise ValueError('Web Store rejected the request.')
            await page.bring_to_front()
            if self.config.authenticator_deployment:
                if not any(worker.url.startswith(f'chrome-extension://{EXTENSION_ID}/') for worker in context.service_workers):
                    await context.wait_for_event('serviceworker', predicate=lambda worker: worker.url.startswith(f'chrome-extension://{EXTENSION_ID}/'), timeout=10000)
                self.state.log('Authenticator automatically loaded and its extension UI opened in this profile.', profile_id)
            else:
                self.state.log('Authenticator Web Store opened. Complete the Chrome installation prompt in this profile.', profile_id)
        except asyncio.CancelledError:
            if page and not page.is_closed():
                await page.close()
            raise
        except Exception as error:
            self.errors.setdefault(profile_id, web_store_error_message(error))
            self.state.log(self.errors[profile_id], profile_id, 'error')
            if page and not page.is_closed():
                await page.close()
        finally:
            self.state.emit()

    async def stop(self, profile_id):
        job = self.jobs.get(profile_id)
        if job:
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
            self.jobs.pop(profile_id, None)
        page = self.pages.pop(profile_id, None)
        if page and not page.is_closed():
            await page.close()
        self.browsers.extension_profiles.discard(profile_id)
        self.state.emit()

    async def shutdown(self):
        await asyncio.gather(*(self.stop(pid) for pid in set(self.jobs) | set(self.pages)), return_exceptions=True)

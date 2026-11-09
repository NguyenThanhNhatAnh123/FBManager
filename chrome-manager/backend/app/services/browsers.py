import asyncio
from pathlib import Path
from filelock import FileLock, Timeout as LockTimeout
from playwright.async_api import async_playwright, Error as PlaywrightError
from app.models.entities import Profile, Proxy, BmProfile, BmProxy, BmBrowserSettings
from app.core.first_open import pending_first_open, complete_first_open, STORE_URL
from app.core.mobile_emulation import bm_mobile_options
from app.services.proxies import from_row
from app.services.proxy_bridge import BrowserProxyBridge
from app.services.browser_diagnostics import BrowserNetworkDiagnostics
from app.services.manual_browser import ManualBrowser, FACEBOOK

class BrowserManager:
    def __init__(self, db, state, config, resources):
        self.db, self.state, self.config, self.resources = db, state, config, resources
        self.contexts, self.launches, self.reserved, self.locks, self.bridges = {}, {}, set(), {}, {}
        self.errors = {}
        self.modes = {}
        self.extension_profiles = set()
        self.facebook_profiles = set()
        self.outlook_profiles = set()
        self.recipient_profiles = set()
        self.reel_profiles = set()
        self.playwright = None
        self.closing = False
        self.condition = asyncio.Condition()

    async def initialize(self):
        self.playwright = await async_playwright().start()

    def status(self, profile_id):
        if profile_id in self.contexts:
            return 'Running'
        if profile_id in self.reserved:
            return 'Starting'
        if profile_id in self.launches:
            return 'Queued'
        return 'Error' if profile_id in self.errors else 'Stopped'

    def validate_start(self, profile_id):
        with self.db.session() as session:
            profile = session.get(Profile, profile_id)
            if not profile:
                raise ValueError('Profile does not exist.')
            is_bm = session.get(BmProfile, profile_id) is not None
            if is_bm:
                scope = session.get(BmProxy, profile.proxy_id) if profile.proxy_id else None
                if scope is None or scope.profile_id != profile_id:
                    raise ValueError('Profile BM cần một proxy riêng được gán đúng profile trước khi mở.')
            routing = 'all' if is_bm else self.state.settings.proxy_routing
            if self.state.settings.require_proxy and not profile.proxy_id:
                raise ValueError('Assign a proxy first, or explicitly enable direct connections in Settings.')
            if profile.proxy_id:
                proxy = session.get(Proxy, profile.proxy_id)
                if proxy.status in ('Failed', 'Timeout') and routing == 'all':
                    raise ValueError('The assigned proxy failed its last check. Fix and check it before opening.')
                from_row(proxy)  # Fail before queuing when DPAPI cannot decrypt.

    def mode(self, profile_id):
        if profile_id in self.contexts or profile_id in self.launches or profile_id in self.reserved:
            return self.modes.get(profile_id)
        return None

    def mobile_enabled(self, profile_id):
        with self.db.session() as session:
            settings=session.get(BmBrowserSettings,profile_id)
            return bool(settings and settings.mobile_enabled and session.get(BmProfile,profile_id) is not None)

    def validate_automation_start(self, profile_id, *, for_facebook=False, for_outlook=False, for_recipient=False, for_reel=False):
        if profile_id in self.reel_profiles and not for_reel:
            raise ValueError('Profile đang được lịch đăng Reel sử dụng. Dừng lượt đăng trước khi dùng tác vụ khác.')
        if profile_id in self.recipient_profiles and not for_recipient:
            raise ValueError('Dừng hoặc tiếp tục lượt nhận Page trước khi dùng profile này.')
        if profile_id in self.outlook_profiles and not for_outlook:
            raise ValueError('Stop the Outlook sign-in before using this profile.')
        if profile_id in self.facebook_profiles and not for_facebook:
            raise ValueError('Stop the Facebook check before using this profile.')
        self.validate_start(profile_id)
        active = profile_id in self.contexts or profile_id in self.launches or profile_id in self.reserved
        if active and self.modes.get(profile_id) != 'automation':
            raise ValueError('Stop this profile before running an automation task. Manual browsing keeps normal website features; tasks use a separate controlled browser session with the same saved profile data.')

    def request_start(self, profile_id, *, open_start_page=True, for_automation=False, for_facebook=False, for_outlook=False, for_recipient=False, for_reel=False, timezone_id=None):
        if self.closing:
            raise ValueError('Application is shutting down.')
        if profile_id in self.reel_profiles and not for_reel:
            raise ValueError('Profile đang được lịch đăng Reel sử dụng. Dừng lượt đăng trước khi mở Chrome.')
        if profile_id in self.recipient_profiles and not for_recipient:
            raise ValueError('Dừng hoặc tiếp tục lượt nhận Page trước khi mở profile này.')
        if profile_id in self.outlook_profiles and not for_outlook:
            raise ValueError('Stop the Outlook sign-in before opening this profile.')
        if profile_id in self.facebook_profiles and not for_facebook:
            raise ValueError('Stop the Facebook check before opening this profile.')
        if for_automation:
            self.validate_automation_start(profile_id, for_facebook=for_facebook, for_outlook=for_outlook, for_recipient=for_recipient, for_reel=for_reel)
        else:
            self.validate_start(profile_id)
        if profile_id not in self.contexts and profile_id not in self.launches:
            self.errors.pop(profile_id, None)
            self.modes[profile_id] = 'automation' if for_automation else ('mobile' if self.mobile_enabled(profile_id) else 'manual')
            job = asyncio.create_task(self._launch(profile_id, open_start_page=open_start_page, for_automation=for_automation, for_facebook=for_facebook, for_reel=for_reel, timezone_id=timezone_id))
            self.launches[profile_id] = job
            def done(task):
                if self.launches.get(profile_id) is task:
                    self.launches.pop(profile_id, None)
                if not task.cancelled():
                    task.exception()  # consumed; error already sanitized in _launch
                self.state.emit()
            job.add_done_callback(done)
            self.state.emit()
        return self.status(profile_id)

    async def ensure_open(self, profile_id, *, for_automation=False, for_facebook=False, for_outlook=False, for_recipient=False, for_reel=False, timezone_id=None):
        self.request_start(profile_id, open_start_page=False, for_automation=for_automation, for_facebook=for_facebook, for_outlook=for_outlook, for_recipient=for_recipient, for_reel=for_reel, timezone_id=timezone_id)
        if profile_id in self.launches:
            await asyncio.shield(self.launches[profile_id])
        context = self.contexts.get(profile_id)
        if not context:
            raise RuntimeError(self.errors.get(profile_id, 'Browser is unavailable.'))
        return context

    async def _launch(self, profile_id, *, open_start_page=True, for_automation=False, for_facebook=False, for_reel=False, timezone_id=None):
        context, bridge, lock = None, None, None
        background = for_facebook or for_reel
        try:
            async with self.condition:
                while True:
                    if self.closing:
                        raise asyncio.CancelledError()
                    blocked = self.resources.blocked_reason(self.state.settings)
                    if len(self.contexts) + len(self.reserved) < self.state.settings.max_browsers and not blocked:
                        self.reserved.add(profile_id)
                        break
                    try:
                        await asyncio.wait_for(self.condition.wait(), timeout=1)
                    except TimeoutError:
                        pass
            self.validate_start(profile_id)
            with self.db.session() as session:
                profile = session.get(Profile, profile_id)
                path = Path(profile.profile_path).resolve()
                root = self.config.profiles_dir.resolve()
                if not path.is_relative_to(root) or path == root or path.is_symlink():
                    raise ValueError('Unsafe profile directory.')
                proxy = from_row(session.get(Proxy, profile.proxy_id)) if profile.proxy_id else None
                is_bm = session.get(BmProfile, profile_id) is not None
                routing = 'all' if is_bm else self.state.settings.proxy_routing
                browser_settings=session.get(BmBrowserSettings,profile_id) if is_bm else None
                mobile=bool(browser_settings and browser_settings.mobile_enabled)
            path.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(path / '.manager.lock'))
            lock.acquire(timeout=0)
            self.locks[profile_id] = lock
            options = None
            if proxy:
                if proxy.username or routing == 'facebook_only':
                    def proxy_error(message):
                        self.errors[profile_id] = message
                        self.state.log(message, profile_id, 'error')
                        self.state.emit()
                    bridge = BrowserProxyBridge(proxy, path, proxy_error, routing=routing)
                    options = await bridge.start()
                    self.bridges[profile_id] = bridge
                    self.state.log('Saved proxy authentication is handled by a private profile connection adapter.', profile_id)
                    if routing == 'facebook_only':
                        self.state.log('Facebook/Messenger domains use the assigned proxy; Outlook and other domains use the direct connection.', profile_id)
                else:
                    host = f'[{proxy.host}]' if ':' in proxy.host else proxy.host
                    options = {'server': f'{proxy.protocol}://{host}:{proxy.port}'}
            if is_bm:
                self.state.log('BM profile uses its dedicated proxy for all websites; direct fallback is disabled.', profile_id)
            self.state.emit()
            arguments = ['--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--proxy-bypass-list=<-loopback>'] if proxy else []
            deployment = self.config.authenticator_deployment
            if deployment:
                bundle_path = deployment['bundle_path']
                arguments += [f'--disable-extensions-except={bundle_path}', f'--load-extension={bundle_path}']
            if for_automation or background or mobile:
                emulation=bm_mobile_options(self.playwright) if mobile else {'viewport':None}
                context = await self.playwright.chromium.launch_persistent_context(
                    str(path), channel='chromium' if deployment else self.config.channel or None, headless=True if background else self.config.headless,
                    ignore_default_args=['--disable-extensions'],
                    proxy=options, service_workers='block' if for_automation or background else 'allow', timezone_id=timezone_id, timeout=self.state.settings.launch_timeout_seconds * 1000,
                    args=arguments, **emulation,
                )
            else:
                context = ManualBrowser(self.config, self.playwright, path, options, arguments,
                    lambda message: self.state.log(message, profile_id))
                await asyncio.wait_for(context.start(open_start_page), self.state.settings.launch_timeout_seconds)
            self._remember_context(profile_id, context)
            self.reserved.discard(profile_id)
            self.state.log('Reel schedule is running in a hidden Chrome session.' if for_reel else 'Facebook/Page check is running in a hidden Chrome session.' if for_facebook else 'Chrome opened with an isolated persistent profile.', profile_id)
            if for_automation or background or mobile:
                BrowserNetworkDiagnostics(context, lambda message: self.state.log(message, profile_id, 'warning'))
            if mobile:
                self.state.log('Chrome opened with mobile viewport, touch and mobile user agent.',profile_id)
                if open_start_page and not for_automation and not background:
                    target=pending_first_open(path) or FACEBOOK
                    page=context.pages[0] if context.pages else await context.new_page()
                    try:
                        await page.goto(target,wait_until='commit',timeout=45000)
                        await page.bring_to_front()
                    except (PlaywrightError,asyncio.TimeoutError):
                        self.state.log('Mobile Chrome is open; its start page could not finish loading. Check the assigned proxy or reopen the page.',profile_id,'warning')
                    else:
                        if target==STORE_URL:complete_first_open(path)
            return context
        except asyncio.CancelledError:
            if context:
                try:
                    await asyncio.wait_for(context.close(), 20)
                except Exception:
                    self._remember_context(profile_id, context)
            raise
        except Exception as error:
            if context:
                try:
                    await asyncio.wait_for(context.close(), 20)
                except Exception:
                    self._remember_context(profile_id, context)
            message = 'Profile directory is locked by another manager.' if isinstance(error, LockTimeout) else 'Chrome launch failed. Check browser installation, proxy configuration, and profile locks.'
            self.errors[profile_id] = message
            self.state.log(message, profile_id, 'error')
            raise RuntimeError(message) from None
        finally:
            self.reserved.discard(profile_id)
            if profile_id not in self.contexts:
                if bridge:
                    await bridge.close()
                    self.bridges.pop(profile_id, None)
                if lock and lock.is_locked:
                    lock.release()
                self.locks.pop(profile_id, None)
                self.modes.pop(profile_id, None)
            await self.wake()

    def _remember_context(self, profile_id, context):
        if self.contexts.get(profile_id) is context:
            return
        self.contexts[profile_id] = context
        context.on('close', lambda *_: asyncio.create_task(self._closed(profile_id, context)))

    async def wake(self):
        async with self.condition:
            self.condition.notify_all()
        self.state.emit()

    async def _closed(self, profile_id, context):
        if self.contexts.get(profile_id) is not context:
            return
        self.contexts.pop(profile_id, None)
        self.modes.pop(profile_id, None)
        bridge = self.bridges.pop(profile_id, None)
        if bridge:
            await bridge.close()
        lock = self.locks.pop(profile_id, None)
        if lock:
            lock.release()
        self.state.log('Chrome stopped; profile data was retained.', profile_id)
        await self.wake()

    async def stop(self, profile_id):
        pending = self.launches.get(profile_id)
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            self.launches.pop(profile_id, None)
        context = self.contexts.get(profile_id)
        if context:
            try:
                await asyncio.wait_for(context.close(), 20)
            except Exception:
                raise ValueError('Chrome did not close within 20 seconds; its profile lock is retained.') from None
            await self._closed(profile_id, context)
        self.errors.pop(profile_id, None)
        self.modes.pop(profile_id, None)
        self.state.emit()

    async def shutdown(self):
        self.closing = True
        await self.wake()
        await asyncio.gather(*(self.stop(pid) for pid in set(self.launches) | set(self.contexts)), return_exceptions=True)
        if self.playwright:
            await self.playwright.stop()


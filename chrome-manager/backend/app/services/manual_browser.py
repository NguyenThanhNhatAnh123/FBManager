"""Ordinary manual browser process, never connected to an automation protocol."""
import asyncio
import ctypes
from ctypes import wintypes
import os
import re
from pathlib import Path
import subprocess
from urllib.parse import urlsplit
from app.core.extension_bundle import EXTENSION_ID
from app.core.first_open import pending_first_open, complete_first_open

FACEBOOK = 'https://www.facebook.com/'
OUTLOOK = 'https://outlook.live.com/mail/'
BUSINESS_SETTINGS = 'https://business.facebook.com/settings/'
POPUP = f'chrome-extension://{EXTENSION_ID}/view/popup.html'
STORE = f'https://chromewebstore.google.com/detail/authenticator/{EXTENSION_ID}'


def request_window_close(pid):
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    @callback_type
    def visit(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)
        return True
    user32.EnumWindows(visit, 0)


def chrome_executable(config, playwright):
    if config.authenticator_deployment or not config.channel:
        return Path(playwright.chromium.executable_path)
    candidates = [Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
                  Path(os.environ['LOCALAPPDATA']) / 'Google/Chrome/Application/chrome.exe']
    return next((path for path in candidates if path.is_file()), None)


def launch_arguments(directory, proxy_options, arguments):
    result = [f'--user-data-dir={directory}', '--new-window', '--no-first-run', '--no-default-browser-check', '--disable-background-mode', *arguments]
    if proxy_options:
        # Credentials belong to the private adapter, never browser arguments.
        server = proxy_options['server']
        parsed = urlsplit(server)
        if parsed.username or parsed.password:
            raise ValueError('Browser arguments must not contain proxy credentials.')
        result.append('--proxy-server=' + server)
    return result + ['about:blank']


class ManualBrowser:
    is_manual = True

    def __init__(self, config, playwright, directory, proxy_options, arguments, log):
        self.config, self.directory, self.log = config, Path(directory), log
        self.executable = chrome_executable(config, playwright)
        self.arguments = launch_arguments(directory, proxy_options, arguments)
        self.process = self.monitor = None
        self.closed = False
        self.listeners = []
        self.service_workers = []  # No runtime inspection of manual profile/account data.

    async def start(self, open_start_page):
        if not self.executable or not self.executable.is_file():
            raise ValueError('Configured browser executable was not found.')
        self.process = await asyncio.create_subprocess_exec(str(self.executable), *self.arguments,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        self.monitor = asyncio.create_task(self._watch())
        # Let Chrome finish normal startup and first-install extension registration
        # before forwarding the requested active tab to its existing process.
        await asyncio.sleep(.8)
        if self.config.authenticator_deployment:
            from app.services.extensions import inspect_authenticator
            for _ in range(24):
                if self.closed:
                    break
                info = inspect_authenticator(self.directory, self.config.profiles_dir,
                    bundle_directory=self.config.authenticator_deployment['bundle_path'])
                if info['status'] in ('Installed', 'Verified', 'Disabled'):
                    break
                await asyncio.sleep(.25)
            await asyncio.sleep(.3)
        if self.closed:
            raise ValueError('Chrome exited before startup completed. Check the profile lock.')
        if open_start_page:
            target=pending_first_open(self.directory) or FACEBOOK
            await self.open_url(target)
            if target==STORE:
                complete_first_open(self.directory)
                self.log('The requested one-time Authenticator Web Store page opened in this profile.')
            else:
                self.log('Facebook opened in a manual Chrome window.')
        return self

    def on(self, event, callback):
        if event == 'close':
            self.listeners.append(callback)
            if self.closed:
                callback(self)

    async def _watch(self):
        await self.process.wait()
        self.closed = True
        for callback in tuple(self.listeners):
            callback(self)
        self.listeners.clear()

    async def open_url(self, url):
        # Only fixed product destinations, never user-supplied command/script URLs.
        business=bool(re.fullmatch(r'https://business\.facebook\.com/settings/(?:\?business_id=[1-9][0-9]{0,29})?',url))
        if url not in (FACEBOOK, OUTLOOK, POPUP, STORE) and not business:
            raise ValueError('Unsupported manual browser destination.')
        if self.closed:
            raise ValueError('The manual browser is closed.')
        command = await asyncio.create_subprocess_exec(str(self.executable), f'--user-data-dir={self.directory}',
            '--new-tab', url, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        if await asyncio.wait_for(command.wait(), 15):
            raise ValueError('Chrome could not open the requested tab.')

    async def close(self):
        if self.process and self.process.returncode is None:
            request_window_close(self.process.pid)
            await self.process.wait()
        if self.monitor:
            await self.monitor

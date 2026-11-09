import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
from app.core.extension_bundle import load_deployment

ROOT = Path(__file__).resolve().parents[3]
load_dotenv(ROOT / '.env')

@dataclass
class Config:
    root: Path = ROOT
    data_dir: Path = field(default_factory=lambda: ROOT / os.getenv('CM_DATA_DIR', 'data'))
    channel: str = field(default_factory=lambda: os.getenv('CM_BROWSER_CHANNEL', 'chrome'))
    headless: bool = field(default_factory=lambda: os.getenv('CM_HEADLESS', 'false').lower() == 'true')
    ip_check_url: str = field(default_factory=lambda: os.getenv('CM_IP_CHECK_URL', 'https://api64.ipify.org?format=json'))
    port: int = field(default_factory=lambda: int(os.getenv('CM_PORT', '8000')))

    def __post_init__(self):
        if self.channel not in ('chrome', ''):
            raise ValueError('CM_BROWSER_CHANNEL must be chrome or empty for Chromium.')
        if not 1024 <= self.port <= 65535:
            raise ValueError('CM_PORT must be between 1024 and 65535.')
        self.authenticator_deployment = load_deployment(self.root, self.data_dir)
        if self.authenticator_deployment:
            # Only this Manager process uses this private runtime directory.
            self.channel = ''
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str((self.root / '.browsers').resolve())

    def ip_check_url_for(self, ip_version):
        if ip_version == 'ipv6':
            return os.getenv('CM_IPV6_CHECK_URL', 'https://api6.ipify.org?format=json')
        if ip_version == 'ipv4':
            return os.getenv('CM_IPV4_CHECK_URL', 'https://api.ipify.org?format=json')
        return self.ip_check_url

    @property
    def profiles_dir(self):
        return self.root / 'profiles'

    @property
    def screenshots_dir(self):
        return self.root / 'screenshots'

    def prepare(self):
        for directory in (self.data_dir, self.profiles_dir, self.screenshots_dir, self.root / 'logs'):
            directory.mkdir(parents=True, exist_ok=True)

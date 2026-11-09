"""One explicit code/resource allowlist shared by packaging and installation."""
from pathlib import Path,PurePosixPath
import re
import stat
import unicodedata

SKILLS = frozenset(('page-shirt-zoom-8s','imagegen','realistic-shirt-photos-9x16',
    'realistic-shirt-photos-closeup-9x16','ao-cao-cap','batch-remove-background','us-video-review'))
DENIED_PARTS = frozenset(('data','profiles','workspaces','private-backups','.install-backups','.runtime','.venv','node_modules',
    '__pycache__','.pytest_cache','tests','test','runs','logs','screenshots','.git','.browsers'))
EXACT_FILES = frozenset((
    'chrome-manager/backend/requirements.txt','chrome-manager/backend/run.py',
    'chrome-manager/.env.example',
    'meta-page-manager/requirements.txt','meta-page-manager/requirements-producer.txt',
    'meta-page-manager/backend/run.py','meta-page-manager/.env.example',
    'via-auth/manage_users.py','via-auth/setup-users.bat','via-auth/README.md',
    'via-mcp/server.py','via-mcp/remote-server.py','via-mcp/smoke.py','via-mcp/requirements.txt',
    'via-mcp/connect-codex.ps1','via-mcp/connect-codex.bat',
    'page-producer/VENDORED-MANIFEST.json','page-producer/page-manager-bridge/bridge.py',
    'page-producer/page-manager-bridge/README.md','chrome-manager/shared/recovery_readers.json',
    'integration-diagnostics/verify_zoom_camera.py','integration-diagnostics/codex_mcp_probe.py',
    'integration-diagnostics/check_producer_profile.py','integration-diagnostics/check_zoom_renderer.py',
    'integration-diagnostics/full_flow_readcheck.py','integration-diagnostics/reload_snapshot.py',
    'tools/production_backup.py','tools/ffmpeg/bin/ffmpeg.exe','tools/ffmpeg/bin/ffprobe.exe',
    'tools/ffmpeg/LICENSE','tools/ffmpeg/README.txt',
    'page-producer/README.md',
))
DEPLOYMENT_FILES=frozenset(('install.py','package_policy.py','check-processes.ps1','install-server.ps1',
    'server-config.example.json','requirements-server.lock','start-server.ps1','start-server.bat',
    'start_server.py','workspaces.py','preflight.py','README.md','MIGRATE-8040.md'))
REQUIRED_FILES = frozenset((
    'chrome-manager/backend/run.py','chrome-manager/backend/app/main.py',
    'chrome-manager/frontend/dist/index.html','meta-page-manager/backend/page_manager/app.py',
    'meta-page-manager/frontend/index.html','meta-page-manager/frontend/fonts.css','via-auth/via_auth/store.py',
    'via-auth/via_auth/middleware.py','via-auth/via_auth/access_tokens.py','via-auth/via_auth/rest.py','via-auth/via_auth/static/app.js',
    'via-mcp/server.py','via-mcp/remote-server.py','via-mcp/via_mcp/remote_app.py','via-mcp/via_mcp/dashboard_proxy.py',
    'page-producer/page-manager-bridge/bridge.py','tools/ffmpeg/bin/ffmpeg.exe',
    'tools/ffmpeg/bin/ffprobe.exe','integration-diagnostics/verify_zoom_camera.py',
    'deployment/install-server.ps1','deployment/install.py','deployment/requirements-server.lock',
    'deployment/server-config.example.json','deployment/start-server.ps1',
)) | frozenset('page-producer/.agents/skills/'+name+'/SKILL.md' for name in SKILLS) | frozenset('deployment/'+name for name in DEPLOYMENT_FILES) | frozenset(
    f'meta-page-manager/frontend/fonts/geist-{subset}-wght-normal.woff2' for subset in ('latin','latin-ext','vietnamese'))


def validate_windows_component(value):
    """Reject aliases Win32 normalizes before resolving a lexical Path boundary."""
    if (not isinstance(value,str) or not value or value in ('.','..') or value.endswith((' ','.'))
        or any(character in '<>:"/\\|?*' or unicodedata.category(character)=='Cc' for character in value)):
        raise ValueError('unsafe_windows_path_component')
    device=value.split('.',1)[0].rstrip(' ').upper()
    if device in ('CON','PRN','AUX','NUL','CONIN$','CONOUT$','CLOCK$') or re.fullmatch(r'(?:COM|LPT)[0-9\u00b9\u00b2\u00b3]',device):
        raise ValueError('unsafe_windows_path_component')
    return value


def validate_filesystem_components(path):
    path=Path(path)
    if path.anchor.startswith('\\\\'):
        # A UNC anchor contains both server and share components; Path.parts
        # otherwise presents them as one anchor and would skip their validation.
        for component in path.anchor.strip('\\/').split('\\'):
            validate_windows_component(component)
    for component in path.parts:
        if component==path.anchor:continue
        validate_windows_component(component)
    return path


def normalized_path(value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('invalid_package_path')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' in path.parts or path.as_posix()!=value:
        raise ValueError('invalid_package_path')
    for component in path.parts:validate_windows_component(component)
    return path


def source_file_allowed(value):
    try:path=normalized_path(value)
    except ValueError:return False
    parts=path.parts
    if any(part.casefold() in DENIED_PARTS for part in parts):return False
    if any(part.startswith('.env') and part!='.env.example' for part in parts):return False
    if path.suffix.casefold() in ('.db','.sqlite','.sqlite3','.pyc','.zip','.log','.bak','.key','.pem'):
        return False
    if value in EXACT_FILES:return True
    if value.startswith('deployment/'):
        return len(parts)==2 and path.name in DEPLOYMENT_FILES
    if value.startswith(('chrome-manager/backend/app/','meta-page-manager/backend/page_manager/')):
        return path.suffix.casefold() in ('.py','.js')
    if value.startswith(('chrome-manager/scripts/','meta-page-manager/scripts/')):
        return path.suffix.casefold() in ('.py','.cjs')
    if value.startswith('chrome-manager/frontend/dist/'):
        return path.suffix.casefold() in ('.html','.js','.css','.woff','.woff2','.ttf','.svg','.png','.ico')
    if value.startswith('meta-page-manager/frontend/'):
        return path.suffix.casefold() in ('.html','.js','.css','.woff','.woff2','.ttf','.svg','.png','.ico','.txt')
    if value.startswith('via-auth/via_auth/'):
        return path.suffix.casefold() in ('.py','.html','.js','.css')
    if value.startswith('via-mcp/via_mcp/'):
        return path.suffix.casefold()=='.py'
    if len(parts)>4 and parts[:3]==('page-producer','.agents','skills') and parts[3] in SKILLS:
        return path.suffix.casefold() in ('.md','.py','.json','.yaml','.yml','.png','.jpg','.jpeg','.webp','.svg','.txt')
    if value.startswith('page-producer/assets/shirt-style-references/'):
        return path.suffix.casefold() in ('.png','.jpg','.jpeg','.webp','.txt')
    return False


def check_regular(path):
    validate_filesystem_components(path)
    for parent in path.parents:
        details=parent.lstat()
        if (stat.S_ISLNK(details.st_mode) or getattr(details,'st_file_attributes',0)&0x400
            or not stat.S_ISDIR(details.st_mode)):
            raise ValueError('package_redirect_or_link_rejected')
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or path.is_symlink()
        or getattr(info,'st_file_attributes',0)&0x400):
        raise ValueError('package_redirect_or_link_rejected')
    return info

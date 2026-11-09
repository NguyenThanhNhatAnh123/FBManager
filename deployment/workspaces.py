"""Stable user-to-core bindings and offline code-only workspace preparation."""

from contextlib import ExitStack, closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

from .install import InstallError, absolute, digest, lock_existing, safe_path
from .package_policy import DENIED_PARTS, EXACT_FILES, check_regular, source_file_allowed


REGISTRY = 'via-auth/data/workspaces.json'
SUPERVISOR_LOCK = 'via-auth/data/workspaces.lock'
MAX_USERS = 32
FIRST_SECONDARY_PORT = 8051
CORE_LOCKS = ('chrome-manager/data/manager.lock', 'meta-page-manager/data/page-manager.lock')
CODE_DIRECTORIES = (
    'chrome-manager/backend', 'chrome-manager/scripts', 'chrome-manager/frontend/dist',
    'chrome-manager/shared', 'meta-page-manager/backend', 'meta-page-manager/frontend',
    'meta-page-manager/scripts', 'via-auth/via_auth', 'page-producer/.agents/skills',
    'page-producer/assets/shirt-style-references', 'page-producer/page-manager-bridge',
    'tools/ffmpeg',
)
CODE_PREFIXES = ('chrome-manager/', 'meta-page-manager/', 'via-auth/via_auth/',
                 'page-producer/', 'integration-diagnostics/', 'tools/ffmpeg/')
CORE_FILES = frozenset(('chrome-manager/backend/run.py', 'chrome-manager/scripts/startup_state.py',
    'chrome-manager/frontend/dist/index.html', 'meta-page-manager/backend/page_manager/app.py',
    'meta-page-manager/frontend/index.html', 'via-auth/via_auth/store.py'))


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate registry key')
        value[key] = item
    return value


def _valid_id(value):
    return type(value) is int and 1 <= value <= 2**63 - 1


def _valid_port(value):
    return type(value) is int and 1024 <= value <= 65535


def _validate(root, manager_port, document):
    if (not _valid_port(manager_port) or not isinstance(document, dict)
            or set(document) != {'version', 'workspaces'} or type(document['version']) is not int
            or document['version'] != 1 or not isinstance(document['workspaces'], list)
            or not 1 <= len(document['workspaces']) <= MAX_USERS):
        raise InstallError('invalid_workspace_registry')
    bindings, ports, primary = {}, set(), 0
    for entry in document['workspaces']:
        if not isinstance(entry, dict) or set(entry) != {'user_id', 'manager_port', 'root'}:
            raise InstallError('invalid_workspace_registry')
        user_id, port, relative = entry['user_id'], entry['manager_port'], entry['root']
        if not _valid_id(user_id) or user_id in bindings or not _valid_port(port) or port in ports:
            raise InstallError('invalid_workspace_registry')
        if relative == '.':
            primary += 1
            if port != manager_port:
                raise InstallError('primary_workspace_port_mismatch')
        elif relative != f'workspaces/user-{user_id}':
            raise InstallError('invalid_workspace_registry')
        directory = safe_path(root / relative, root)
        if directory.exists() and not directory.is_dir():
            raise InstallError('invalid_workspace_directory')
        bindings[user_id] = {'manager_port': port, 'root': relative}
        ports.add(port)
    if primary != 1:
        raise InstallError('invalid_workspace_registry')
    return bindings


def load_workspaces(root, manager_port):
    """Read the published registry. Missing, redirected or malformed files fail closed."""
    root = absolute(root)
    path = safe_path(root / REGISTRY, root)
    try:
        check_regular(path)
        with path.open('rb') as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError('registry too large')
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        raise InstallError('workspace_registry_missing') from None
    except (OSError, ValueError, RecursionError):
        raise InstallError('invalid_workspace_registry') from None
    return _validate(root, manager_port, document)


def _identity_rows(root):
    path = safe_path(root / 'via-auth/data/identity.sqlite3', root)
    for suffix in ('-wal', '-shm'):
        safe_path(path.with_name(path.name + suffix), root)
    try:
        check_regular(path)
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT id, role, active FROM users ORDER BY id LIMIT ?', (MAX_USERS + 1,)).fetchall()
    except (OSError, ValueError, sqlite3.Error):
        raise InstallError('workspace_identities_unavailable') from None
    if (not 1 <= len(rows) <= MAX_USERS or any(not _valid_id(user_id)
            or role not in ('admin', 'operator') or type(active) is not int or active not in (0, 1)
            for user_id, role, active in rows)):
        raise InstallError('invalid_workspace_identities')
    return rows


def _document(bindings):
    return {'version': 1, 'workspaces': [dict(user_id=user_id, **binding)
        for user_id, binding in sorted(bindings.items())]}


def plan_workspaces(root, manager_port, gateway_port):
    """Propose initial bindings without creating data; retain all published bindings thereafter."""
    root = absolute(root)
    if not _valid_port(manager_port) or not _valid_port(gateway_port) or manager_port == gateway_port:
        raise InstallError('invalid_workspace_ports')
    rows = _identity_rows(root)
    registry = safe_path(root / REGISTRY, root)
    if registry.exists():
        bindings = load_workspaces(root, manager_port)
        if set(bindings) != {row[0] for row in rows}:
            raise InstallError('workspace_identity_mapping_changed')
        if any(entry['manager_port'] == gateway_port for entry in bindings.values()):
            raise InstallError('workspace_gateway_port_conflict')
        for binding in bindings.values():
            if not safe_path(root / binding['root'], root).is_dir():
                raise InstallError('existing_workspace_directory_missing')
        return bindings
    primary = next((user_id for user_id, role, active in rows if role == 'admin' and active), None)
    if primary is None:
        raise InstallError('workspace_primary_admin_required')
    bindings, port = {}, FIRST_SECONDARY_PORT
    for user_id, _, _ in rows:
        if user_id == primary:
            bindings[user_id] = {'manager_port': manager_port, 'root': '.'}
            continue
        while port in (manager_port, gateway_port):
            port += 1
        relative = f'workspaces/user-{user_id}'
        if safe_path(root / relative, root).exists():
            raise InstallError('unregistered_workspace_directory_exists')
        bindings[user_id] = {'manager_port': port, 'root': relative}
        port += 1
    return _validate(root, manager_port, _document(bindings))


def _code_files(root):
    paths = set()
    for name in EXACT_FILES:
        if name.startswith(CODE_PREFIXES) and not Path(name).name.startswith('.env'):
            path = safe_path(root / name, root)
            if path.exists():
                paths.add(path)
    for relative in CODE_DIRECTORIES:
        base = safe_path(root / relative, root)
        if not base.exists():
            continue
        if not base.is_dir():
            raise InstallError('invalid_workspace_code_directory')
        for folder, directories, files in os.walk(base, followlinks=False):
            directories[:] = [name for name in directories if name.casefold() not in DENIED_PARTS]
            for name in directories:
                safe_path(Path(folder) / name, root)
            paths.update(Path(folder) / name for name in files)
    selected = {}
    for path in sorted(paths):
        name = path.relative_to(root).as_posix()
        if source_file_allowed(name):
            check_regular(path)
            selected[name] = path
    if not CORE_FILES <= selected.keys():
        raise InstallError('workspace_core_files_missing')
    return selected


def _protect(path):
    auth_package = Path(__file__).resolve().parents[1] / 'via-auth'
    if str(auth_package) not in sys.path:
        sys.path.insert(0, str(auth_package))
    from via_auth.store import protect_private
    protect_private(path)


def prepare_workspaces(root, manager_port, gateway_port, *, protect=_protect):
    """Copy only allowlisted code/resources, with rollback on an interrupted preparation.

    The caller holds SUPERVISOR_LOCK and has confirmed that the managed ports are idle.
    Existing data files and settings are outside the copy plan.
    """
    root = absolute(root)
    bindings = plan_workspaces(root, manager_port, gateway_port)
    registry = safe_path(root / REGISTRY, root)
    is_new = not registry.exists()
    secondary = [safe_path(root / entry['root'], root) for entry in bindings.values() if entry['root'] != '.']
    files = _code_files(root) if secondary else {}
    fingerprints = {name: digest(source) for name, source in files.items()}
    parent = safe_path(root / 'workspaces', root)
    if not parent.exists():
        parent.mkdir()
        protect(parent)

    with ExitStack() as stack:
        for workspace in (root, *secondary):
            for relative in CORE_LOCKS:
                stack.enter_context(lock_existing(safe_path(workspace / relative, root)))
        staging = Path(stack.enter_context(TemporaryDirectory(prefix='.prepare-', dir=parent))).absolute()
        if not safe_path(staging, parent).resolve().is_relative_to(parent.resolve()):
            raise InstallError('invalid_workspace_staging_directory')
        protect(staging)
        changes, created_dirs, applied = [], [], []

        def ensure_directory(directory):
            if directory.is_dir():
                return
            ensure_directory(directory.parent)
            directory.mkdir()
            created_dirs.append(directory)

        try:
            for workspace in secondary:
                for name, source in files.items():
                    target = safe_path(workspace / name, root)
                    if target.exists():
                        check_regular(target)
                        if digest(target) == fingerprints[name]:
                            continue
                    staged = staging / 'new' / workspace.name / name
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, staged)
                    if digest(staged) != fingerprints[name]:
                        raise InstallError('workspace_source_changed_during_copy')
                    previous = None
                    if target.exists():
                        previous = staging / 'previous' / workspace.name / name
                        previous.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(target, previous)
                    changes.append((target, staged, previous))
            for workspace in secondary:
                if not workspace.exists():
                    ensure_directory(workspace)
                    protect(workspace)
                for name in ('media/ideas', 'media/videos', 'media/audio', 'mcp-media'):
                    ensure_directory(safe_path(workspace / name, root))
            for target, staged, previous in changes:
                safe_path(target, root)
                ensure_directory(target.parent)
                staged.replace(target)
                applied.append((target, previous))
            if is_new:
                staged_registry = staging / 'workspaces.json'
                staged_registry.write_text(json.dumps(_document(bindings), indent=2) + '\n', encoding='utf-8')
                protect(staged_registry)
                if registry.exists():
                    raise InstallError('workspace_registry_created_concurrently')
                staged_registry.rename(registry)
        except BaseException:
            for target, previous in reversed(applied):
                safe_path(target, root)
                if previous is None:
                    target.unlink()
                else:
                    previous.replace(target)
            for directory in reversed(created_dirs):
                try:
                    safe_path(directory, root).rmdir()
                except OSError:
                    pass
            raise
    return bindings

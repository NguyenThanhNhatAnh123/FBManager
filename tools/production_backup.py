"""Private, verifiable Via snapshots. Never restores or starts/stops applications.

Run with chrome-manager's Python environment. DB-only snapshots can be made online;
file snapshots require an explicit offline attestation and available manager locks.
"""
import argparse
from contextlib import ExitStack, closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import time
from uuid import uuid4

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))
sys.path.insert(0, str(CODE_ROOT / 'via-auth'))
from deployment.start_server import read_config
from deployment.workspaces import CORE_LOCKS, REGISTRY, SUPERVISOR_LOCK, load_workspaces

DATA_ROOTS = ('chrome-manager/data', 'meta-page-manager/data', 'via-mcp/data', 'via-auth/data')
EXPECTED_DATABASES = (
    'chrome-manager/data/manager.sqlite3', 'meta-page-manager/data/page-manager.sqlite3',
    'via-mcp/data/operations.sqlite3', 'via-auth/data/identity.sqlite3',
)
FILE_ROOTS = (*DATA_ROOTS, 'chrome-manager/profiles', 'chrome-manager/screenshots',
              'chrome-manager/reel-assets', 'chrome-manager/page-assets',
              'chrome-manager/.runtime/extensions/authenticator',
              'chrome-manager/.runtime/page-recipient',
              'meta-page-manager/reel-assets', 'meta-page-manager/.runtime/audio-mixes',
              'media', 'mcp-media', 'page-producer/page-manager-bridge/runs')
CONFIG_FILES = ('chrome-manager/.env', 'meta-page-manager/.env',
                'meta-page-manager/.runtime/producer-capability-evidence.json',
                'meta-page-manager/.runtime/producer-image-capability.json',
                'meta-page-manager/.runtime/page-video-agent-readiness.json')
PROOF_REFERENCE_ROOTS = ('page-producer/.runtime',
                         'integration-diagnostics/.runtime/producer-pilot/image-probe')
ROOT_MARKER = '.via-backup-root'
DEFAULT_MAX_BYTES = 100 * 1024 ** 3
DEFAULT_MAX_FILES = 100_000
RESTORE_NOTES = [
    'This snapshot is not a complete machine backup and does not perform a restore.',
    'Restore the matching reviewed application release, Python/Node runtimes, extensions and dependencies separately.',
    'Restore all selected databases together while both managers, MCP, producers and managed browsers are stopped.',
    'Restore profiles, media, producer run evidence, configuration and local-service.key from an offline file snapshot.',
    'Windows DPAPI secrets and Chrome encrypted data may require the original Windows account and machine; files alone do not guarantee decryption.',
    'Reconcile absolute paths and revoke old identity sessions after a restore before exposing network access.',
    'Capability/readiness records are evidence only; rerun readiness checks and reconcile external posting state before restarting schedulers.',
    'External/custom data paths and historical directories outside this workspace are not copied; inventory them separately.',
    'Prove recovery on an isolated machine before replacing live data or allowing scheduled publication.',
]


class BackupError(Exception):
    pass


def protect_private(path):
    # Verification also runs before the destination has installed Via's runtime.
    from via_auth.store import protect_private as protect
    protect(path)


def absolute(path):
    return Path(os.path.abspath(os.fspath(path)))


def safe_path(path, root):
    """Reject filesystem redirects before resolving them, including Windows junctions."""
    path, root = absolute(path), absolute(root)
    if not path.is_relative_to(root):
        raise BackupError('path_outside_workspace')
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BackupError('filesystem_redirect_rejected')
        if part == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise BackupError('hardlinked_file_rejected')
        if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
            raise BackupError('special_file_rejected')
    return path


def files_under(directory, workspace):
    directory = safe_path(directory, workspace)
    if not directory.exists():
        return
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            child = safe_path(entry.path, workspace)
            if child.is_dir():
                yield from files_under(child, workspace)
            else:
                yield child


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as file:
        while block := file.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def sqlite_snapshot(source, target, max_bytes):
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src:
        src.execute('PRAGMA query_only=ON')
        if src.execute('PRAGMA page_count').fetchone()[0] * src.execute('PRAGMA page_size').fetchone()[0] > max_bytes:
            raise BackupError('backup_resource_limit')
        started = time.monotonic()
        def progress(_status, _remaining, _total):
            if time.monotonic() - started > 120:
                raise BackupError('sqlite_backup_timeout')
            if target.exists() and target.stat().st_size > max_bytes:
                raise BackupError('backup_resource_limit')
        with closing(sqlite3.connect(target, timeout=10)) as dst:
            src.backup(dst, pages=256, progress=progress, sleep=0.05)
            dst.execute('PRAGMA journal_mode=DELETE')
            if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise BackupError('sqlite_integrity_failed')


def secondary_workspaces(workspace):
    if not safe_path(workspace / REGISTRY, workspace).exists():
        parent = safe_path(workspace / 'workspaces', workspace)
        if parent.is_dir() and any(item.name.startswith('user-') for item in parent.iterdir()):
            raise BackupError('workspace_registry_missing')
        return ()
    try:
        config = read_config(workspace)
        bindings = load_workspaces(workspace, config['manager_port'])
        secondary = tuple(binding['root'] for binding in bindings.values() if binding['root'] != '.')
        if any(binding['manager_port'] == config['port'] for binding in bindings.values()):
            raise ValueError('workspace port conflict')
        if any(not safe_path(workspace / relative, workspace).is_dir() for relative in secondary):
            raise ValueError('missing workspace data directory')
        return secondary
    except Exception:
        raise BackupError('workspace_registry_unavailable') from None


def _windows_venv_redirector(psutil):
    """Identify the exact venv executable that is waiting for this backup."""
    if os.name != 'nt' or sys.prefix == sys.base_prefix:
        return None
    try:
        executable = Path(sys.executable).resolve(strict=True)
        if executable != Path(sys.prefix).resolve() / 'Scripts' / 'python.exe':
            return None
        current = psutil.Process()
        if Path(current.exe()).resolve(strict=True) != Path(sys._base_executable).resolve(strict=True):
            return None
        parent = current.parent()
        if parent is None or Path(parent.exe()).resolve(strict=True) != executable:
            return None
        argv = parent.cmdline()
        if (argv and Path(parent.cwd(), argv[0]).resolve(strict=True) == executable
                and argv[1:] == sys.orig_argv[1:]):
            return parent
    except (OSError, RuntimeError, psutil.Error):
        pass
    return None


@contextmanager
def offline_guard(workspace, secondary=()):
    try:
        import psutil
        from filelock import FileLock, Timeout
    except ImportError:
        raise BackupError('use_manager_python_environment') from None
    with ExitStack() as locks:
        paths = (SUPERVISOR_LOCK, *CORE_LOCKS, 'via-mcp/data/remote-server.lock',
                 *(f'{root}/{relative}' for root in secondary for relative in CORE_LOCKS))
        for relative in paths:
            lock = safe_path(workspace / relative, workspace)
            if lock.parent.is_dir():
                try:
                    locks.enter_context(FileLock(str(lock), timeout=0))
                except Timeout:
                    raise BackupError('manager_is_running') from None
        # Locking prevents a manager restart. Producers, MCP and Chrome have their
        # own lifecycles, so also require an offline attestation and inspect visible
        # process command lines. This is not a machine-wide process guarantee.
        prefix = str(workspace).casefold()
        redirector = _windows_venv_redirector(psutil)
        for process in psutil.process_iter(['name', 'cmdline']):
            if process.pid == os.getpid() or process == redirector:
                continue
            try:
                command = ' '.join(process.info['cmdline'] or []).casefold()
                name = (process.info['name'] or '').casefold()
                relevant = any(item in name for item in ('python', 'node', 'chrome', 'chromium', 'codex'))
                if relevant and prefix in command:
                    raise BackupError('workspace_process_is_running')
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        yield


def prepare_output(output, workspace):
    output = safe_path(output, workspace)
    excluded = [workspace / item for item in (*FILE_ROOTS, 'workspaces', 'LanguageCheck')]
    service_roots = {workspace / item for item in ('chrome-manager', 'meta-page-manager', 'via-mcp', 'via-auth', 'page-producer', 'tools')}
    if output == workspace or output in service_roots or any(output == item or output.is_relative_to(item) for item in excluded):
        raise BackupError('output_overlaps_source')
    marker = safe_path(output / ROOT_MARKER, workspace)
    if output.exists():
        if not marker.is_file() and any(output.iterdir()):
            raise BackupError('output_root_must_be_empty_or_owned')
        if marker.exists() and marker.read_text(encoding='ascii') != 'via-private-backups-v1\n':
            raise BackupError('invalid_backup_root')
    output.mkdir(parents=True, exist_ok=True)
    protect_private(output)
    if not marker.exists():
        marker.write_text('via-private-backups-v1\n', encoding='ascii')
    protect_private(marker)
    return output


def create_backup(workspace, output, *, include_files=False, offline=False,
                  max_bytes=DEFAULT_MAX_BYTES, max_files=DEFAULT_MAX_FILES):
    workspace = safe_path(workspace, workspace)
    if not workspace.is_dir() or max_bytes < 1 or not 1 <= max_files <= DEFAULT_MAX_FILES:
        raise BackupError('invalid_backup_options')
    if include_files and not offline:
        raise BackupError('file_snapshot_requires_offline')
    secondary = secondary_workspaces(workspace)
    registry_present = safe_path(workspace / REGISTRY, workspace).is_file()
    data_roots = (*DATA_ROOTS, *(f'{root}/{name}' for root in secondary for name in DATA_ROOTS))
    file_roots = (*FILE_ROOTS, *(f'{root}/{name}' for root in secondary for name in FILE_ROOTS))
    configs = (*CONFIG_FILES, *(f'{root}/{name}' for root in secondary for name in CONFIG_FILES))
    if registry_present:
        configs += ('deployment/server-config.json',)
    proof_roots = (*PROOF_REFERENCE_ROOTS,
                   *(f'{root}/{name}' for root in secondary for name in PROOF_REFERENCE_ROOTS))
    expected = (*EXPECTED_DATABASES, *(f'{root}/{name}' for root in secondary for name in EXPECTED_DATABASES[:2]))
    output = prepare_output(output, workspace)
    snapshot = output / ('via-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid4().hex[:12])
    snapshot.mkdir(exist_ok=False)
    protect_private(snapshot)
    manifest = {
        'version': 1, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'kind': 'databases-and-listed-files' if include_files else
                'databases-and-workspace-registry' if registry_present else 'databases-only',
        'full_backup': False, 'offline_attested': bool(offline),
        'consistency': 'offline-attested-with-manager-locks' if offline else 'independent-online-SQLite-snapshots',
        'entries': [], 'missing_expected_databases': [],
        'file_resources_included': bool(include_files),
        'file_resource_roots': list(file_roots),
        'configuration_files': [*configs, *((REGISTRY,) if registry_present else ())],
        'missing_file_resource_roots': [item for item in file_roots if not safe_path(workspace / item, workspace).exists()],
        'proof_reference_roots': list(proof_roots),
        'unresolved_proof_references': 0,
        'restore_notes': RESTORE_NOTES,
    }
    used_bytes = 0
    seen = set()

    def add(source, kind):
        nonlocal used_bytes
        relative = source.relative_to(workspace).as_posix()
        if relative in seen:
            return
        seen.add(relative)
        if len(seen) > max_files or used_bytes + source.stat().st_size > max_bytes:
            raise BackupError('backup_resource_limit')
        target = snapshot / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == 'sqlite':
            sqlite_snapshot(source, target, max_bytes - used_bytes)
        else:
            before = source.stat()
            with source.open('rb') as src, target.open('xb') as dst:
                count = 0
                while block := src.read(1024 * 1024):
                    count += len(block)
                    if used_bytes + count > max_bytes:
                        raise BackupError('backup_resource_limit')
                    dst.write(block)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                raise BackupError('source_changed_during_copy')
        # New files inherit the already-private root ACL. Do not copy source ACLs
        # or metadata that could reintroduce broad grants.
        used_bytes += target.stat().st_size
        if used_bytes > max_bytes:
            raise BackupError('backup_resource_limit')
        manifest['entries'].append({'path': relative, 'kind': kind, 'bytes': target.stat().st_size, 'sha256': digest(target)})

    with ExitStack() as guard:
        if offline:
            guard.enter_context(offline_guard(workspace, secondary))
            if secondary_workspaces(workspace) != secondary:
                raise BackupError('workspace_registry_changed_during_backup')
        for relative in expected:
            if not safe_path(workspace / relative, workspace).is_file():
                manifest['missing_expected_databases'].append(relative)
        for relative in data_roots:
            for source in files_under(workspace / relative, workspace):
                if source.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
                    add(source, 'sqlite')
        if not manifest['entries']:
            raise BackupError('no_databases_found')
        if registry_present:
            add(safe_path(workspace / REGISTRY, workspace), 'file')
            if not include_files:
                add(safe_path(workspace / 'deployment/server-config.json', workspace), 'file')
        if include_files:
            for relative in file_roots:
                for source in files_under(workspace / relative, workspace):
                    if source.name.endswith(('-wal', '-shm', '-journal', '.lock')):
                        continue
                    add(source, 'file')
            for relative in configs:
                source = safe_path(workspace / relative, workspace)
                if source.is_file():
                    add(source, 'file')
                    # Capability metadata may bind one precise provenance JSON or
                    # preflight image outside the canonical run directory. Copy
                    # only those named artifacts, not arbitrary .runtime caches.
                    if source.suffix != '.json' or source.stat().st_size > 2 * 1024 * 1024:
                        continue
                    try:
                        record = json.loads(source.read_text(encoding='utf-8'))
                    except (ValueError, UnicodeError):
                        manifest['unresolved_proof_references'] += 1
                        continue
                    if not isinstance(record, dict):
                        continue
                    references = [record.get('image_path')]
                    references += [record.get(key, {}).get('path') for key in ('source', 'image', 'video', 'native_generation')
                                   if isinstance(record.get(key), dict)]
                    allowed = [workspace / item for item in (*file_roots, *proof_roots)]
                    for raw in references:
                        if not isinstance(raw, str) or not raw:
                            continue
                        reference = absolute(raw)
                        if not any(reference.is_relative_to(item) for item in allowed):
                            manifest['unresolved_proof_references'] += 1
                            continue
                        reference = safe_path(reference, workspace)
                        if reference.is_file():
                            add(reference, 'file')
                        else:
                            manifest['unresolved_proof_references'] += 1
    manifest['total_bytes'] = used_bytes
    manifest_path = snapshot / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding='utf-8')
    protect_private(manifest_path)
    verify_backup(snapshot, workspace, max_bytes=max_bytes)
    return snapshot, manifest


def verify_backup(snapshot, workspace, *, max_bytes=DEFAULT_MAX_BYTES):
    workspace, snapshot = absolute(workspace), safe_path(snapshot, workspace)
    manifest_path = safe_path(snapshot / 'manifest.json', workspace)
    if not manifest_path.is_file() or manifest_path.stat().st_size > 32 * 1024 * 1024:
        raise BackupError('invalid_manifest')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise BackupError('invalid_manifest') from None
    if not isinstance(manifest, dict):
        raise BackupError('invalid_manifest')
    entries = manifest.get('entries')
    if manifest.get('version') != 1 or not isinstance(entries, list) or not 1 <= len(entries) <= DEFAULT_MAX_FILES:
        raise BackupError('invalid_manifest')
    seen, total = set(), 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str):
            raise BackupError('invalid_manifest')
        relative = Path(entry['path'])
        if relative.is_absolute() or '..' in relative.parts or relative.as_posix() != entry['path'] or entry['path'] in seen:
            raise BackupError('invalid_manifest_path')
        seen.add(entry['path'])
        file = safe_path(snapshot / relative, snapshot)
        if not file.is_file() or file.stat().st_size != entry.get('bytes'):
            raise BackupError('backup_size_mismatch')
        total += file.stat().st_size
        if total > max_bytes:
            raise BackupError('backup_resource_limit')
        if digest(file) != entry.get('sha256'):
            raise BackupError('backup_hash_mismatch')
        if entry.get('kind') == 'sqlite':
            with closing(sqlite3.connect(file.as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise BackupError('sqlite_integrity_failed')
        elif entry.get('kind') != 'file':
            raise BackupError('invalid_manifest')
    actual = {file.relative_to(snapshot).as_posix() for file in files_under(snapshot, workspace)}
    if actual != seen | {'manifest.json'} or manifest.get('total_bytes') != total:
        raise BackupError('unexpected_backup_files')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=CODE_ROOT)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument('--output-root', type=Path)
    operation.add_argument('--verify-only', type=Path, metavar='SNAPSHOT_DIRECTORY')
    parser.add_argument('--include-files', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Attest managers, MCP, producers and managed browsers are stopped; acquire manager locks and inspect visible processes')
    parser.add_argument('--max-total-bytes', type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument('--max-files', type=int, default=DEFAULT_MAX_FILES)
    args = parser.parse_args()
    try:
        if args.verify_only:
            manifest = verify_backup(args.verify_only, args.workspace, max_bytes=args.max_total_bytes)
            result = {'verified': True, 'files': len(manifest['entries']), 'full_backup': False}
        else:
            snapshot, manifest = create_backup(args.workspace, args.output_root, include_files=args.include_files,
                offline=args.offline, max_bytes=args.max_total_bytes, max_files=args.max_files)
            result = {'created': True, 'snapshot': str(snapshot), 'files': len(manifest['entries']),
                      'kind': manifest['kind'], 'full_backup': False,
                      'missing_expected_databases': len(manifest['missing_expected_databases'])}
        print(json.dumps(result))
        return 0
    except Exception as error:
        # Never echo OS/provider/SQLite errors, environment values or file contents.
        print(json.dumps({'error': str(error) if isinstance(error, BackupError) else 'backup_operation_failed'}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

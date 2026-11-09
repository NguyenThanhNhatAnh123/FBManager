"""Validated local extension software bundles. Never copies profile or OTP storage."""
import base64
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

EXTENSION_ID = 'bhghoamapcdpbohphigoooaddinpkbai'
# Chrome Web Store's generated integrity metadata is removed by Chromium when
# loading an unpacked copy. Validate the runtime payload, not these store files.
STORE_METADATA = frozenset({'_metadata/computed_hashes.json', '_metadata/verified_contents.json'})


def manifest_identity(manifest):
    try:
        digest = hashlib.sha256(base64.b64decode(manifest['key'], validate=True)).digest()[:16].hex()
        return ''.join(chr(ord('a') + int(char, 16)) for char in digest)
    except (KeyError, ValueError, TypeError):
        return None


def software_files(directory):
    directory = directory.resolve()
    files = {}
    total = 0
    for path in directory.rglob('*'):
        if path.is_symlink() or not path.resolve().is_relative_to(directory):
            raise ValueError('Extension package contains an unsafe path.')
        if not path.is_file():
            continue
        if path.stat().st_size > 25_000_000:
            raise ValueError('Extension package file is too large.')
        total += path.stat().st_size
        if total > 100_000_000:
            raise ValueError('Extension package is too large.')
        files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return files


def validate_manifest(directory):
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest_identity(manifest) != EXTENSION_ID or manifest.get('manifest_version') != 3:
        raise ValueError('This package is not the expected Authenticator extension.')
    version = manifest.get('version', '')
    if not isinstance(version, str) or not re.fullmatch(r'\d{1,6}(?:\.\d{1,6}){1,3}', version):
        raise ValueError('Invalid Authenticator version.')
    return manifest


def prepare_from_installed(root, data_dir):
    root, data_dir = Path(root).resolve(), Path(data_dir).resolve()
    profiles_root = (root / 'profiles').resolve()
    database = data_dir / 'manager.sqlite3'
    if not database.is_file():
        raise ValueError('Create Manager profiles and install Authenticator in one profile first.')
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        profiles = list(connection.execute('SELECT id,profile_path FROM profiles ORDER BY id'))
    candidates = []
    for profile_id, raw_path in profiles:
        path = Path(raw_path)
        if path.is_symlink() or not path.resolve().is_relative_to(profiles_root) or path.resolve() == profiles_root:
            continue
        extension_root = path / 'Default' / 'Extensions' / EXTENSION_ID
        for source in extension_root.glob('*'):
            if not source.is_dir() or source.is_symlink() or not source.resolve().is_relative_to(path.resolve()):
                continue
            try:
                manifest = validate_manifest(source)
            except (ValueError, OSError, TypeError):
                continue
            candidates.append((tuple(int(n) for n in manifest['version'].split('.')), profile_id, source, manifest))
    if not candidates:
        raise ValueError('No official Authenticator installation was found in Manager profiles.')
    _, profile_id, source, manifest = max(candidates, key=lambda item: item[0])
    # This is only Default/Extensions/<ID>/<version> software. Local Extension
    # Settings, Sync Extension Settings, cookies and profile files are not read.
    source_files = software_files(source)
    files = {name: digest for name, digest in source_files.items() if name not in STORE_METADATA}
    base = root / '.runtime' / 'extensions' / 'authenticator'
    if not base.resolve().is_relative_to(root):
        raise ValueError('Unsafe extension bundle root.')
    base.mkdir(parents=True, exist_ok=True)
    destination = base / manifest['version']
    if destination.exists():
        if destination.is_symlink():
            raise ValueError('An existing local bundle is an unsafe symlink.')
        existing = software_files(destination)
        payload = {name: digest for name, digest in existing.items() if name not in STORE_METADATA}
        extra = existing.keys() - files.keys()
        if payload != files or not extra.issubset(STORE_METADATA) or any(existing[name] != source_files.get(name) for name in extra):
            raise ValueError('An existing local bundle differs. Review it before replacing it.')
        # Migrate the previous app-created bundle without touching source profile
        # files. Both exact targets are checked inside this verified workspace.
        for name in extra:
            metadata = (destination / name).resolve()
            if not metadata.is_relative_to(destination.resolve()):
                raise ValueError('Unsafe generated metadata path.')
            metadata.unlink()

    else:
        with tempfile.TemporaryDirectory(prefix='prepare-', dir=base) as staging_name:
            staging = Path(staging_name).resolve()
            if not staging.is_relative_to(base.resolve()):
                raise ValueError('Unsafe staging path.')
            for relative in files:
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
            if software_files(staging) != files:
                raise ValueError('Extension software changed while it was being copied.')
            # Move only this verified workspace staging directory to its final path.
            if not destination.resolve().is_relative_to(base.resolve()):
                raise ValueError('Unsafe destination path.')
            staging.rename(destination)
    plan = {'enabled': False, 'scope': 'manager_profiles_only', 'extension_id': EXTENSION_ID,
            'version': manifest['version'], 'bundle': destination.relative_to(root).as_posix(),
            'files': files, 'excluded_store_metadata': sorted(STORE_METADATA.intersection(source_files)),
            'source_profile_id': profile_id, 'profile_count_at_preparation': len(profiles)}
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / 'authenticator-plan.json').write_text(json.dumps(plan, indent=2), encoding='utf-8')
    return plan


def load_deployment(root, data_dir):
    path = Path(data_dir) / 'authenticator-auto.json'
    if not path.is_file():
        return None
    deployment = json.loads(path.read_text(encoding='utf-8'))
    if deployment.get('enabled') is not True:
        return None
    if deployment.get('scope') != 'manager_profiles_only' or deployment.get('extension_id') != EXTENSION_ID:
        raise ValueError('Invalid automatic Authenticator deployment scope.')
    root = Path(root).resolve()
    bundle = (root / deployment['bundle']).resolve()
    base = (root / '.runtime' / 'extensions' / 'authenticator').resolve()
    if not bundle.is_relative_to(base) or bundle == base or not base.is_relative_to(root):
        raise ValueError('Unsafe automatic Authenticator bundle path.')
    manifest = validate_manifest(bundle)
    if manifest['version'] != deployment.get('version') or software_files(bundle) != deployment.get('files'):
        raise ValueError('Automatic Authenticator bundle verification failed. Run start-auto-authenticator.bat again.')
    return deployment | {'bundle_path': str(bundle)}

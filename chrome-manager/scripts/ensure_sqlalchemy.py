"""Use SQLAlchemy's bundled pure-Python implementation when optional accelerators cannot load.

Creates a local compatibility wheel from RECORD-verified files of the already installed
distribution. It omits native accelerators; it never alters Windows Application Control.
Official reference: https://docs.sqlalchemy.org/en/21/intro.html#building-the-cython-extensions
"""
import base64
import csv
import hashlib
import importlib.metadata
import io
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build_pure_wheel(output_directory):
    distribution = importlib.metadata.distribution('SQLAlchemy')
    entries = distribution.files
    if not entries:
        raise RuntimeError('SQLAlchemy package RECORD is missing; reinstall from its official source.')
    metadata_entry = next(entry for entry in entries if str(entry).endswith('.dist-info/METADATA'))
    metadata_directory = metadata_entry.parts[0]
    payload = {}
    skipped = []
    for entry in entries:
        relative = str(entry).replace('\\', '/')
        parts = Path(relative).parts
        if not parts or parts[0] not in ('sqlalchemy', metadata_directory):
            continue
        if '..' in parts:
            raise RuntimeError('Invalid SQLAlchemy distribution path.')
        if entry.suffix.lower() in ('.pyd', '.dll', '.so', '.dylib'):
            if entry.suffix.lower() == '.pyd':
                source = entry.parent / (entry.name.split('.')[0] + '.py')
                if not distribution.locate_file(source).is_file():
                    raise RuntimeError('A native accelerator has no bundled Python equivalent.')
            skipped.append(relative)
            continue
        if '__pycache__' in parts or entry.suffix.lower() == '.pyc' or entry.name in ('RECORD', 'INSTALLER', 'REQUESTED', 'direct_url.json', 'WHEEL'):
            continue
        data = distribution.locate_file(entry).read_bytes()
        if entry.hash:
            if entry.hash.mode != 'sha256':
                raise RuntimeError('Unexpected package hash algorithm.')
            actual = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
            if actual != entry.hash.value:
                raise RuntimeError('Installed SQLAlchemy source failed its RECORD integrity check.')
        elif parts[0] == 'sqlalchemy':
            raise RuntimeError('An installed SQLAlchemy source has no integrity hash.')
        payload[relative] = data
    if 'sqlalchemy/util/_immutabledict_cy.py' not in payload and not skipped:
        raise RuntimeError('No pure-Python SQLAlchemy sources were found.')
    payload[metadata_directory + '/WHEEL'] = (
        'Wheel-Version: 1.0\n'
        'Generator: chrome-manager-local-pure-python-compatibility\n'
        'Root-Is-Purelib: true\n'
        'Tag: py3-none-any\n'
    ).encode()
    record_buffer = io.StringIO(newline='')
    writer = csv.writer(record_buffer)
    for relative, data in sorted(payload.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow([relative, 'sha256=' + digest, len(data)])
    record_path = metadata_directory + '/RECORD'
    writer.writerow([record_path, '', ''])
    payload[record_path] = record_buffer.getvalue().encode()
    output_directory.mkdir(parents=True, exist_ok=True)
    wheel = output_directory / f'sqlalchemy-{distribution.version}-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w', zipfile.ZIP_DEFLATED) as archive:
        for relative, data in sorted(payload.items()):
            archive.writestr(relative, data)
    return wheel, len(skipped)

def main():
    if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
        raise RuntimeError('Run this helper with this project virtual environment.')
    try:
        import sqlalchemy
    except ImportError as error:
        detail = str(error)
        if 'DLL load failed' not in detail and '_cy' not in detail and 'cyextension' not in detail:
            raise
    else:
        print(f'SQLAlchemy {sqlalchemy.__version__}: import OK')
        return
    temp = ROOT / '.runtime' / 'tmp'
    temp.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp))
    wheel, count = build_pure_wheel(ROOT / '.runtime' / 'wheels')
    print(f'Installing SQLAlchemy pure-Python compatibility build; omitted {count} optional native accelerators.')
    subprocess.run([
        sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
        '--no-index', '--no-cache-dir', '--no-deps', '--force-reinstall', str(wheel),
    ], cwd=ROOT, env=environment, check=True)
    subprocess.run([
        sys.executable, '-c',
        'import sqlalchemy; from sqlalchemy import create_engine, text; '
        'engine=create_engine("sqlite://"); '
        'connection=engine.connect(); '
        'assert connection.execute(text("select 1")).scalar()==1; '
        'connection.close(); engine.dispose(); print("SQLAlchemy pure-Python + SQLite: OK")',
    ], cwd=ROOT, env=environment, check=True)

if __name__ == '__main__':
    main()

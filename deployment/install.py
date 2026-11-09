"""Validated code deployment for Via. Existing user/runtime data is never copied or removed."""
import argparse
from contextlib import contextmanager,ExitStack
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parent))
from package_policy import source_file_allowed,normalized_path,check_regular,REQUIRED_FILES,validate_filesystem_components

LOCK_FILES=('via-auth/data/workspaces.lock','chrome-manager/data/manager.lock','meta-page-manager/data/page-manager.lock','via-mcp/data/remote-server.lock')
RUNTIME_ROOTS=('chrome-manager/data','chrome-manager/profiles','chrome-manager/reel-assets','chrome-manager/page-assets',
    'chrome-manager/.runtime','meta-page-manager/data','meta-page-manager/reel-assets','meta-page-manager/.runtime',
    'via-auth/data','via-mcp/data','page-producer/page-manager-bridge/runs','media','mcp-media','workspaces','private-backups')


class InstallError(Exception):pass


def absolute(path):
    try:validate_filesystem_components(path)
    except ValueError:raise InstallError('unsafe_windows_path_component') from None
    return Path(os.path.abspath(os.fspath(path)))


def safe_path(path,root):
    path,root=absolute(path),absolute(root)
    if not path.is_relative_to(root):raise InstallError('path_outside_installation')
    for part in (*reversed(path.parents),path):
        try:info=part.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise InstallError('filesystem_redirect_rejected')
        if part==path and stat.S_ISREG(info.st_mode) and info.st_nlink!=1:
            raise InstallError('hardlinked_file_rejected')
    return path


def digest(path):
    result=hashlib.sha256()
    with path.open('rb') as file:
        while block:=file.read(1024*1024):result.update(block)
    return result.hexdigest()


def verify_source(source):
    source=absolute(source)
    manifest_file=safe_path(source/'distribution-manifest.json',source)
    check_regular(manifest_file)
    if manifest_file.stat().st_size>4*1024*1024:raise InstallError('invalid_release_manifest')
    try:manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError):raise InstallError('invalid_release_manifest') from None
    if (not isinstance(manifest,dict) or type(manifest.get('format_version')) is not int
        or manifest['format_version']!=1 or manifest.get('product')!='via-server'
        or manifest.get('data_included') is not False or manifest.get('credentials_included') is not False
        or not isinstance(manifest.get('version'),str)
        or not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?',manifest['version'])
        or not isinstance(manifest.get('entries'),list) or not 1<=len(manifest['entries'])<=10000):
        raise InstallError('invalid_release_manifest')
    names=set();casefold=set()
    for entry in manifest['entries']:
        if not isinstance(entry,dict) or set(entry)!={'path','bytes','sha256'}:raise InstallError('invalid_release_entry')
        name=entry['path']
        if not source_file_allowed(name) or name.casefold() in casefold:raise InstallError('release_path_not_allowed')
        if type(entry['bytes']) is not int or entry['bytes']<0 or not isinstance(entry['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256']):
            raise InstallError('invalid_release_entry')
        path=safe_path(source/normalized_path(name),source);check_regular(path)
        if path.stat().st_size!=entry['bytes'] or digest(path)!=entry['sha256']:raise InstallError('release_hash_mismatch')
        names.add(name);casefold.add(name.casefold())
    if not REQUIRED_FILES<=names:raise InstallError('required_release_files_missing')
    return manifest


def public_origin(value):
    from urllib.parse import urlsplit
    import ipaddress
    if value in (None,''):return None
    if not isinstance(value,str):raise InstallError('public_origin_must_be_https_origin')
    parsed=urlsplit(value)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path or parsed.query or parsed.fragment or value.endswith('/')
        or '\\' in value or any(ord(c)<33 for c in value)):
        raise InstallError('public_origin_must_be_https_origin')
    try:
        port=parsed.port
        if port is not None and not 1<=port<=65535:raise ValueError
        raw=parsed.hostname
        if '%' in raw:raise ValueError
        try:
            address=ipaddress.ip_address(raw)
            host='['+address.compressed+']' if address.version==6 else str(address)
        except ValueError:
            host=raw.encode('idna').decode('ascii').lower()
            if len(host)>253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?',label) for label in host.split('.')):
                raise ValueError
            if re.fullmatch(r'[0-9.]+',host):raise ValueError
    except (ValueError,UnicodeError):raise InstallError('public_origin_must_be_https_origin') from None
    return 'https://'+host+(':'+str(port) if port not in (None,443) else '')


def plan(source,target,existing=None,origin=None):
    source,target=absolute(source),absolute(target)
    if target==Path(target.anchor) or source==target or source.is_relative_to(target) or target.is_relative_to(source):
        raise InstallError('invalid_installation_root')
    safe_path(target,target)
    manifest=verify_source(source)
    if existing is not None and absolute(existing)!=target:raise InstallError('cross_root_data_migration_not_automatic')
    nonempty=target.exists() and any(target.iterdir())
    if nonempty and existing is None:raise InstallError('nonempty_target_requires_existing_root')
    entries=manifest['entries']
    for entry in entries:safe_path(target/entry['path'],target)
    inventory={name:{'present':(target/name).exists()} for name in RUNTIME_ROOTS}
    for config in ('chrome-manager/.env','meta-page-manager/.env','deployment/server-config.json'):
        inventory[config]={'present':(target/config).exists()}
    return {'format_version':1,'release_version':manifest['version'],'install_root':str(target),
            'mode':'update-code-preserve-data' if existing is not None else 'fresh-code-install',
            'source_files':len(entries),'runtime_inventory':inventory,'public_origin':public_origin(origin),
            'existing_configuration_preserved':True,'automatic_data_import':False,
            'runtime_preparation':'new-local-venv-from-locked-requirements',
            'fresh_accounts_only':not (target/'via-auth/data/identity.sqlite3').exists()}


@contextmanager
def lock_existing(path):
    if not path.parent.is_dir():yield;return
    safe_path(path,path.parent)
    stream=path.open('a+b');stream.seek(0)
    try:
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise InstallError('installation_is_running_stop_it_first') from None
        yield
    finally:
        try:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        except OSError:pass
        stream.close()


def running_workspace_processes(target):
    if os.name!='nt':raise InstallError('windows_server_required')
    script=Path(__file__).with_name('check-processes.ps1')
    result=subprocess.run(['powershell.exe','-NoProfile','-File',str(script),'-Root',str(target),'-ExcludePid',str(os.getpid())],
                          capture_output=True,text=True,timeout=30,check=False,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise InstallError('workspace_process_probe_failed')
    try:value=json.loads(result.stdout.lstrip('\ufeff'))
    except ValueError:raise InstallError('workspace_process_probe_failed') from None
    if not isinstance(value,dict) or type(value.get('count')) is not int:raise InstallError('workspace_process_probe_failed')
    return value['count']


def invoke(argv,step,timeout=1200):
    try:result=subprocess.run(list(map(str,argv)),capture_output=True,text=True,timeout=timeout,check=False,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,subprocess.TimeoutExpired):raise InstallError(step+'_failed') from None
    # Do not print package-manager/config/SDK output; it may contain environment
    # credentials. Only a fixed step code crosses this installation boundary.
    if result.returncode:raise InstallError(step+'_failed')
    return result.stdout


def restore_runtime(target,backup):
    venv=safe_path(target/'chrome-manager/.venv',target)
    if venv.exists():
        if venv.is_symlink() or getattr(venv.lstat(),'st_file_attributes',0)&0x400:raise InstallError('unsafe_failed_venv')
        # Dependency trees can exceed MAX_PATH after being nested in a release
        # test/root. Apply the extended Windows prefix only after checking the
        # absolute removal target against this installation root.
        removal=str(venv)
        if os.name=='nt':removal='\\\\?\\UNC\\'+removal[2:] if removal.startswith('\\\\') else '\\\\?\\'+removal
        shutil.rmtree(removal)
    old=safe_path(backup/'previous-venv',target)
    if old.exists():old.rename(venv)


def prepare_runtime(target,python,backup,*,runner=invoke):
    python=absolute(python)
    if python.is_relative_to(target/'chrome-manager/.venv'):raise InstallError('installer_python_is_target_venv')
    runner([python,'-I','-c','import sys;assert sys.version_info >= (3,12)'],'python_version',30)
    venv=safe_path(target/'chrome-manager/.venv',target)
    old=backup/'previous-venv'
    moved=False
    if venv.exists():
        if not venv.is_dir():raise InstallError('invalid_existing_venv')
        venv.rename(old);moved=True
    try:
        runner([python,'-m','venv',venv],'venv_create',180)
        local=venv/'Scripts/python.exe'
        runner([local,'-I','-m','pip','install','--disable-pip-version-check','-r',target/'deployment/requirements-server.lock'],'dependency_install',1200)
        runner([local,'-I','-c','import fastapi,uvicorn,sqlalchemy,playwright,psutil,httpx,filelock,dotenv,PIL,numpy,tzdata; import numpy as n; assert n.__version__'],'runtime_imports',30)
        runner([local,'-I','-B',target/'deployment/preflight.py','--root',target,'--runtime-only'],'machine_runtime_preflight',30)
    except BaseException:
        # Only the new, owned runtime directory is removed, never any mutable app
        # root. Absolute paths were checked against the explicit installation root.
        restore_runtime(target,backup)
        raise
    return venv/'Scripts/python.exe'


def install(source,target,existing=None,origin=None,python=None,*,runtime=True,process_probe=running_workspace_processes,runner=invoke):
    result=plan(source,target,existing,origin)
    source,target=absolute(source),absolute(target)
    manifest=verify_source(source)
    if runtime and python is None:raise InstallError('python_executable_required')
    if existing is not None and process_probe(target):raise InstallError('workspace_processes_are_running')
    with ExitStack() as guards:
        if existing is not None:
            for relative in LOCK_FILES:guards.enter_context(lock_existing(safe_path(target/relative,target)))
            if process_probe(target):raise InstallError('workspace_processes_are_running')
        target.mkdir(parents=True,exist_ok=True)
        backup=safe_path(target/'.install-backups'/('release-'+manifest['version']+'-'+uuid4().hex),target)
        backup.mkdir(parents=True,exist_ok=False)
        previous=[];created=[];runtime_created=False;temporary=None
        try:
            for entry in manifest['entries']:
                relative=entry['path'];src=safe_path(source/relative,source);dst=safe_path(target/relative,target)
                if digest(src)!=entry['sha256']:raise InstallError('release_changed_during_install')
                if dst.exists():
                    if not dst.is_file():raise InstallError('invalid_existing_code_target')
                    saved=backup/'code'/relative;saved.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(dst,saved);previous.append((dst,saved))
                else:created.append(dst)
                dst.parent.mkdir(parents=True,exist_ok=True)
                temporary=dst.with_name(dst.name+'.install-'+uuid4().hex)
                with src.open('rb') as inp,temporary.open('xb') as out:shutil.copyfileobj(inp,out,1024*1024)
                if digest(temporary)!=entry['sha256']:raise InstallError('installed_code_hash_mismatch')
                temporary.replace(dst)
            config=safe_path(target/'deployment/server-config.json',target)
            if not config.exists():
                config.write_text(json.dumps({'version':1,'bind_host':'127.0.0.1','port':8040,'manager_port':8000,
                    'public_origin':public_origin(origin),'allow_http_loopback':False},indent=2)+'\n',encoding='utf-8')
                created.append(config)
            for relative in ('media/ideas','media/videos','media/audio','mcp-media'):
                safe_path(target/relative,target).mkdir(parents=True,exist_ok=True)
            if runtime:
                local=prepare_runtime(target,python,backup,runner=runner);runtime_created=True
                identity=target/'via-auth/data/identity.sqlite3'
                if not identity.exists():
                    # Fresh auth only; the CLI itself refuses an initialized user
                    # table, so existing credentials can never be silently reset.
                    runner([local,'-I','-B',target/'via-auth/manage_users.py','bootstrap'],'fresh_user_bootstrap',60)
            record={'release_version':manifest['version'],'installed_at_utc':datetime.now(timezone.utc).isoformat(),
                    'source_files':len(manifest['entries']),'data_replaced':False,'configuration_preserved':True}
            metadata=target/'deployment/installed-release.json'
            safe_path(metadata,target)
            if metadata.exists():
                saved=backup/'previous-installed-release.json';shutil.copyfile(metadata,saved);previous.append((metadata,saved))
            else:created.append(metadata)
            metadata.write_text(json.dumps(record,indent=2)+'\n',encoding='utf-8')
        except BaseException:
            # Roll back code only. User databases, profiles, media, .env, auth data
            # and configuration that existed before installation are untouched.
            for dst,saved in reversed(previous):shutil.copyfile(saved,safe_path(dst,target))
            for dst in reversed(created):
                safe_path(dst,target)
                if dst.is_file():dst.unlink()
            if temporary is not None and temporary.exists():safe_path(temporary,target).unlink()
            if runtime_created:restore_runtime(target,backup)
            raise
    return {**result,'installed':True,'code_backup':str(backup),'services_started':False,
            'fresh_runtime_created':runtime_created,'data_replaced':False,'next_step':'deployment/start-server.ps1'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--install-root',type=Path,required=True)
    parser.add_argument('--existing-root',type=Path)
    parser.add_argument('--python',type=Path,default=Path(sys.executable))
    parser.add_argument('--public-origin')
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    if args.check_only:
        value=plan(args.source_root,args.install_root,args.existing_root,args.public_origin)
        value.update({'check_only':True,'writes_performed':False})
    else:value=install(args.source_root,args.install_root,args.existing_root,args.public_origin,args.python)
    print(json.dumps(value,ensure_ascii=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'error':str(error) if type(error) in (InstallError,ValueError) else 'installation_failed'}),file=sys.stderr)
        raise SystemExit(1)

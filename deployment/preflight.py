"""Read-only machine checks. Never launches browsers, Codex, models or publication jobs."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def inspect(root,*,runtime_only=False):
    root=Path(root).absolute()
    checks={'python_312_or_later':sys.version_info>=(3,12),'windows':os.name=='nt'}
    missing=[]
    for name in ('fastapi','uvicorn','sqlalchemy','playwright','psutil','httpx','filelock','dotenv','PIL','numpy','tzdata','websockets'):
        try:importlib.import_module(name)
        except (ImportError,OSError):missing.append(name)
    checks['runtime_imports']=not missing
    node=shutil.which('node.exe' if os.name=='nt' else 'node')
    checks['node_2212_or_later']=False
    if node:
        try:
            output=subprocess.run([node,'-p','JSON.stringify(process.versions.node.split(".").map(Number))'],
                capture_output=True,text=True,timeout=10,check=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            version=json.loads(output.stdout)
            checks['node_2212_or_later']=version[0]>22 or (version[0]==22 and version[1]>=12)
        except (ValueError,IndexError,OSError,subprocess.SubprocessError):pass
    options={}
    if 'dotenv' not in missing:
        from dotenv import dotenv_values
        options=dotenv_values(root/'chrome-manager/.env')
    channel=os.environ.get('CM_BROWSER_CHANNEL',options.get('CM_BROWSER_CHANNEL','chrome'))
    browser=False
    if channel=='chrome':
        for variable in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA'):
            folder=os.environ.get(variable)
            if folder and (Path(folder)/'Google/Chrome/Application/chrome.exe').is_file():browser=True
    elif channel=='':
        # Query the configured executable path only; no browser process is opened.
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser=Path(playwright.chromium.executable_path).is_file()
        except Exception:pass
        if (root/'chrome-manager/data/authenticator-auto.json').is_file():
            browser=any((root/'chrome-manager/.browsers').glob('chromium-*/chrome-win*/chrome.exe'))
    checks['browser_available']=browser
    checks['ffmpeg_available']=all((root/'tools/ffmpeg/bin'/name).is_file() for name in ('ffmpeg.exe','ffprobe.exe'))
    codex=os.environ.get('PM_CODEX_CLI')
    codex_ready=bool(codex and Path(codex).is_absolute() and Path(codex).is_file()) or bool(shutil.which('codex.exe'))
    uploader=os.environ.get('PM_PODSACH_TOOL_ROOT')
    warnings=[]
    if not codex_ready:warnings.append('producer_codex_requires_local_install_and_login_or_PM_CODEX_CLI')
    if not uploader:warnings.append('uploader_root_not_explicitly_configured_for_this_machine')
    if not (root/'media/audio/source-library.json').is_file():warnings.append('production_audio_catalog_must_be_provisioned_and_approved_separately')
    required=('python_312_or_later','windows','runtime_imports','node_2212_or_later')
    if not runtime_only:required+=('browser_available','ffmpeg_available')
    return {'ready':all(checks[key] for key in required),'checks':checks,'missing_imports':missing,
            'warnings':warnings,'runtime_only':runtime_only,'browsers_launched':False,'models_invoked':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--runtime-only',action='store_true')
    args=parser.parse_args();result=inspect(args.root,runtime_only=args.runtime_only)
    print(json.dumps(result));return 0 if result['ready'] else 1


if __name__=='__main__':raise SystemExit(main())

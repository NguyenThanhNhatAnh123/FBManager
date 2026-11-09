"""Build a deterministic, code-only Via server ZIP from the release allowlist."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from deployment.package_policy import source_file_allowed,check_regular,REQUIRED_FILES,validate_filesystem_components


def sha(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        while block:=stream.read(1024*1024):result.update(block)
    return result.hexdigest()


def selected_files(root):
    validate_filesystem_components(root)
    root=Path(root).absolute()
    result=[]
    # Walk only fixed code/resource roots; mutable profile/media/cache trees are
    # never enumerated. A denied file cannot enter the archive via manifest edits.
    roots=('deployment','chrome-manager/backend','chrome-manager/scripts','chrome-manager/frontend/dist',
        'chrome-manager/shared','meta-page-manager/backend','meta-page-manager/frontend','meta-page-manager/scripts',
        'via-auth/via_auth','via-mcp/via_mcp','page-producer/.agents/skills','page-producer/assets/shirt-style-references',
        'page-producer/page-manager-bridge','tools/ffmpeg')
    from deployment.package_policy import EXACT_FILES,DENIED_PARTS
    paths={root/name for name in EXACT_FILES if (root/name).is_file()}
    for relative in roots:
        base=root/relative
        if not base.exists():continue
        if base.is_symlink() or getattr(base.lstat(),'st_file_attributes',0)&0x400:raise ValueError('package_redirect_or_link_rejected')
        for folder,dirs,files in os.walk(base,followlinks=False):
            kept=[]
            for name in dirs:
                child=Path(folder)/name
                if name.casefold() in DENIED_PARTS:continue
                if child.is_symlink() or getattr(child.lstat(),'st_file_attributes',0)&0x400:raise ValueError('package_redirect_or_link_rejected')
                kept.append(name)
            dirs[:]=kept
            paths.update(Path(folder)/name for name in files)
    for path in sorted(paths,key=lambda path:path.relative_to(root).as_posix()):
        relative=path.relative_to(root).as_posix()
        if not source_file_allowed(relative):continue
        check_regular(path)
        result.append((relative,path))
    return result


def make_manifest(root,version,*,strict=True):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?',version):raise ValueError('invalid_release_version')
    selected=selected_files(root)
    present={name for name,_ in selected}
    if strict and not REQUIRED_FILES<=present:raise ValueError('required_release_files_missing:'+','.join(sorted(REQUIRED_FILES-present)))
    entries=[{'path':relative,'bytes':path.stat().st_size,'sha256':sha(path)} for relative,path in selected]
    return {'format_version':1,'product':'via-server','version':version,'data_included':False,
            'credentials_included':False,'entries':entries,'mutable_data_policy':'preserve-existing-never-overwrite'},selected


def build_server(root,output,version='1.4.1',*,strict=True):
    validate_filesystem_components(root);validate_filesystem_components(output)
    root,output=Path(root).absolute(),Path(output).absolute()
    if output.exists():raise ValueError('output_already_exists')
    manifest,selected=make_manifest(root,version,strict=strict)
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.building')
    if temporary.exists():raise ValueError('output_already_exists')
    manifest_bytes=(json.dumps(manifest,ensure_ascii=True,indent=2)+'\n').encode()
    with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        def write(name,data):
            item=zipfile.ZipInfo('via-server/'+name,date_time=(1980,1,1,0,0,0))
            item.compress_type=zipfile.ZIP_DEFLATED;item.external_attr=0o100644<<16
            archive.writestr(item,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        write('distribution-manifest.json',manifest_bytes)
        for relative,path in selected:
            raw=path.read_bytes()
            expected=next(item for item in manifest['entries'] if item['path']==relative)
            if len(raw)!=expected['bytes'] or hashlib.sha256(raw).hexdigest()!=expected['sha256']:
                raise ValueError('source_changed_during_packaging')
            write(relative,raw)
    temporary.replace(output)
    return {'created':True,'kind':'server','version':version,'output':str(output),'files':len(selected),
            'bytes':output.stat().st_size,'sha256':sha(output),'data_included':False,'credentials_included':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=('server',),default='server')
    parser.add_argument('--source-root',type=Path,default=ROOT)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--version',default='1.4.1')
    args=parser.parse_args()
    print(json.dumps(build_server(args.source_root,args.output,args.version)))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'error':str(error) if type(error) is ValueError else 'distribution_build_failed'}),file=sys.stderr)
        raise SystemExit(1)

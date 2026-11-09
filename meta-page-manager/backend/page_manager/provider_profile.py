"""Per-process Codex producer isolation. Never writes global configuration.

The legacy producer keeps its seven skills and read-only Via status. Stage
profiles expose only their own skills, never MCP or agent delegation. Native
image generation is enabled only for the image stage (and legacy producer).
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tomllib
from types import MappingProxyType

from .agent_roles import codex_role_args

PRODUCER_SKILLS=('page-shirt-zoom-8s','imagegen','realistic-shirt-photos-9x16',
    'realistic-shirt-photos-closeup-9x16','ao-cao-cap','batch-remove-background','us-video-review')
STAGE_SKILLS=MappingProxyType({
    'image':('imagegen','realistic-shirt-photos-9x16','realistic-shirt-photos-closeup-9x16','ao-cao-cap'),
    'qa':('us-video-review',),
    'product':('batch-remove-background',),
})
MAX_SKILLS=512
MAX_PROFILE_CHARACTERS=24000


def _codex_home():return Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))


def _configurations(project):
    paths=[_codex_home()/'config.toml',*(parent/'.codex'/'config.toml' for parent in reversed((project,*project.parents)))]
    values=[];seen=set()
    for path in paths:
        if path in seen:continue
        seen.add(path)
        if not path.exists():continue
        if not path.is_file() or path.stat().st_size>1024*1024:raise ValueError('producer_configuration_invalid')
        try:values.append(tomllib.loads(path.read_text(encoding='utf-8')))
        except (OSError,UnicodeError,tomllib.TOMLDecodeError):raise ValueError('producer_configuration_invalid') from None
    return values


def _required(project,names=PRODUCER_SKILLS):
    if not project.is_absolute() or project.resolve()!=project or not project.is_dir():raise ValueError('producer_project_invalid')
    result={}
    for name in names:
        path=project/'.agents'/'skills'/name/'SKILL.md'
        try:
            for parent in (path,*path.parents):
                info=parent.lstat()
                if parent.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('producer_skill_unavailable')
            info=path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=2*1024*1024:raise ValueError('producer_skill_unavailable')
            with path.open('r',encoding='utf-8') as stream:text=stream.read(65536)
            header=text.split('---',2)
            match=re.search(r'^name:\s*["\']?([A-Za-z0-9_-]+)["\']?\s*$',header[1],re.M) if len(header)==3 and not header[0].strip() else None
            if not match or match[1]!=name:raise ValueError('producer_skill_unavailable')
        except (OSError,UnicodeError):raise ValueError('producer_skill_unavailable') from None
        result[path]=True
    return result


def _skill_paths(project,configurations,roots=None,observed=()):
    if roots is None:
        roots=[_codex_home()/'skills',Path.home()/'.agents'/'skills',_codex_home()/'plugins'/'cache',
            *(parent/'.agents'/'skills' for parent in (project,*project.parents))]
    paths=set()
    for root in dict.fromkeys(map(Path,roots)):
        if not root.is_dir():continue
        visited=0
        for folder,dirs,files in os.walk(root,followlinks=False):
            visited+=1
            if visited>10000:raise ValueError('producer_skill_inventory_too_large')
            dirs[:]=[name for name in dirs if name not in ('.git','node_modules','__pycache__')
                and not (Path(folder)/name).is_symlink() and not (Path(folder)/name).is_junction()]
            if 'SKILL.md' in files:paths.add(Path(folder)/'SKILL.md')
            if len(paths)>MAX_SKILLS:raise ValueError('producer_skill_inventory_too_large')
    for config in configurations:
        entries=config.get('skills',{}).get('config',[])
        if not isinstance(entries,list):raise ValueError('producer_configuration_invalid')
        for row in entries:
            if isinstance(row,dict) and isinstance(row.get('path'),str):
                path=Path(row['path'])
                if path.name!='SKILL.md':path=path/'SKILL.md'
                if path.is_absolute():paths.add(path)
    for item in observed:
        path=Path(item)
        if not path.is_absolute() or path.name!='SKILL.md':raise ValueError('producer_skill_inventory_invalid')
        paths.add(path)
    return paths


def _disabled_table(names):
    return '{'+','.join(json.dumps(name,ensure_ascii=True)+'={enabled=false}' for name in sorted(names))+'}'


def producer_profile_args(project_root,*,stage=None,configurations=None,skill_roots=None,observed_skill_paths=()):
    """Return only CLI -c pairs, also usable by read-only app-server discovery.

    ``stage=None`` preserves the legacy seven-skill producer. Explicit image,
    qa and product stages disable every MCP transport and agent delegation;
    their prompt supplies the pipeline contract rather than its full skill.
    Optional inputs support synthetic tests and an explicit discovery inventory.
    Config values/credentials are never copied into arguments; only names and
    skill paths are used. The configured Via transport stays in its owner config.
    The exec caller must separately add --skip-git-repo-check for this project.
    """
    if stage is not None and (not isinstance(stage,str) or stage not in STAGE_SKILLS):raise ValueError('producer_stage_invalid')
    project=Path(project_root);required=_required(project,PRODUCER_SKILLS if stage is None else STAGE_SKILLS[stage])
    configurations=_configurations(project) if configurations is None else configurations
    if not isinstance(configurations,(list,tuple)) or any(not isinstance(row,dict) for row in configurations):raise ValueError('producer_configuration_invalid')
    servers=set();plugins=set();apps={'_default'}
    for config in configurations:
        for field,target in (('mcp_servers',servers),('plugins',plugins),('apps',apps)):
            table=config.get(field,{})
            if not isinstance(table,dict) or any(not isinstance(name,str) for name in table):raise ValueError('producer_configuration_invalid')
            target.update(table)
    if stage is None and 'via_manager' not in servers:raise ValueError('producer_mcp_unconfigured')
    if any(not re.fullmatch(r'[A-Za-z0-9_]+',name) for name in servers):raise ValueError('producer_mcp_name_unsupported')
    if stage is None:
        args=codex_role_args('producer')+['-c','mcp_servers.via_manager.enabled=true']
        disabled_servers=servers-{'via_manager'}
    else:
        # Do not synthesize a transport-less Via entry in configurations which
        # have no Via server. An existing server also receives an empty policy.
        args=codex_role_args('producer_'+stage) if 'via_manager' in servers else []
        disabled_servers=servers
    for name in sorted(disabled_servers):args.extend(['-c','mcp_servers.'+name+'.enabled=false'])
    paths=_skill_paths(project,configurations,skill_roots,observed_skill_paths)
    settings={path:False for path in paths};settings.update(required)
    if len(settings)>MAX_SKILLS:raise ValueError('producer_skill_inventory_too_large')
    skills='['+','.join('{path='+json.dumps(path.as_posix(),ensure_ascii=True)+',enabled='+str(enabled).lower()+'}'
        for path,enabled in sorted(settings.items(),key=lambda item:item[0].as_posix().casefold()))+']'
    args.extend(['-c','plugins='+_disabled_table(plugins),'-c','apps='+_disabled_table(apps),
        '-c','features.apps=false','-c','features.image_generation='+str(stage in (None,'image')).lower(),
        '-c','approval_policy="never"','-c','sandbox_workspace_write.network_access=true','-c','skills.config='+skills])
    if stage is not None:args.extend(['-c','features.multi_agent=false'])
    if len(subprocess.list2cmdline(args).encode('utf-16-le'))//2>MAX_PROFILE_CHARACTERS:raise ValueError('producer_profile_too_large')
    return args

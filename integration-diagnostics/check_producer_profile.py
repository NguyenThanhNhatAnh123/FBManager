"""Read-only app-server verification of the isolated project producer profile.

No thread/turn, generation, uploader check, login or publication is requested.
Only bounded configuration flags, MCP catalog and skill metadata are recorded.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'meta-page-manager'/'backend'))
from page_manager.idea_runs import _cli_path
from page_manager.provider_profile import PRODUCER_SKILLS,producer_profile_args,_codex_home
from codex_mcp_probe import CodexClient,ProbeError


def _hash(path):return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,default=ROOT/'page-producer')
    parser.add_argument('--output',type=Path,default=ROOT/'integration-diagnostics'/'project-consolidation'/'producer-profile-check.json')
    args=parser.parse_args();project=args.project.resolve();cli=_cli_path()
    if cli is None:raise SystemExit('Producer CLI unavailable')
    profile=producer_profile_args(project);config_path=_codex_home()/'config.toml';before=_hash(config_path)
    client=CodexClient(cli,45,profile);result={}
    try:
        client.call('initialize',{'clientInfo':{'name':'producer_profile_probe','version':'1.0.0'},'capabilities':{'experimentalApi':True}})
        client.send({'method':'initialized','params':{}})
        configuration=client.call('config/read',{'includeLayers':False,'cwd':str(project)}).get('config',{})
        response=client.call('mcpServerStatus/list',{'detail':'toolsAndAuthOnly','cursor':None})
        servers=[{'name':row.get('name'),'tools':sorted(row.get('tools',{})),'error':bool(row.get('toolsError'))}
            for row in response.get('data',[])]
        projects=client.call('skills/list',{'cwds':[str(project)],'forceReload':True}).get('data',[])
        skills=[row for item in projects for row in item.get('skills',[]) if row.get('enabled') is True]
        names=sorted(row['name'] for row in skills);local=all(Path(row['path']).is_relative_to(project) for row in skills)
        flags={'apps_enabled':configuration.get('features',{}).get('apps'),
            'native_image_generation_enabled':configuration.get('features',{}).get('image_generation'),
            'uploader_network_enabled':configuration.get('sandbox_workspace_write',{}).get('network_access'),
            'plugins_all_disabled':all(row.get('enabled') is False for row in configuration.get('plugins',{}).values()),
            'node_repl_disabled':configuration.get('mcp_servers',{}).get('node_repl',{}).get('enabled') is False}
        passed=(sum(len(row['tools']) for row in servers)==1
            and any(row['name']=='via_manager' and row['tools']==['via_status'] and not row['error'] for row in servers)
            and names==sorted(PRODUCER_SKILLS) and local and flags['apps_enabled'] is False
            and flags['native_image_generation_enabled'] is True and flags['uploader_network_enabled'] is True
            and flags['plugins_all_disabled'] and flags['node_repl_disabled'])
        result={'check':'project_producer_profile','passed':passed,'mcp_servers':servers,
            'active_skill_count':len(skills),'active_skill_names':names,'all_active_skills_project_local':local,
            'duplicate_active_names':sorted({name for name in names if names.count(name)>1}),
            'project_relative_skill_files':[Path(row['path']).relative_to(project).as_posix() for row in skills if Path(row['path']).is_relative_to(project)],
            'skill_error_count':sum(len(item.get('errors',[])) for item in projects),'flags':flags,
            'profile_override_count':len(profile)//2,'profile_command_utf16_characters':len(subprocess.list2cmdline(profile).encode('utf-16-le'))//2,
            'global_configuration_unchanged':before==_hash(config_path),'model_turn_started':False}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,ensure_ascii=True,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result,ensure_ascii=True))
        return 0 if passed and result['global_configuration_unchanged'] else 2
    except ProbeError as error:
        print(json.dumps({'check':'project_producer_profile','passed':False,'reason':str(error)}));return 2
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())

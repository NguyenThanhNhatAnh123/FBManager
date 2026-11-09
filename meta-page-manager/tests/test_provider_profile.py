"""Synthetic profile tests never start a provider, model, CLI or subprocess."""
from copy import deepcopy
from pathlib import Path
import tomllib

import pytest

from page_manager.provider_profile import PRODUCER_SKILLS,STAGE_SKILLS,producer_profile_args


def skill(root,name):
    path=root/name/'SKILL.md';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('---\nname: "'+name+'"\ndescription: inert test fixture\n---\n',encoding='utf-8')
    return path


@pytest.fixture
def profile(tmp_path):
    project=tmp_path/'project';local=project/'.agents'/'skills'
    required=[skill(local,name) for name in PRODUCER_SKILLS]
    external=tmp_path/'global';outside=[skill(external,name) for name in ('imagegen','flow-old','unrelated')]
    extra=skill(local,'unneeded-project-skill')
    config={'mcp_servers':{'via_manager':{'command':'private-transport','env':{'SECRET':'NEVER_COPY'}},'node_repl':{}},
        'plugins':{'plugin.with.dots@market':{'enabled':True}},'apps':{'private-app-id':{'enabled':True}},
        'skills':{'config':[{'path':str(outside[0]),'enabled':True}]}}
    return project,required,outside,extra,config


def parse(args):
    assert args[::2]==['-c']*(len(args)//2)
    return {key:tomllib.loads('value='+value)['value'] for key,value in (arg.split('=',1) for arg in args[1::2])}


def test_profile_enforces_project_seven_native_imagegen_and_only_read_mcp(profile):
    project,required,outside,extra,config=profile;before=deepcopy(config)
    args=producer_profile_args(project,configurations=[config],skill_roots=[project/'.agents/skills',outside[0].parent.parent])
    values=parse(args)
    assert values['mcp_servers.via_manager.enabled'] is True
    assert values['mcp_servers.via_manager.enabled_tools']==['via_status']
    assert values['mcp_servers.node_repl.enabled'] is False
    assert not any('"node_repl"' in arg for arg in args),'Quoted dotted CLI keys must not create a second invalid transport'
    assert values['plugins']=={'plugin.with.dots@market':{'enabled':False}}
    assert values['apps']=={'_default':{'enabled':False},'private-app-id':{'enabled':False}}
    assert values['features.apps'] is False and values['features.image_generation'] is True
    assert values['sandbox_workspace_write.network_access'] is True and values['approval_policy']=='never'
    skills={Path(row['path']):row['enabled'] for row in values['skills.config']}
    assert {path for path,enabled in skills.items() if enabled}==set(required)
    assert all(skills[path] is False for path in [*outside,extra])
    assert config==before and 'NEVER_COPY' not in str(args) and 'private-transport' not in str(args)


def test_all_config_layers_and_explicit_discovery_paths_are_restricted(profile,tmp_path):
    project,required,outside,extra,config=profile;observed=skill(tmp_path/'managed','managed-extra')
    second={'mcp_servers':{'additional':{'url':'PRIVATE_URL'}},'plugins':{'second@market':{}},'apps':{'second-app':{}}}
    values=parse(producer_profile_args(project,configurations=[config,second],skill_roots=[],observed_skill_paths=[observed]))
    assert values['mcp_servers.additional.enabled'] is False
    assert values['plugins']['second@market']['enabled'] is False and values['apps']['second-app']['enabled'] is False
    skills={Path(row['path']):row['enabled'] for row in values['skills.config']}
    assert skills[observed] is False and all(skills[path] is True for path in required)
    assert 'PRIVATE_URL' not in str(values)


@pytest.mark.parametrize('change',['missing','wrong_name','empty'])
def test_incomplete_local_skill_package_stops_before_provider_launch(profile,change):
    project,required,_,_,config=profile;path=required[0]
    if change=='missing':path.unlink()
    else:path.write_text('' if change=='empty' else '---\nname: substituted-skill\n---\n',encoding='utf-8')
    with pytest.raises(ValueError,match='producer_skill_unavailable'):
        producer_profile_args(project,configurations=[config],skill_roots=[])


@pytest.mark.parametrize('config,reason',[
    ({'mcp_servers':{}},'producer_mcp_unconfigured'),
    ({'mcp_servers':{'via_manager':{},'odd.server':{}}},'producer_mcp_name_unsupported'),
    ({'mcp_servers':[]},'producer_configuration_invalid'),
])
def test_configuration_cannot_select_an_unrestricted_or_invalid_fallback(profile,config,reason):
    with pytest.raises(ValueError,match=reason):producer_profile_args(profile[0],configurations=[config],skill_roots=[])


def test_observed_inventory_is_only_skill_paths_and_profile_is_deterministic(profile):
    project,_,_,_,config=profile
    with pytest.raises(ValueError,match='producer_skill_inventory_invalid'):
        producer_profile_args(project,configurations=[config],skill_roots=[],observed_skill_paths=[project/'auth.json'])
    first=producer_profile_args(project,configurations=[config],skill_roots=[])
    assert producer_profile_args(project,configurations=[config],skill_roots=[])==first


@pytest.mark.parametrize('stage',STAGE_SKILLS)
def test_stage_exposes_only_its_skills_and_disables_mcp_and_delegation(profile,stage):
    project,required,outside,extra,config=profile;before=deepcopy(config)
    config['model']='inherited-model';config['model_reasoning_effort']='inherited-effort'
    args=producer_profile_args(project,stage=stage,configurations=[config],
        skill_roots=[project/'.agents/skills',outside[0].parent.parent])
    values=parse(args)
    assert values['mcp_servers.via_manager.enabled'] is False
    assert values['mcp_servers.node_repl.enabled'] is False
    assert values['mcp_servers.via_manager.enabled_tools']==[]
    assert values['mcp_servers.via_manager.env.VIA_MCP_ALLOWED_TOOLS']=='[]'
    assert values['features.multi_agent'] is False
    assert values['features.image_generation'] is (stage=='image')
    assert values['features.apps'] is False
    assert all(row['enabled'] is False for row in values['plugins'].values())
    assert all(row['enabled'] is False for row in values['apps'].values())
    assert values['sandbox_workspace_write.network_access'] is True
    skills={Path(row['path']):row['enabled'] for row in values['skills.config']}
    wanted={project/'.agents/skills'/name/'SKILL.md' for name in STAGE_SKILLS[stage]}
    assert {path for path,enabled in skills.items() if enabled}==wanted
    assert all(skills[path] is False for path in [*outside,extra,*[p for p in required if p not in wanted]])
    assert not any(key in values for key in ('model','model_provider','model_reasoning_effort','profile'))
    assert 'NEVER_COPY' not in str(args) and 'private-transport' not in str(args)
    config.pop('model');config.pop('model_reasoning_effort');assert config==before


@pytest.mark.parametrize('stage',STAGE_SKILLS)
def test_stage_validates_required_package_without_demanding_unrelated_pipeline_skills(profile,stage):
    project,required,_,_,config=profile
    unrelated=next(path for path in required if path.parent.name not in STAGE_SKILLS[stage])
    unrelated.unlink()
    values=parse(producer_profile_args(project,stage=stage,configurations=[config],skill_roots=[]))
    assert sum(row['enabled'] is True for row in values['skills.config'])==len(STAGE_SKILLS[stage])
    required_path=project/'.agents/skills'/STAGE_SKILLS[stage][0]/'SKILL.md'
    required_path.write_text('---\nname: substituted-skill\n---\n',encoding='utf-8')
    with pytest.raises(ValueError,match='producer_skill_unavailable'):
        producer_profile_args(project,stage=stage,configurations=[config],skill_roots=[])


@pytest.mark.parametrize('stage',STAGE_SKILLS)
def test_stage_needs_no_via_transport_and_disables_every_configured_server(profile,stage):
    project,_,_,_,_=profile
    values=parse(producer_profile_args(project,stage=stage,
        configurations=[{'mcp_servers':{'extra':{'command':'PRIVATE'}}}],skill_roots=[]))
    assert values['mcp_servers.extra.enabled'] is False
    assert not any(key.startswith('mcp_servers.via_manager.') for key in values)
    assert 'PRIVATE' not in str(values)
    empty=parse(producer_profile_args(project,stage=stage,configurations=[{}],skill_roots=[]))
    assert not any(key.startswith('mcp_servers.') for key in empty)


@pytest.mark.parametrize('stage',['unknown','Image','producer_image','',False,[],{}])
def test_unknown_stage_fails_closed_before_filesystem_or_config_selection(stage,tmp_path):
    with pytest.raises(ValueError,match='producer_stage_invalid'):
        producer_profile_args(tmp_path/'missing',stage=stage,configurations=[{}],skill_roots=[])


def test_explicit_none_preserves_legacy_profile(profile):
    project,_,_,_,config=profile
    old=producer_profile_args(project,configurations=[config],skill_roots=[])
    assert producer_profile_args(project,stage=None,configurations=[config],skill_roots=[])==old
    assert 'features.multi_agent' not in parse(old)

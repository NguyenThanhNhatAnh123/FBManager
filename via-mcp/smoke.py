"""Read-only MCP process test. --live adds only cached local HTTP reads."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
EXPECTED_TOOLS=61


def configuration_check(root=ROOT, *, runner=subprocess.run, find=shutil.which):
    """Ask the installed CLI for its effective entry; never echo env/args/errors."""
    command=find('codex')
    if not command:return {'status':'unavailable','code':'codex_cli_not_found'}
    try:
        result=runner([command,'mcp','get','via_manager','--json'],stdout=subprocess.PIPE,
                      stderr=subprocess.PIPE,timeout=20,cwd=root)
        if result.returncode:return {'status':'unverified','code':'codex_mcp_get_failed'}
        value=json.loads(result.stdout)
        transport=value.get('transport',{})
        args=transport.get('args',[])
        expected_python=root.parent/'chrome-manager'/'.venv'/'Scripts'/'python.exe'
        normalize=lambda path:os.path.normcase(os.path.abspath(path))
        matches=(transport.get('type')=='stdio' and isinstance(transport.get('command'),str)
                 and normalize(transport['command'])==normalize(expected_python)
                 and isinstance(args,list) and all(isinstance(arg,str) for arg in args)
                 and len(args)>=2 and args[0]=='-I' and normalize(args[1])==normalize(root/'server.py'))
        # A different backend port must not be mistaken for this script's port 8000 probe.
        if matches:
            seen_port=False
            for index in range(2,len(args),2):
                if index+1>=len(args) or args[index] not in ('--port','--media-root'):
                    matches=False;break
                if args[index]=='--port':
                    if seen_port or args[index+1]!='8000':matches=False;break
                    seen_port=True
        enabled=value.get('enabled',True) is True
        report={'status':'passed' if matches and enabled else 'failed',
                'registered':True,'enabled':enabled,'expected_target':matches}
        for key in ('startup_timeout_sec','tool_timeout_sec'):
            number=value.get(key)
            if type(number) in (int,float) and 0<number<86400:report[key]=number
        return report
    except (OSError,subprocess.SubprocessError,ValueError,TypeError,AttributeError):
        return {'status':'unverified','code':'codex_config_check_failed'}


def transport_check(result):
    """Return bounded evidence, never subprocess stderr or a raw MCP response."""
    if result.returncode or result.stderr:
        return {'status':'failed','code':'mcp_process_failed'},None
    try:
        messages=[json.loads(line) for line in result.stdout.splitlines()]
        by_id={message['id']:message for message in messages}
        assert by_id[0]['error']['code']==-32601
        assert by_id[1]['result']['protocolVersion']=='2025-11-25'
        tools=by_id[2]['result']['tools']
        assert len(tools)==EXPECTED_TOOLS and len({tool['name'] for tool in tools})==EXPECTED_TOOLS
        assert by_id[3]['result']['structuredContent']['ok'] is True
    except (ValueError,KeyError,TypeError,AssertionError):
        return {'status':'failed','code':'mcp_handshake_or_catalog_failed'},None
    return {'status':'passed','protocol_version':'2025-11-25','tool_count':len(tools)},by_id


def backend_check(by_id):
    try:
        health=by_id[4]['result']['structuredContent']
        managers=health['result']['managers']
        statuses={scope:{'available':managers[scope].get('available') is True} for scope in ('chrome','pages')}
        for scope in statuses:
            if not statuses[scope]['available']:
                code=managers[scope].get('code')
                statuses[scope]['code']=code if code in ('manager_unavailable','session_failed','upstream_rejected','invalid_response','response_too_large') else 'backend_read_failed'
        counts={key:by_id[number]['result']['structuredContent'].get('result',{}).get('total')
                for number,key in ((5,'page_count'),(6,'profile_count'))}
        passed=all(item['available'] for item in statuses.values()) and all(type(count) is int and count>=0 for count in counts.values())
        return {'status':'passed' if passed else 'unavailable_from_this_process','managers':statuses,
                **{key:count if type(count) is int and count>=0 else None for key,count in counts.items()}}
    except (KeyError,TypeError,AttributeError):
        return {'status':'failed','code':'invalid_backend_diagnostic_response'}


def main():
    parser=argparse.ArgumentParser(description='Read-only connection diagnostic: CLI config, isolated MCP transport, then cached backend reads.')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--check-codex-config',action='store_true',help='Read via_manager registration using codex mcp get; never changes config.')
    args=parser.parse_args()
    checks={'configuration':configuration_check() if args.check_codex_config else {'status':'not_requested'},
            'transport':{'status':'not_run'},'backend':{'status':'not_requested' if not args.live else 'not_run'}}
    report={'live_requested':args.live,'checks':checks,
            'scope':'Isolated stdio process. Passing this check does not prove tools were loaded into an existing Codex Desktop chat.'}
    calls=[{'jsonrpc':'2.0','id':0,'method':'server/discover','params':{}},
           {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'via-smoke','version':'1'}}},
           {'jsonrpc':'2.0','method':'notifications/initialized'},
           {'jsonrpc':'2.0','id':2,'method':'tools/list','params':{'cursor':None,'_meta':{'progressToken':2}}},
           {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'via_new_operation_id','arguments':{}}}]
    if args.live:
        calls.extend([{'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'via_status','arguments':{}}},
                      {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'pages_list','arguments':{'limit':1}}},
                      {'jsonrpc':'2.0','id':6,'method':'tools/call','params':{'name':'chrome_list_profiles','arguments':{'limit':1,'include_bm':True}}}])
    by_id=None
    try:
        result=subprocess.run([sys.executable,'-I',str(ROOT/'server.py')],input=b''.join(json.dumps(call).encode()+b'\n' for call in calls),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,cwd=ROOT)
        checks['transport'],by_id=transport_check(result)
    except (OSError,subprocess.SubprocessError):
        checks['transport']={'status':'failed','code':'mcp_process_start_or_timeout'}
    if by_id is not None:
        report.update(stdio_initialize='passed',legacy_discovery_fallback='passed',tool_count=checks['transport']['tool_count'])
        if args.live:
            checks['backend']=backend_check(by_id)
            report['live_reads_passed']=checks['backend']['status']=='passed'
            report['live_managers']=checks['backend'].get('managers',{})
            for key in ('page_count','profile_count'):report[key]=checks['backend'].get(key)
    output=ROOT/'.runtime';output.mkdir(exist_ok=True)
    (output/('smoke-live.json' if args.live else 'smoke.json')).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True))
    if checks['configuration']['status'] not in ('passed','not_requested'):return 3
    if checks['transport']['status']!='passed':return 1
    return 0 if not args.live or report.get('live_reads_passed') else 2


if __name__=='__main__':sys.exit(main())

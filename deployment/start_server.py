"""Foreground supervisor for account-bound loopback Managers and their shared gateway."""
import argparse
import http.client
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deployment.install import InstallError,absolute,lock_existing,public_origin,safe_path
from deployment.workspaces import SUPERVISOR_LOCK,plan_workspaces,prepare_workspaces


MACHINE_ENV = frozenset(('SYSTEMROOT','SYSTEMDRIVE','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP',
    'APPDATA','LOCALAPPDATA','USERPROFILE','HOMEDRIVE','HOMEPATH','USERNAME','USERDOMAIN','PROGRAMFILES',
    'PROGRAMFILES(X86)','PROGRAMW6432','PROGRAMDATA','ALLUSERSPROFILE','PROCESSOR_ARCHITECTURE','OS'))


def read_config(root):
    path=safe_path(root/'deployment/server-config.json',root)
    value=json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value,dict) or set(value)!={'version','bind_host','port','manager_port','public_origin','allow_http_loopback'}
        or type(value['version']) is not int or value['version']!=1 or value['bind_host']!='127.0.0.1'
        or type(value['port']) is not int or not 1024<=value['port']<=65535
        or type(value['manager_port']) is not int or not 1024<=value['manager_port']<=65535
        or value['manager_port']==value['port'] or type(value['allow_http_loopback']) is not bool):
        raise InstallError('invalid_server_config')
    value['public_origin']=public_origin(value['public_origin'])
    return value


def manager_state(root,python,environment=None):
    result=subprocess.run([str(python),'-B',str(root/'chrome-manager/scripts/startup_state.py')],
        cwd=str(root),env=environment,capture_output=True,text=True,timeout=10,check=False,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise InstallError('manager_state_unavailable')
    try:value=json.loads(result.stdout)
    except ValueError:raise InstallError('manager_state_unavailable') from None
    if not isinstance(value,dict) or value.get('state') not in ('running','stopped','busy','unavailable'):
        raise InstallError('manager_state_unavailable')
    return value


def gateway_ready(config):
    connection=http.client.HTTPConnection('127.0.0.1',config['port'],timeout=1)
    host=urlsplit(config['public_origin']).netloc if config['public_origin'] else '127.0.0.1:'+str(config['port'])
    try:
        connection.request('GET','/health',headers={'Host':host,'Connection':'close'})
        response=connection.getresponse();raw=response.read(1025)
        if response.status!=200 or len(raw)>1024:return False
        value=json.loads(raw)
        return isinstance(value,dict) and value.get('name')=='via-remote-mcp' and value.get('ready') is True
    except (OSError,ValueError,http.client.HTTPException):return False
    finally:connection.close()


def gateway_owned(root,config,launcher_pid=None):
    """A matching JSON response alone cannot identify a host from this release root."""
    try:
        import psutil
        listeners={entry.pid for entry in psutil.net_connections(kind='tcp')
            if entry.status==psutil.CONN_LISTEN and entry.laddr and entry.laddr.port==config['port']
            and entry.laddr.ip in ('127.0.0.1','0.0.0.0','::','::ffff:127.0.0.1')}
        if len(listeners)!=1 or None in listeners:return False
        pid=next(iter(listeners))
        if launcher_pid is not None and not child_listener(pid,launcher_pid):return False
        process=psutil.Process(pid)
        if not re.fullmatch(r'python(?:w|[0-9](?:\.[0-9]+)?)?(?:\.exe)?',Path(process.exe()).name,re.IGNORECASE):return False
        arguments=process.cmdline()[1:];index=0
        while index<len(arguments):
            argument=arguments[index]
            if argument.startswith(('-c','-m')) or argument in ('-','--help','--version'):return False
            if argument in ('-W','-X'):index+=2;continue
            if argument.startswith('-'):index+=1;continue
            script=Path(argument)
            if not script.is_absolute():script=Path(process.cwd())/script
            if script.resolve()!=(root/'via-mcp/remote-server.py').resolve():return False
            if arguments[index+1:index+2]!=['--config'] or index+2>=len(arguments):return False
            configured=Path(arguments[index+2])
            if not configured.is_absolute():configured=Path(process.cwd())/configured
            return configured.resolve()==(root/'deployment/server-config.json').resolve() and gateway_ready(config)
        return False
    except Exception:return False


def port_free(port):
    with socket.socket() as connection:
        connection.settimeout(.25)
        return connection.connect_ex(('127.0.0.1',port))!=0


def core_environment(root,user_id,binding,environment=None):
    source=os.environ if environment is None else environment
    primary=binding['root']=='.'
    # Inherit the original application's environment only for the original data
    # root. Operators start from defaults and load their own local .env, if any.
    child={key:value for key,value in source.items() if primary or key.upper() in MACHINE_ENV}
    for key in list(child):
        if key.upper().startswith('VIA_WORKSPACE_') or key.upper() in ('CM_PORT','CM_OPEN_DASHBOARD'):
            child.pop(key)
    child.update(CM_PORT=str(binding['manager_port']),CM_OPEN_DASHBOARD='false',
        VIA_WORKSPACE_USER_ID=str(user_id),VIA_WORKSPACE_AUTH_DIR=str(root/'via-auth/data'),
        VIA_WORKSPACE_PRIMARY='1' if primary else '0')
    if not primary:
        child['CM_DATA_DIR']='data'
    return child


def _core_state(root,python,user_id,binding):
    workspace=safe_path(root/binding['root'],root)
    port=binding['manager_port']
    if not (workspace/'chrome-manager/scripts/startup_state.py').is_file():
        return {'state':'stopped' if port_free(port) else 'unavailable','port':port}
    return manager_state(workspace,python,core_environment(root,user_id,binding))


def child_listener(pid,launcher_pid):
    if type(pid) is not int or pid<=0:
        return False
    try:
        # A Windows venv executable may spawn the base interpreter; recognize
        # either the child itself or its listener descendant, never a reused core.
        import psutil
        return pid==launcher_pid or launcher_pid in {parent.pid for parent in psutil.Process(pid).parents()}
    except Exception:
        return False


def core_owned(port,user_id,pid,launcher_pid):
    if not child_listener(pid,launcher_pid):
        return False
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=1)
    try:
        connection.request('GET','/_via/workspace',headers={'Connection':'close'})
        response=connection.getresponse();raw=response.read(1025)
        if response.status!=200 or len(raw)>1024:
            return False
        value=json.loads(raw)
        return (isinstance(value,dict) and set(value)=={'workspace_user_id','pid'}
            and type(value['workspace_user_id']) is int and value['workspace_user_id']==user_id
            and type(value['pid']) is int and value['pid']==pid)
    except (OSError,ValueError,http.client.HTTPException):
        return False
    finally:
        connection.close()


def graceful_stop(processes,*,timeout=45):
    # Signal only children created by this supervisor. Never kill an existing
    # installation, reused Manager or unrelated process on a configured port.
    for process in reversed(processes):
        if process.poll() is None:
            try:process.send_signal(signal.CTRL_BREAK_EVENT if os.name=='nt' else signal.SIGINT)
            except OSError:pass
    deadline=time.monotonic()+timeout
    for process in processes:
        try:process.wait(timeout=max(.01,deadline-time.monotonic()))
        except subprocess.TimeoutExpired:pass
    return all(process.poll() is not None for process in processes)


def run(root,*,check_only=False):
    root=absolute(root);config=read_config(root)
    python=safe_path(root/'chrome-manager/.venv/Scripts/python.exe',root)
    if not python.is_file():raise InstallError('install_server_runtime_first')
    machine=subprocess.run([str(python),'-I','-B',str(root/'deployment/preflight.py'),'--root',str(root)],
        capture_output=True,text=True,timeout=30,check=False,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    try:preflight=json.loads(machine.stdout)
    except ValueError:raise InstallError('machine_preflight_failed') from None
    bindings=plan_workspaces(root,config['manager_port'],config['port'])
    states={uid:_core_state(root,python,uid,binding) for uid,binding in bindings.items()}
    for uid,state in states.items():
        if state.get('port')!=bindings[uid]['manager_port']:
            raise InstallError('manager_port_does_not_match_server_config')
    primary=next(uid for uid,binding in bindings.items() if binding['root']=='.')
    gateway=gateway_owned(root,config)
    report={'preflight':preflight,'manager_state':states[primary]['state'],'gateway_ready':gateway,
            'manager_port':config['manager_port'],'gateway_port':config['port'],
            'public_access_configured':config['public_origin'] is not None,'check_only':check_only,
            'workspaces':[{'user_id':uid,**binding,'state':states[uid]['state']} for uid,binding in bindings.items()]}
    if check_only:
        print(json.dumps(report),flush=True);return 0 if preflight.get('ready') else 1
    if not preflight.get('ready'):raise InstallError('machine_preflight_not_ready')

    # Keep the shared installer/supervisor lock until every child has shut down.
    # Confirm idle state again inside it, then provision before launching anything.
    with lock_existing(safe_path(root/SUPERVISOR_LOCK,root)):
        for uid,binding in bindings.items():
            if states[uid]['state']!='stopped' or _core_state(root,python,uid,binding)['state']!='stopped':
                raise InstallError('workspace_core_running_or_unverified_stop_it_first')
        if gateway or not port_free(config['port']):
            raise InstallError('gateway_port_in_use_or_unverified')
        ready=prepare_workspaces(root,config['manager_port'],config['port'])
        if ready!=bindings:
            raise InstallError('workspace_identity_mapping_changed')
        return supervise(root,python,config,bindings,report)


def supervise(root,python,config,bindings,report):
    children=[]
    def launch(argv,cwd,environment):
        # Inherit this terminal; no extra GUI window is opened. The process group
        # allows Ctrl+C in the supervisor to request uvicorn's graceful shutdown.
        flags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
        process=subprocess.Popen(list(map(str,argv)),cwd=str(cwd),env=environment,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
        children.append(process);return process
    def wait_until(predicate,process,label):
        deadline=time.monotonic()+25
        while time.monotonic()<deadline:
            if process.poll() is not None:raise InstallError(label+'_exited_before_ready')
            if predicate():return
            time.sleep(.25)
        raise InstallError(label+'_startup_timeout')
    try:
        for uid,binding in bindings.items():
            workspace=safe_path(root/binding['root'],root)
            environment=core_environment(root,uid,binding)
            core=launch([python,'-B',workspace/'chrome-manager/backend/run.py'],workspace/'chrome-manager/backend',environment)
            def ready():
                state=manager_state(workspace,python,environment)
                return state['state']=='running' and state.get('port')==binding['manager_port'] and core_owned(
                    binding['manager_port'],uid,state.get('pid'),core.pid)
            wait_until(ready,core,'workspace_core')
        environment={key:value for key,value in os.environ.items() if not key.upper().startswith('VIA_WORKSPACE_')}
        remote=launch([python,'-B',root/'via-mcp/remote-server.py','--config',root/'deployment/server-config.json'],root,environment)
        wait_until(lambda:gateway_owned(root,config,remote.pid),remote,'gateway')
        print(json.dumps({**report,'manager_state':'running','gateway_ready':True,'supervising_children':len(children),
            'workspaces':[{'user_id':uid,**binding,'state':'running'} for uid,binding in bindings.items()],
            'stop':'Ctrl+C in this terminal; graceful shutdown only'}),flush=True)
        while all(process.poll() is None for process in children):time.sleep(.5)
        raise InstallError('managed_process_exited')
    except KeyboardInterrupt:
        stopped=graceful_stop(children)
        print(json.dumps({'graceful_shutdown_complete':stopped,'force_termination_performed':False}),flush=True)
        return 0 if stopped else 1
    except BaseException:
        graceful_stop(children)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--check-only',action='store_true')
    args=parser.parse_args()
    try:return run(args.root,check_only=args.check_only)
    except Exception as error:
        print(json.dumps({'error':str(error) if isinstance(error,InstallError) else 'server_start_failed'}),file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())

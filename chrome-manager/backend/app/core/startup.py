"""Bounded, read-only recognition of this installation's existing local host."""
import errno
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

import psutil


def _listeners(port):
    return sorted({entry.pid for entry in psutil.net_connections(kind='tcp')
                   if entry.status==psutil.CONN_LISTEN and entry.laddr and entry.laddr.port==port
                   and entry.laddr.ip in ('127.0.0.1','0.0.0.0','::','::ffff:127.0.0.1')},key=lambda pid:pid or 0)


def _same_host(pid,root):
    process=psutil.Process(pid)
    if not re.fullmatch(r'python(?:w|[0-9](?:\.[0-9]+)?)?(?:\.exe)?',Path(process.exe()).name,re.IGNORECASE):return False
    arguments=process.cmdline()[1:]
    index=0
    while index<len(arguments):
        argument=arguments[index]
        if argument.startswith(('-c','-m')) or argument in ('-','--help','--version'):return False
        if argument in ('-W','-X'):
            index+=2;continue
        if argument.startswith('-'):
            index+=1;continue
        candidate=Path(argument)
        if not candidate.is_absolute():candidate=Path(process.cwd())/candidate
        return candidate.resolve()==(Path(root)/'backend'/'run.py').resolve()
    return False


def _service_headers():
    """Load the protected key without logging it or accepting caller-supplied auth.

    A source installation without the identity package can still recognize its
    historical loopback host. An installed but unsafe credential fails closed.
    """
    auth_root=Path(__file__).resolve().parents[4]/'via-auth'
    if not auth_root.is_dir():return {}
    if str(auth_root) not in sys.path:sys.path.insert(0,str(auth_root))
    try:
        from via_auth.client import service_headers
        return service_headers()
    except (ImportError,OSError,ValueError) as error:
        raise ValueError('Local service credential unavailable') from None
    except Exception:
        # Identity path/DACL errors contain no useful public probe metadata.
        raise ValueError('Local service credential unavailable') from None


def _session_ready(port,timeout):
    connection=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        connection.request('GET','/api/session',headers={'Connection':'close',**_service_headers()})
        response=connection.getresponse()
        payload=response.read(257)
        if response.status!=200 or len(payload)>256:return False
        value=json.loads(payload)
        if not isinstance(value,dict) or value.get('ok') is not True or set(value)-{'ok','rest_api_version'}:
            return False
        return ('rest_api_version' not in value
                or type(value['rest_api_version']) is int and value['rest_api_version']==1)
    finally:connection.close()


def _lock_held(path):
    """Probe the same first-byte lock without creating/truncating the lock file."""
    try:before=path.lstat()
    except FileNotFoundError:return False
    if not stat.S_ISREG(before.st_mode) or path.is_symlink() or before.st_nlink!=1:raise ValueError('Unsafe lock')
    with path.open('r+b') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):raise ValueError('Changed lock')
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:
                if error.errno in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):return True
                raise
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return True
            fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
    return False


def inspect_existing_instance(config,*,timeout=2.0,listeners=None,same_host=None,lock_probe=None,
                              http_probe=None,monotonic=time.monotonic,sleep=time.sleep):
    """Only reuse a matching listener, process command and held application lock.

    Probe callables are injectable so tests never touch a live process, socket or
    production database. Returned metadata never contains command lines or cookies.
    """
    if type(timeout) not in (int,float) or not 0<=timeout<=5:raise ValueError('Invalid startup timeout')
    port=config.port
    if type(port) is not int or not 1024<=port<=65535:raise ValueError('Invalid startup port')
    listeners=listeners or _listeners;same_host=same_host or _same_host
    lock_probe=lock_probe or _lock_held;http_probe=http_probe or _session_ready
    deadline=monotonic()+timeout
    while True:
        try:
            held=lock_probe(Path(config.data_dir)/'manager.lock')
            pids=listeners(port)
            if not pids:
                result={'state':'busy' if held else 'stopped','port':port,
                        'reason':'instance_starting' if held else 'no_instance'}
            elif len(pids)!=1 or type(pids[0]) is not int or pids[0]<=0:
                return {'state':'unavailable','port':port,'reason':'instance_identity_unverified'}
            else:
                pid=pids[0]
                if not same_host(pid,config.root):
                    return {'state':'unavailable','port':port,'reason':'port_in_use'}
                if not held:
                    return {'state':'unavailable','port':port,'reason':'instance_identity_unverified'}
                try:ready=http_probe(port,max(.05,min(.35,deadline-monotonic())))
                except (OSError,ValueError,http.client.HTTPException):ready=False
                if ready:return {'state':'running','port':port,'pid':pid,'reason':'existing_instance'}
                result={'state':'busy','port':port,'pid':pid,'reason':'instance_starting'}
        except (OSError,ValueError,psutil.Error):
            return {'state':'unavailable','port':port,'reason':'instance_probe_unavailable'}
        remaining=deadline-monotonic()
        if result['state']!='busy' or remaining<=0:return result
        sleep(min(.1,remaining))

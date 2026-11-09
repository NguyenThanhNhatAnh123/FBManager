"""Loopback gateway entry point; the reverse proxy owns external HTTPS."""
import argparse
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT.parent/'via-auth'))
sys.path.insert(0,str(ROOT.parent))


def main():
    parser=argparse.ArgumentParser(description='Private Via server and Codex gateway')
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    if sys.version_info<(3,12):raise ValueError('Python 3.12 or later is required')
    from filelock import FileLock
    from via_auth.store import _assert_private_path
    from via_mcp.remote_app import create_app,load_config,private_folder
    from deployment.workspaces import load_workspaces
    import uvicorn
    config=load_config(args.config)
    workspaces=load_workspaces(ROOT.parent,config['manager_port'])
    folder=private_folder(ROOT/'data');path=folder/'remote-server.lock'
    _assert_private_path(path,missing=True)
    lock=FileLock(str(path));lock.acquire(timeout=0)
    try:
        app=create_app(ROOT.parent,config,workspaces=workspaces)
        uvicorn.run(app,host=config['bind_host'],port=config['port'],access_log=False,
                    proxy_headers=False,server_header=False,log_level='warning',limit_concurrency=32,
                    timeout_keep_alive=10,timeout_graceful_shutdown=30)
    finally:lock.release()


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,BrokenPipeError):pass
    except Exception:
        sys.stderr.write('Via gateway could not start. Check server configuration, Python runtime and private file permissions.\n')
        raise SystemExit(1)

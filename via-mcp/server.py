"""Launch with the existing app venv. stdout is exclusively MCP JSON-RPC."""
import argparse
import os
from pathlib import Path
import sys

# Works under Python -I; never import code from the current working directory.
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))


def main():
    from via_mcp.bridge import ManagerClient
    from via_mcp.media import MediaVault
    from via_mcp.journal import Journal
    from via_mcp.registry import Registry,parse_allowed_tools
    from via_mcp.protocol import Protocol
    parser=argparse.ArgumentParser(description='Local Via Manager stdio MCP')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--media-root',action='append',default=None)
    args=parser.parse_args()
    allowed_tools=parse_allowed_tools(os.environ.get('VIA_MCP_ALLOWED_TOOLS'))
    media=MediaVault(args.media_root or [ROOT.parent/'mcp-media'])
    auth_root=ROOT.parent/'via-auth'
    service_token=None
    if auth_root.is_dir():
        sys.path.insert(0,str(auth_root))
        from via_auth.client import service_headers
        service_token=service_headers().get('X-Via-Service-Token')
    client=ManagerClient(args.port,media,service_token=service_token)
    try:
        # Validate capability policy before opening the durable write journal.
        registry=Registry(client,media,None,allowed_tools=allowed_tools)
        registry.context.journal=Journal(ROOT)
        protocol=Protocol(registry)
        protocol.serve(sys.stdin.buffer,sys.stdout.buffer)
    finally:client.close()


if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,BrokenPipeError):pass
    except Exception:
        sys.stderr.write('Via MCP could not start. Check the existing app Python runtime and local file permissions.\n')
        sys.exit(1)

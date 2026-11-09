"""Check Codex's real MCP client without starting a chat, turn, or producer.

Only initialize, MCP discovery and optional local CLI login status are requested.
Raw server output, credentials, prompts, and tool results are never persisted.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import queue
import subprocess
import threading
import time


class ProbeError(Exception):
    pass


class CodexClient:
    def __init__(self, executable, timeout, config_args=()):
        self.timeout = timeout
        self.sequence = 0
        self.messages = queue.Queue()
        self.process = subprocess.Popen(
            [str(executable), *config_args, "app-server", "--stdio"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except (ValueError, UnicodeError):
                    self.messages.put({"probe_error": "invalid_json"})
        finally:
            self.messages.put({"probe_error": "server_closed"})

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise ProbeError("request_timeout") from None
            if "probe_error" in message:
                raise ProbeError(message["probe_error"])
            if message.get("id") != request_id:
                # Never accept an interactive approval or authentication request.
                if "id" in message and "method" in message:
                    self.send({"id": message["id"], "error": {
                        "code": -32601, "message": "Noninteractive read-only probe"}})
                continue
            if "error" in message:
                raise ProbeError("rpc_error_" + str(message["error"].get("code", "unknown")))
            return message.get("result", {})

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", type=Path, required=True, help="Absolute existing codex.exe")
    parser.add_argument("--server", default="via_manager")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--check-login", action="store_true", help="Check local CLI authentication without printing credentials")
    parser.add_argument("--role", choices=("coordinator", "producer", "qa", "publisher"))
    parser.add_argument("--inspect-skills", type=Path, help="Read enabled skill metadata for one project")
    args = parser.parse_args()
    if not args.cli.is_absolute() or not args.cli.is_file() or args.timeout < 1:
        parser.error("--cli must be an absolute existing executable; timeout must be positive")
    config_args = []
    if args.role:
        role_file = Path(__file__).resolve().parents[1] / 'meta-page-manager/backend/page_manager/agent_roles.py'
        spec = importlib.util.spec_from_file_location('page_agent_roles', role_file)
        roles = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(roles)
        config_args = roles.codex_role_args(args.role)
    client = CodexClient(args.cli, args.timeout, config_args)
    passed = False
    try:
        client.call("initialize", {
            "clientInfo": {"name": "via_integration_probe", "version": "1.0.0"},
            "capabilities": {"experimentalApi": True, "explicitGatewayOauth": True},
        })
        client.send({"method": "initialized", "params": {}})
        result = client.call("mcpServerStatus/list", {
            "serverName": args.server, "detail": "toolsAndAuthOnly", "cursor": None,
        })
        for server in result.get("data", []):
            if server.get("name") != args.server:
                continue
            tools = server.get("tools", {})
            passed = bool(tools) and not server.get("toolsError")
            print(json.dumps({
                "check": "codex_mcp_client", "server": args.server,
                "role": args.role or "configured_default",
                "discovery_passed": passed, "tool_count": len(tools),
                "has_via_status": "via_status" in tools,
                "auth_status": server.get("authStatus"),
                "runtime_status": server.get("runtimeStatus"),
                "capabilities_received": server.get("serverCapabilities") is not None,
                "discovery_error": bool(server.get("toolsError")),
            }))
        if not result.get("data"):
            print(json.dumps({"check": "codex_mcp_client", "discovery_passed": False,
                              "error": "server_not_found"}))
        if args.check_login:
            try:
                login = subprocess.run([str(args.cli), "login", "status"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=min(args.timeout, 15),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                authenticated = login.returncode == 0
                print(json.dumps({"check": "cli_authentication", "verified": authenticated,
                                  "exit_code": login.returncode}))
                passed = passed and authenticated
            except (OSError, subprocess.TimeoutExpired):
                passed = False
                print(json.dumps({"check": "cli_authentication", "verified": False,
                                  "error": "login_probe_unavailable"}))
        if args.inspect_skills:
            result = client.call('skills/list', {'cwds': [str(args.inspect_skills.resolve())], 'forceReload': True})
            rows = [skill for project in result.get('data', []) for skill in project.get('skills', [])]
            active = [skill for skill in rows if skill.get('enabled') is True]
            retired = {'flow-3d-shirt-video', 'grok-apparel-action-reel', 'grok-four-scene-rug-reel',
                       'grok-reference-video', 'grok-rug-product-video', 'kb-viral-1', 'kb-viral-2',
                       'noel-video-prompts', 'noel-video-workflow', 'pod-video-ads', 'pod-shirt-pipeline'}
            names = [skill.get('name') for skill in active]
            retired_enabled = sorted((set(names) & retired) | {name for name in names if str(name).startswith('remotion')})
            print(json.dumps({'check': 'project_skill_discovery', 'active_skill_count': len(active),
                'page_canonical_enabled': 'page-shirt-zoom-8s' in names,
                'retired_workflows_enabled': retired_enabled,
                'duplicate_active_names': sorted({name for name in names if names.count(name) > 1})}))
            passed = passed and 'page-shirt-zoom-8s' in names and not retired_enabled
        return 0 if passed else 1
    except ProbeError as error:
        print(json.dumps({"check": "codex_mcp_client", "discovery_passed": False,
                          "error": str(error)}))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only local discovery using an isolated CODEX_HOME; never starts a model turn."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import queue
import threading
import time


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def runtime_probe(executable, workspace, environment):
    process = subprocess.Popen([str(executable), "--no-daemon", "--enable", "plugins", "-C", str(workspace), "app-server", "--stdio"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment, cwd=workspace, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    messages = queue.Queue()
    def read():
        for line in process.stdout:
            messages.put(json.loads(line))
    threading.Thread(target=read, daemon=True).start()
    sequence = 0
    def send(value):
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()
    def call(method, params):
        nonlocal sequence
        sequence += 1
        send({"id": sequence, "method": method, "params": params})
        deadline = time.monotonic() + 40
        while True:
            message = messages.get(timeout=max(0.01, deadline - time.monotonic()))
            if message.get("id") == sequence:
                if "error" in message:
                    raise RuntimeError("App-server RPC failed: " + method + " " + json.dumps(message["error"]))
                return message.get("result", {})
            if "id" in message and "method" in message:
                send({"id": message["id"], "error": {"code": -32601, "message": "Read-only fixture probe"}})
    try:
        call("initialize", {"clientInfo": {"name": "via_private_fixture", "version": "1.0.0"}, "capabilities": {"experimentalApi": True}})
        send({"method": "initialized", "params": {}})
        plugins = call("plugin/list", {"cwds": [str(workspace)], "forceRefetch": False})
        plugin = call("plugin/read", {"marketplacePath": str(workspace / ".agents" / "plugins" / "marketplace.json"), "pluginName": "via"})
        status = call("mcpServerStatus/list", {"detail": "toolsAndAuthOnly", "cursor": None})
        skills = call("skills/list", {"cwds": [str(workspace)], "forceReload": True})
        config = call("config/read", {"includeLayers": False})
        servers = [{"name": row.get("name"), "tool_count": len(row.get("tools", {})), "error": bool(row.get("toolsError")), "error_detail": row.get("toolsError")} for row in status.get("data", []) if "via" in row.get("name", "")]
        matched = [s for row in skills.get("data", []) for s in row.get("skills", []) if s.get("name") in {"via-manager", "via:via-manager"}]
        result = {"servers": servers, "via_skill_count": len(matched), "via_skill_enabled": all(s.get("enabled") for s in matched)}
        if not servers or not all(s["tool_count"] == 2 and not s["error"] for s in servers) or len(matched) != 1 or not result["via_skill_enabled"]:
            raise RuntimeError("Installed Via MCP/skill discovery failed: " + json.dumps({**result, "config_keys": list(config), "plugins_config": config.get("config", {}).get("plugins"), "feature_plugins": config.get("config", {}).get("features", {}).get("plugins"), "plugins": plugins, "plugin": plugin}))
        return result
    finally:
        # Stop only this disposable app-server and its children before the parent exits.
        if os.name == "nt" and process.poll() is None:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        process.stdin.close()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def verify(executable: Path, portable=False):
    source = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="via-codex-discovery-") as folder:
        root = Path(folder)
        home = root / "codex-home"
        workspace = root / "marketplace"
        plugin = workspace / "plugins" / "via"
        home.mkdir()
        plugin.mkdir(parents=True)
        files = ("plugin.json", "mcp.json", ".codex-plugin/plugin.json", ".mcp.json", "skills/via-manager/SKILL.md", "skills/via-manager/agents/openai.yaml", "client/stdio_proxy.py", "client/private.py")
        for relative in files:
            destination = plugin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        fixture = _module("via_runtime_fixture", source / "tests" / "test_client.py")
        gateway = fixture.Gateway()
        credential = root / "private" / "token.bin"
        fixture.client.private.save_token(credential, fixture.TOKEN_A)
        (plugin / "client-config.json").write_text(json.dumps({"version": 1, "server_url": gateway.url, "credential_file": str(credential), "allow_loopback_http": True}), encoding="utf-8")
        import sys
        fixture.installer.materialize_manifests(plugin, plugin, Path(sys.executable), portable_layout=portable)
        catalog = workspace / ".agents" / "plugins" / "marketplace.json"
        catalog.parent.mkdir(parents=True)
        catalog.write_text(json.dumps({"name": "via-discovery-test", "interface": {"displayName": "Via disposable test"}, "plugins": [{"name": "via", "source": {"source": "local", "path": "./plugins/via"}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]}), encoding="utf-8")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(home)

        def run(*args):
            process = subprocess.run([str(executable), "-C", str(workspace), *args], env=environment, capture_output=True, text=True, encoding="utf-8", timeout=45, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                raise RuntimeError("Codex discovery command failed: " + process.stderr[:2000])
            return json.loads(process.stdout)

        try:
            added = run("plugin", "marketplace", "add", str(workspace), "--json")
            available = run("plugin", "list", "--marketplace", "via-discovery-test", "--available", "--json")
            installed = run("plugin", "add", "via@via-discovery-test", "--json")
            listed = run("plugin", "list", "--marketplace", "via-discovery-test", "--json")
            with (home / "config.toml").open("a", encoding="utf-8") as stream:
                stream.write("\n[projects." + json.dumps(str(workspace)) + "]\ntrust_level = \"trusted\"\n")
            runtime = runtime_probe(executable, workspace, environment)
            return {"isolated_codex_home": True, "available_plugin_count": len(available.get("available", [])), "installed_plugin_id": installed.get("pluginId"), "enabled": listed.get("installed", [{}])[0].get("enabled"), "runtime": runtime}
        finally:
            gateway.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--portable", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.codex, args.portable), indent=2))

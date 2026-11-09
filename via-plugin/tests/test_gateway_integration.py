"""Actual HTTP gateway plus client, with three disposable users and no real managers."""
import importlib.util
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "via-mcp"))
sys.path.insert(0, str(ROOT / "via-auth"))

try:
    import uvicorn
    from via_auth import IdentityStore
    from via_mcp.common import MutationInput, Tool
    from via_mcp.registry import basic_tools
    from via_mcp.remote_app import create_app
except ImportError:
    uvicorn = None

spec = importlib.util.spec_from_file_location("integration_via_client", ROOT / "via-plugin" / "client" / "stdio_proxy.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


@unittest.skipIf(uvicorn is None, "Run with the server's Python environment for actual gateway integration")
class GatewayIntegrationTests(unittest.TestCase):
    def test_three_personal_clients_roles_media_journals_and_revocation_over_http(self):
        with tempfile.TemporaryDirectory(prefix="via-real-gateway-test-") as folder:
            root = Path(folder)
            store = IdentityStore(root / "via-auth" / "data")
            accounts = store.bootstrap()
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(64)
            port = listener.getsockname()[1]
            url = "http://127.0.0.1:" + str(port) + "/mcp"
            tokens, user_ids, mutations = {}, {}, []
            for account in accounts:
                user = next(row for row in store.list_users() if row["username"] == account["username"])
                store.change_password(user["id"], account["password"], "Temporary-Changed-" + account["username"] + "-Password")
                user_ids[account["username"]] = user["id"]
                tokens[account["username"]] = store.create_access_token(user["id"], name="Disposable client integration", scopes=["via:read", "via:write"], audience=url)

            class Manager:
                def __init__(self, port, media, *, bearer_token):
                    self.raw = bearer_token
                def close(self):
                    pass
                def request(self, *args, **kwargs):
                    return {"stats": {"total": 3}}

            def write(context, args):
                who = next(name for name, pat in tokens.items() if pat["token"] == context.client.raw)
                mutations.append(who)
                return {"actor": who}

            catalog = basic_tools() + [
                Tool("chrome_stop_profile", "Fixture operator mutation", MutationInput, write, read_only=False),
                Tool("chrome_check_proxies", "Fixture admin mutation", MutationInput, write, read_only=False),
            ]
            config = {"version": 1, "bind_host": "127.0.0.1", "port": port, "manager_port": 8000, "public_origin": None, "allow_http_loopback": True}
            app = create_app(root, config, store=store, client_factory=Manager, catalog=catalog)
            server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical", lifespan="on", access_log=False))
            thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
            thread.start()
            proxies = {}
            try:
                deadline = time.monotonic() + 10
                while not server.started and thread.is_alive() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(server.started, "Disposable gateway failed to start")
                operation = str(uuid.uuid4())
                for name in ("admin", "operator1", "operator2"):
                    proxy = client.Proxy(url, tokens[name]["token"], allow_loopback_http=True, timeout=10)
                    proxies[name] = proxy
                    proxy.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "integration", "version": "1.1.0"}}})
                    proxy.call({"jsonrpc": "2.0", "method": "notifications/initialized"})
                    tools = proxy.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
                    self.assertEqual("chrome_check_proxies" in {t["name"] for t in tools}, name == "admin")
                    result = proxy.call({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "chrome_stop_profile", "arguments": {"operation_id": operation}}})["result"]["structuredContent"]
                    self.assertTrue(result["ok"])
                    self.assertEqual(result["result"]["actor"], name)
                    if name != "admin":
                        denied = proxy.call({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "chrome_check_proxies", "arguments": {"operation_id": str(uuid.uuid4())}}})["result"]["structuredContent"]
                        self.assertEqual(denied["code"], "tool_not_allowed")
                self.assertEqual(mutations, ["admin", "operator1", "operator2"])
                self.assertEqual(len({p.session for p in proxies.values()}), 3)
                self.assertEqual(len(list((root / "via-mcp" / "data" / "remote-users").glob("*/data/operations.sqlite3"))), 3)
                image = root / "art có dấu.png"
                image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test private bytes")
                media = proxies["operator2"].call({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "via_register_media", "arguments": {"file_path": str(image), "kind": "image"}}})["result"]["structuredContent"]
                self.assertTrue(media["ok"], json.dumps(media))
                self.assertEqual(media["result"]["name"], image.name)
                self.assertNotIn(str(root), json.dumps(media))
                store.revoke_access_token(user_ids["operator1"], tokens["operator1"]["id"])
                with self.assertRaises(client.ClientError) as revoked:
                    proxies["operator1"].call({"jsonrpc": "2.0", "id": 6, "method": "tools/list"})
                self.assertEqual(revoked.exception.code, "authentication")
                still_allowed = proxies["operator2"].call({"jsonrpc": "2.0", "id": 7, "method": "tools/list"})
                self.assertIn("result", still_allowed)
            finally:
                for proxy in proxies.values():
                    proxy.close()
                server.should_exit = True
                thread.join(10)
                listener.close()
                self.assertFalse(thread.is_alive(), "Disposable gateway did not stop")


if __name__ == "__main__":
    unittest.main()

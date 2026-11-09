from __future__ import annotations

import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import urlsplit
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


client = module("test_via_client", ROOT / "client" / "stdio_proxy.py")
installer = module("test_via_installer", ROOT / "client" / "install.py")
builder = module("test_via_builder", ROOT / "build_package.py")
TOKEN_A = "via_pat_" + "a" * 43
TOKEN_B = "via_pat_" + "b" * 43


class Gateway(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.records, self.sessions, self.uploads = [], {}, []
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server_port) + "/mcp"

    def stop(self):
        self.shutdown()
        self.server_close()
        self.thread.join(5)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def reply(self, value=None, status=200, headers=None):
        raw = json.dumps(value).encode() if value is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if raw:
            self.wfile.write(raw)

    def authorized(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if token not in {TOKEN_A, TOKEN_B}:
            self.reply(status=401)
            return None
        return token

    def session_ok(self, token):
        session = self.headers.get("Mcp-Session-Id")
        if self.server.sessions.get(session) != token or self.headers.get("MCP-Protocol-Version") != "2025-11-25":
            self.reply(status=403)
            return False
        return True

    def do_POST(self):
        token = self.authorized()
        if not token:
            return
        length = int(self.headers.get("Content-Length", 0))
        if self.path.startswith("/mcp/media?"):
            if not self.session_ok(token):
                return
            digest = hashlib.sha256()
            size, reads = 0, 0
            while size < length:
                chunk = self.rfile.read(min(32768, length - size))
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                reads += 1
            if digest.hexdigest() != self.headers.get("X-Content-SHA256"):
                self.reply(status=400)
                return
            upload = str(uuid.uuid4())
            self.server.uploads.append({"id": upload, "size": size, "reads": reads, "filename": self.headers.get("X-Filename"), "kind": urlsplit(self.path).query})
            self.reply({"upload_id": upload})
            return
        raw = self.rfile.read(length)
        body = json.loads(raw)
        self.server.records.append({"body": body, "token": token, "session": self.headers.get("Mcp-Session-Id"), "protocol": self.headers.get("MCP-Protocol-Version"), "accept": self.headers.get("Accept")})
        if body["method"] == "initialize":
            session = uuid.uuid4().hex
            self.server.sessions[session] = token
            self.reply({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "test", "version": "1"}}}, headers={"Mcp-Session-Id": session})
            return
        if not self.session_ok(token):
            return
        if "id" not in body:
            self.reply(status=204)
            return
        result = {"ok": True}
        if body["method"] == "tools/list":
            result = {"tools": [{"name": "read", "description": "Read fixture", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}}, {"name": "write", "description": "Write fixture", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}}]}
        if body["method"] == "tools/call":
            name = body["params"]["name"]
            if name in {"write_lost", "read_lost"}:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            if name == "redirect":
                self.reply(status=302, headers={"Location": "http://127.0.0.1:9/stolen"})
                return
            if name == "forbidden":
                self.reply(status=403)
                return
            if name == "echo_secret":
                result = {"content": [{"type": "text", "text": "Đã kết nối; bearer=" + token}], "structuredContent": {token: token}}
            if name == "via_register_media":
                result = {"content": [{"type": "text", "text": "registered"}], "structuredContent": body["params"]["arguments"]}
        self.reply({"jsonrpc": "2.0", "id": body["id"], "result": result})

    def do_DELETE(self):
        token = self.authorized()
        if token and self.session_ok(token):
            self.server.sessions.pop(self.headers["Mcp-Session-Id"], None)
            self.reply(status=204)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.proxy = client.Proxy(self.gateway.url, TOKEN_A, allow_loopback_http=True, timeout=3)
        self.temp = tempfile.TemporaryDirectory(prefix="via-client-test-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.gateway.stop)
        self.addCleanup(self.proxy.close)

    def initialize(self, proxy=None):
        proxy = proxy or self.proxy
        return proxy.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})

    def call(self, name, arguments=None, id=3):
        return self.proxy.call({"jsonrpc": "2.0", "id": id, "method": "tools/call", "params": {"name": name, "arguments": arguments or {}}})

    def test_handshake_notification_headers_close_and_distinct_tokens(self):
        self.initialize()
        session = self.proxy.session
        self.assertIsNone(self.proxy.call({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.proxy.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertIn("read", self.proxy.read_only)
        row = self.gateway.records[-1]
        self.assertEqual(row["session"], session)
        self.assertEqual(row["protocol"], "2025-11-25")
        self.assertIn("application/json", row["accept"])
        other = client.Proxy(self.gateway.url, TOKEN_B, allow_loopback_http=True, timeout=3)
        try:
            self.initialize(other)
            self.assertNotEqual(other.session, session)
            other.session = session
            with self.assertRaises(client.ClientError) as error:
                other.call({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
            self.assertEqual(error.exception.code, "permission_denied")
        finally:
            other.session = None
        self.proxy.close()
        self.assertNotIn(session, self.gateway.sessions)

    def test_lost_mutation_has_uncertain_status_and_is_never_retried(self):
        self.initialize()
        operation = str(uuid.uuid4())
        message = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "write_lost", "arguments": {"operation_id": operation}}}
        with self.assertRaises(client.ClientError) as caught:
            self.proxy.call(message)
        response = self.proxy.error_response(message, caught.exception)
        self.assertTrue(response["result"]["structuredContent"]["uncertain"])
        self.assertEqual(response["result"]["structuredContent"]["operation_id"], operation)
        self.assertEqual(len([r for r in self.gateway.records if r["body"].get("params", {}).get("name") == "write_lost"]), 1)

    def test_redirect_auth_and_response_redaction(self):
        self.initialize()
        self.assertNotIn(TOKEN_A, json.dumps(self.call("echo_secret")))
        with self.assertRaises(client.ClientError) as redirected:
            self.call("redirect")
        self.assertEqual(redirected.exception.code, "redirect_refused")
        with self.assertRaises(client.ClientError) as denied:
            self.call("forbidden")
        self.assertEqual(denied.exception.code, "permission_denied")

    def test_proxy_environment_does_not_change_destination(self):
        with mock.patch.dict(os.environ, {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9"}):
            self.initialize()
        self.assertTrue(self.proxy.session)

    def test_media_stream_upload_rewrites_only_explicit_path_and_preserves_id(self):
        self.initialize()
        path = Path(self.temp.name) / "video có dấu.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * (client.CHUNK * 3 + 17))
        result = self.call("via_register_media", {"file_path": str(path), "kind": "video", "extra": "kept"}, id="media-19")
        sent = self.gateway.records[-1]["body"]
        self.assertEqual(sent["id"], "media-19")
        self.assertEqual(sent["params"]["arguments"]["file_path"], "upload:" + self.gateway.uploads[0]["id"])
        self.assertEqual(sent["params"]["arguments"]["extra"], "kept")
        self.assertNotIn(str(path), json.dumps(sent))
        self.assertGreater(self.gateway.uploads[0]["reads"], 1)
        self.assertIn("%", self.gateway.uploads[0]["filename"])
        self.assertEqual(result["id"], "media-19")

    def test_image_size_signature_private_paths_and_hardlinks_rejected(self):
        path = Path(self.temp.name) / "photo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        with mock.patch.object(client, "IMAGE_MAX", 16):
            with self.assertRaises(client.ClientError):
                client.LocalMedia(str(path), "image", None)
        video = path.with_name("video.mp4")
        video.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"x" * 32)
        with mock.patch.object(client, "VIDEO_MAX", 16):
            with self.assertRaises(client.ClientError):
                client.LocalMedia(str(video), "video", None)
        path.write_bytes(b"not an image")
        with self.assertRaises(client.ClientError):
            client.LocalMedia(str(path), "image", None)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        with self.assertRaises(client.ClientError):
            client.LocalMedia(str(path), "image", path)
        linked = path.with_name("linked.png")
        try:
            os.link(path, linked)
        except OSError:
            return
        with self.assertRaises(client.ClientError):
            client.LocalMedia(str(path), "image", None)

    def test_changed_open_media_is_rejected_before_registration(self):
        path = Path(self.temp.name) / "photo.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
        local = client.LocalMedia(str(path), "image", None)
        try:
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 64)
            with self.assertRaises(client.ClientError):
                local.verify()
        finally:
            local.close()

    def test_url_and_json_validation(self):
        for url in ["http://example.com/mcp", "http://127.0.0.1/mcp", "https://person:secret@example.com/mcp", "https://example.com/mcp?x=1", "https://example.com/other", "https://example.com/mcp#x", "https://example.com\\@evil.com/mcp"]:
            with self.subTest(url=url), self.assertRaises(client.ClientError):
                client.validate_url(url)
        self.assertEqual(client.validate_url("https://example.com/mcp"), "https://example.com/mcp")
        self.assertEqual(client.validate_url("https://EXAMPLE.COM:443/mcp"), "https://example.com/mcp")
        self.assertEqual(client.validate_url("https://bücher.example/mcp"), "https://xn--bcher-kva.example/mcp")
        with self.assertRaises(ValueError):
            client.strict_json('{"id":1,"id":2}')
        with self.assertRaises(ValueError):
            client.strict_json('{"id":NaN}')
        with self.assertRaises(client.private.PrivateError):
            client.private.validate_token("a" * 64)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI")
class WindowsInstallTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()
        self.temp = tempfile.TemporaryDirectory(prefix="via-install-test-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.gateway.stop)

    def spec(self):
        return {"marketplace_root": str(self.root), "install_root": str(self.root / "plugins" / "via"), "credential_file": str(self.root / "private" / "token.bin"), "python_exe": sys.executable, "server_url": self.gateway.url, "token": TOKEN_A, "allow_loopback_http": True}

    def test_dpapi_roundtrip_no_plaintext_and_reject_another_entropy(self):
        path = self.root / "private" / "token.bin"
        client.private.save_token(path, TOKEN_A)
        self.assertEqual(client.private.load_token(path), TOKEN_A)
        self.assertNotIn(TOKEN_A.encode(), path.read_bytes())
        with mock.patch.object(client.private, "ENTROPY", b"different"):
            with self.assertRaises(client.private.PrivateError):
                client.private.load_token(path)

    def test_install_preserves_marketplace_and_updates_owned_plugin_only(self):
        market = self.root / ".agents" / "plugins" / "marketplace.json"
        market.parent.mkdir(parents=True)
        existing = {"name": "my-plugins", "interface": {"displayName": "Mine"}, "plugins": [{"name": "unrelated", "source": {"source": "local", "path": "./plugins/unrelated"}, "extra": 41}], "extra": {"keep": True}}
        market.write_text(json.dumps(existing))
        result = installer.install(self.spec(), ROOT)
        installed = Path(result["plugin_root"])
        after = json.loads(market.read_text())
        self.assertEqual(after["plugins"][0], existing["plugins"][0])
        self.assertEqual(after["extra"], existing["extra"])
        self.assertEqual(result["plugin_id"], "via@my-plugins")
        self.assertNotIn(TOKEN_A, "".join(p.read_text(encoding="utf-8") for p in installed.rglob("*") if p.is_file()))
        manifests = [json.loads((installed / p).read_text(encoding="utf-8")) for p in ("mcp.portable.json", ".mcp.json")]
        self.assertTrue(all(m["mcpServers"]["via_manager"]["command"] == sys.executable for m in manifests))
        update = self.spec()
        update["token"] = TOKEN_B
        second = installer.install(update, ROOT)
        self.assertTrue(Path(second["previous_plugin"]).is_dir())
        self.assertEqual(client.private.load_token(Path(update["credential_file"])), TOKEN_B)
        self.assertEqual(len(json.loads(market.read_text())["plugins"]), 2)

    def test_conflicting_plugin_auth_failure_and_market_write_failure_leave_old_setup(self):
        result = installer.install(self.spec(), ROOT)
        target = Path(result["plugin_root"])
        old_config = (target / "client-config.json").read_bytes()
        changed = self.spec()
        changed["token"] = TOKEN_B
        with mock.patch.object(installer, "_atomic_json", side_effect=OSError("mock failure")):
            with self.assertRaises(OSError):
                installer.install(changed, ROOT)
        self.assertEqual((target / "client-config.json").read_bytes(), old_config)
        self.assertEqual(client.private.load_token(Path(changed["credential_file"])), TOKEN_A)
        bad = self.spec()
        bad["token"] = "via_pat_" + "z" * 43
        with self.assertRaises(installer.proxy.ClientError):
            installer.install(bad, ROOT)
        self.assertEqual((target / "client-config.json").read_bytes(), old_config)
        market = Path(result["marketplace"])
        catalog = json.loads(market.read_text())
        catalog["plugins"][0]["source"]["path"] = "./somewhere-else"
        market.write_text(json.dumps(catalog))
        with self.assertRaises(ValueError):
            installer.install(self.spec(), ROOT)

    def test_post_replace_credential_acl_failure_restores_exact_previous_encrypted_bytes(self):
        result = installer.install(self.spec(), ROOT)
        target = Path(result["plugin_root"])
        credential = Path(self.spec()["credential_file"])
        previous_credential = credential.read_bytes()
        previous_config = (target / "client-config.json").read_bytes()
        marketplace = Path(result["marketplace"])
        previous_marketplace = marketplace.read_bytes()
        real_protect = installer.private.protect_path
        failed = False

        def fail_once_after_replace(path):
            nonlocal failed
            if Path(path) == credential and not failed:
                # save_token has already replaced the old encrypted file here.
                self.assertNotEqual(credential.read_bytes(), previous_credential)
                failed = True
                raise OSError("Injected final credential ACL failure")
            return real_protect(path)

        changed = self.spec()
        changed["token"] = TOKEN_B
        with mock.patch.object(installer.private, "protect_path", side_effect=fail_once_after_replace):
            with self.assertRaises(OSError):
                installer.install(changed, ROOT)
        self.assertTrue(failed)
        self.assertEqual(credential.read_bytes(), previous_credential)
        self.assertEqual(client.private.load_token(credential), TOKEN_A)
        self.assertEqual((target / "client-config.json").read_bytes(), previous_config)
        self.assertEqual(marketplace.read_bytes(), previous_marketplace)

    def test_installed_stdio_process_uses_dpapi_without_secret_output(self):
        result = installer.install(self.spec(), ROOT)
        target = Path(result["plugin_root"])
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "test", "version": "1"}, "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo_secret", "arguments": {}}},
        ]
        process = subprocess.run([sys.executable, "-I", "-B", str(target / "client" / "stdio_proxy.py"), "--config", str(target / "client-config.json")], input="\n".join(json.dumps(m) for m in messages) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn(TOKEN_A, process.stdout + process.stderr)
        responses = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r["id"] for r in responses], [1, 2, 3])
        self.assertEqual(len(self.gateway.sessions), 0)

    def test_powershell_wrapper_hidden_input_pipe_and_explicit_temporary_paths(self):
        script = self.root / "run-wrapper.ps1"
        (self.root / "appdata").mkdir()
        # Fixture token is disposable and only appears in the test fixture, never argv.
        script.write_text("$ErrorActionPreference = 'Stop'\n$env:LOCALAPPDATA = '" + str(self.root / "appdata").replace("'", "''") + "'\n$token = ConvertTo-SecureString ('via_pat_' + ('a' * 43)) -AsPlainText -Force\n& '" + str(ROOT / "install-client.ps1").replace("'", "''") + "' -ServerUrl '" + self.gateway.url + "' -MarketplaceRoot '" + str(self.root).replace("'", "''") + "' -PythonExe '" + sys.executable.replace("'", "''") + "' -AccessToken $token -AllowLoopbackHttp\n", encoding="utf-8")
        environment = os.environ.copy()
        # Codex bundles PowerShell 7; Windows PowerShell 5 must not import its modules.
        environment["PSModulePath"] = str(Path(os.environ["WINDIR"]) / "System32" / "WindowsPowerShell" / "v1.0" / "Modules")
        process = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)], capture_output=True, text=True, timeout=40, env=environment)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertNotIn(TOKEN_A, process.stdout + process.stderr)
        self.assertTrue((self.root / ".codex" / "plugins" / "via" / "client-config.json").is_file())


class PackageTests(unittest.TestCase):
    def test_archive_is_allowlisted_and_contains_only_one_skill_without_credentials(self):
        with tempfile.TemporaryDirectory(prefix="via-package-test-") as folder:
            path = Path(folder) / "client.zip"
            result = builder.build(path)
            self.assertEqual(result["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                self.assertEqual(len([n for n in names if n.endswith("SKILL.md")]), 1)
                self.assertFalse(any("token.bin" in n or n.endswith("/client-config.json") or "/tests/" in n for n in names))
                self.assertEqual(set(names), {"Via-Codex-Client/" + p for p in builder.FILES} | {"Via-Codex-Client/PACKAGE.json"})
                self.assertNotIn(TOKEN_A.encode(), b"".join(archive.read(name) for name in names))


if __name__ == "__main__":
    unittest.main()

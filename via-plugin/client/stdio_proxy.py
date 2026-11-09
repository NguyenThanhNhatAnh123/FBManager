"""Line-delimited MCP stdio to Via's authenticated HTTP gateway; no dependencies."""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

_spec = importlib.util.spec_from_file_location("via_client_private", Path(__file__).with_name("private.py"))
private = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(private)

MAX_LINE = 262144
MAX_RESPONSE = 4 * 1024 * 1024
IMAGE_MAX = 8 * 1024 * 1024
VIDEO_MAX = 1024 * 1024 * 1024
CHUNK = 128 * 1024
PROTOCOLS = {"2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"}
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, uncertain: bool = False):
        self.code, self.message, self.uncertain = code, message, uncertain
        super().__init__(message)


def strict_json(raw: bytes | str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON")))


def validate_url(value: str, allow_loopback_http: bool = False) -> str:
    if not isinstance(value, str) or not value or any(ord(c) <= 32 for c in value) or "\\" in value:
        raise ClientError("configuration", "Configure a concrete HTTPS Via /mcp URL using the installer.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ClientError("configuration", "Invalid Via server URL.") from exc
    local = False
    if parsed.hostname:
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = parsed.hostname.lower() == "localhost"
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.path != "/mcp" or parsed.query or parsed.fragment:
        raise ClientError("configuration", "Use the server's HTTPS /mcp URL without credentials or extra parameters.")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and allow_loopback_http is True and local):
        raise ClientError("configuration", "Via requires HTTPS; HTTP is available only for explicit loopback tests.")
    if port is not None and not 1 <= port <= 65535:
        raise ClientError("configuration", "Invalid Via server port.")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ClientError("configuration", "Invalid Via server hostname.") from exc
    authority = ("[" + hostname + "]") if ":" in hostname else hostname
    if port is not None and not (parsed.scheme == "https" and port == 443):
        authority += ":" + str(port)
    return urlunsplit((parsed.scheme, authority, "/mcp", "", ""))


def read_config(path: Path) -> dict:
    private.safe_path(path, regular=True)
    if path.stat().st_size > 16384:
        raise ClientError("configuration", "Invalid client configuration.")
    config = strict_json(path.read_bytes())
    if not isinstance(config, dict) or set(config) != {"version", "server_url", "credential_file", "allow_loopback_http"} or type(config["version"]) is not int or config["version"] != 1 or type(config["allow_loopback_http"]) is not bool:
        raise ClientError("configuration", "Rerun the Via client installer to configure this plugin.")
    config["server_url"] = validate_url(config["server_url"], config["allow_loopback_http"])
    credential = Path(config["credential_file"])
    private.safe_path(credential, regular=True)
    if credential.is_relative_to(path.parent):
        raise ClientError("configuration", "The personal credential must be stored outside the plugin folder.")
    return config


def _fingerprint(info):
    # On Windows Python 3.12, stat/fstat can report different ctime semantics.
    # File identity + size + mtime are checked alongside two full SHA256 passes.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) + (() if os.name == "nt" else (info.st_ctime_ns,))


class LocalMedia:
    def __init__(self, filename: str, kind: str, credential_file: Path | None):
        if kind not in {"image", "video"} or not isinstance(filename, str) or not filename:
            raise ClientError("media_invalid", "Select an absolute local image or video path and its kind.")
        self.path = Path(filename)
        try:
            private.safe_path(self.path, regular=True)
            if credential_file and (self.path == credential_file or self.path.is_relative_to(credential_file.parent)):
                raise ValueError("credential path")
            lowered = [p.lower() for p in self.path.parts]
            if any(p in {".ssh", ".codex", ".agents", "credentials"} for p in lowered) or "user data" in lowered:
                raise ValueError("private path")
            suffix = self.path.suffix.lower()
            if suffix not in ({".jpg", ".jpeg", ".png"} if kind == "image" else {".mp4", ".mov", ".m4v"}):
                raise ValueError("extension")
            self.stream = self.path.open("rb")
            self.info = os.fstat(self.stream.fileno())
            if not stat.S_ISREG(self.info.st_mode) or self.info.st_nlink != 1 or _fingerprint(self.info) != _fingerprint(self.path.stat()):
                raise ValueError("changed path")
            self.size = self.info.st_size
            if not 0 < self.size <= (IMAGE_MAX if kind == "image" else VIDEO_MAX):
                raise ValueError("size")
            header = self.stream.read(16)
            valid = (header.startswith(b"\x89PNG\r\n\x1a\n") if suffix == ".png" else header.startswith(b"\xff\xd8\xff")) if kind == "image" else len(header) >= 12 and header[4:8] == b"ftyp"
            if not valid:
                raise ValueError("signature")
            self.stream.seek(0)
            digest = hashlib.sha256()
            while chunk := self.stream.read(CHUNK):
                digest.update(chunk)
            self.digest = digest.hexdigest()
            self.verify()
            self.stream.seek(0)
        except ClientError:
            self.close()
            raise
        except (OSError, ValueError, private.PrivateError) as exc:
            self.close()
            raise ClientError("media_invalid", "Choose a regular local JPEG/PNG up to 8 MiB or MP4/MOV/M4V up to 1 GiB. Links and private credential paths are excluded.") from exc

    def verify(self):
        try:
            private.safe_path(self.path, regular=True)
            changed = _fingerprint(os.fstat(self.stream.fileno())) != _fingerprint(self.info) or _fingerprint(self.path.stat()) != _fingerprint(self.info)
        except (OSError, private.PrivateError) as exc:
            raise ClientError("media_changed", "The local media path changed. Select it again before retrying.") from exc
        if changed:
            raise ClientError("media_changed", "The local media file changed. Select it again before retrying.")

    def close(self):
        if getattr(self, "stream", None):
            self.stream.close()


class Proxy:
    def __init__(self, server_url: str, token: str, *, allow_loopback_http=False, credential_file=None, timeout=120):
        self.url = urlsplit(validate_url(server_url, allow_loopback_http))
        self.token = private.validate_token(token)
        self.credential_file = Path(credential_file) if credential_file else None
        self.timeout = timeout
        self.session = None
        self.protocol = None
        self.read_only = set()

    def redact(self, obj):
        if isinstance(obj, str):
            return obj.replace(self.token, "[redacted]")
        if isinstance(obj, list):
            return [self.redact(item) for item in obj]
        if isinstance(obj, dict):
            return {self.redact(key): self.redact(value) for key, value in obj.items()}
        return obj

    def request(self, method, path, body=None, *, media=None, mutation=False):
        cls = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        options = {"timeout": self.timeout}
        if self.url.scheme == "https":
            options["context"] = ssl.create_default_context()
        # http.client uses neither HTTP(S)_PROXY nor automatic redirects/retries.
        connection = cls(self.url.hostname, self.url.port, **options)
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.protocol:
            headers["MCP-Protocol-Version"] = self.protocol
        headers["Content-Type"] = "application/octet-stream" if media else "application/json"
        raw = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if media:
            headers.update({"Content-Length": str(media.size), "X-Filename": quote(media.path.name, safe=""), "X-Content-SHA256": media.digest})
        sent = False
        try:
            if media:
                connection.putrequest(method, path)
                for key, value in headers.items():
                    connection.putheader(key, value)
                connection.endheaders()
                sent = True
                digest = hashlib.sha256()
                remaining = media.size
                while remaining:
                    chunk = media.stream.read(min(CHUNK, remaining))
                    if not chunk:
                        raise ClientError("media_changed", "The media changed during upload; no tool call was sent.")
                    digest.update(chunk)
                    connection.send(chunk)
                    remaining -= len(chunk)
                media.verify()
                if digest.hexdigest() != media.digest:
                    raise ClientError("media_changed", "The media changed during upload; no tool call was sent.")
            else:
                sent = True
                connection.request(method, path, raw, headers)
            response = connection.getresponse()
            status = response.status
            response_headers = {k.lower(): v for k, v in response.getheaders()}
            if 300 <= status < 400:
                raise ClientError("redirect_refused", "Via server redirected this request. Configure its final HTTPS /mcp URL.", uncertain=mutation)
            if status in {401, 403}:
                raise ClientError("authentication" if status == 401 else "permission_denied", "Your Via token is expired/revoked or the account needs to change its password." if status == 401 else "Your Via account or token does not have permission for this operation.")
            if status >= 400:
                raise ClientError("server_error", "Via rejected the request. Inspect the operation status before retrying a write.", uncertain=mutation and status >= 500)
            raw_response = response.read(MAX_RESPONSE + 1)
            if len(raw_response) > MAX_RESPONSE:
                raise ClientError("response_invalid", "Via returned an oversized response.", uncertain=mutation)
            if status in {202, 204} and not raw_response:
                return None, response_headers
            if response_headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ClientError("response_invalid", "Via must return a JSON response.", uncertain=mutation)
            try:
                return strict_json(raw_response), response_headers
            except (ValueError, UnicodeError, RecursionError) as exc:
                raise ClientError("response_invalid", "Via returned invalid JSON.", uncertain=mutation) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ClientError("transport_error", "The connection ended. Check the operation status before retrying a write." if mutation else "Cannot connect to the configured Via server.", uncertain=mutation and sent) from exc
        finally:
            connection.close()

    def call(self, message: dict):
        method = message.get("method")
        if method != "initialize" and not self.session:
            raise ClientError("not_initialized", "Initialize the Via connection first.")
        if method == "initialize" and self.session:
            raise ClientError("already_initialized", "This Via connection is already initialized.")
        is_tool = method == "tools/call"
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise ClientError("request_invalid", "Invalid MCP parameters.")
        tool = params.get("name") if is_tool else None
        mutation = is_tool and tool not in self.read_only
        if tool == "via_register_media":
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ClientError("media_invalid", "Invalid media arguments.")
            local = LocalMedia(arguments.get("file_path"), arguments.get("kind"), self.credential_file)
            try:
                uploaded, _ = self.request("POST", "/mcp/media?kind=" + arguments["kind"], media=local, mutation=False)
            finally:
                local.close()
            try:
                upload_id = str(uuid.UUID(uploaded["upload_id"]))
                if uploaded["upload_id"] != upload_id:
                    raise ValueError("Invalid UUID")
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise ClientError("response_invalid", "Via returned an invalid upload ID; no tool call was sent.") from exc
            message = copy.deepcopy(message)
            message["params"]["arguments"]["file_path"] = "upload:" + upload_id
        result, headers = self.request("POST", "/mcp", message, mutation=mutation)
        if "id" not in message:
            if result is not None:
                raise ClientError("response_invalid", "Via returned a response to a notification.")
            return None
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0" or result.get("id") != message["id"] or ("result" in result) == ("error" in result):
            raise ClientError("response_invalid", "Via returned an invalid MCP response.", uncertain=mutation)
        if method == "initialize" and "result" in result:
            session = headers.get("mcp-session-id", "")
            protocol = result["result"].get("protocolVersion") if isinstance(result["result"], dict) else None
            if not SESSION_RE.fullmatch(session) or protocol not in PROTOCOLS:
                raise ClientError("response_invalid", "Via returned invalid connection metadata.")
            self.session, self.protocol = session, protocol
        if method == "tools/list" and isinstance(result.get("result"), dict):
            for item in result["result"].get("tools", []):
                if isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("annotations"), dict) and item["annotations"].get("readOnlyHint") is True:
                    self.read_only.add(item["name"])
        return self.redact(result)

    def error_response(self, message, error: ClientError):
        if not isinstance(message, dict) or "id" not in message:
            return None
        if message.get("method") == "tools/call":
            detail = {"ok": False, "code": error.code, "message": error.message, "uncertain": error.uncertain}
            params = message.get("params")
            arguments = params.get("arguments") if isinstance(params, dict) else None
            operation = arguments.get("operation_id") if isinstance(arguments, dict) else None
            if isinstance(operation, str):
                try:
                    if str(uuid.UUID(operation)) == operation:
                        detail["operation_id"] = operation
                except ValueError:
                    pass
            return {"jsonrpc": "2.0", "id": message["id"], "result": {"isError": True, "content": [{"type": "text", "text": json.dumps(detail, ensure_ascii=False)}], "structuredContent": detail}}
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": error.message, "data": {"code": error.code, "uncertain": error.uncertain}}}

    def close(self):
        if self.session:
            try:
                self.request("DELETE", "/mcp")
            except ClientError:
                pass
            self.session = None


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    parser = argparse.ArgumentParser(description="Private Via MCP client")
    parser.add_argument("--config", type=Path, required=True)
    options = parser.parse_args()
    try:
        config = read_config(options.config)
        proxy = Proxy(config["server_url"], private.load_token(Path(config["credential_file"])), allow_loopback_http=config["allow_loopback_http"], credential_file=config["credential_file"])
    except (ClientError, private.PrivateError, OSError, ValueError, TypeError, RecursionError):
        print("Via client setup is missing or invalid. Rerun install-client.ps1 with your own personal token.", file=sys.stderr)
        return 1
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_LINE + 1)
            if not line:
                break
            if len(line) > MAX_LINE:
                print("Via client received an oversized MCP request.", file=sys.stderr)
                return 1
            message = None
            try:
                message = strict_json(line)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str) or ("id" in message and (isinstance(message["id"], bool) or not isinstance(message["id"], (str, int)))):
                    raise ClientError("request_invalid", "Invalid MCP request.")
                result = proxy.call(message)
            except ClientError as exc:
                result = proxy.error_response(message, exc)
            except (ValueError, TypeError, KeyError, OSError, private.PrivateError, RecursionError):
                result = proxy.error_response(message, ClientError("request_invalid", "Invalid MCP request or local file."))
            if result is not None:
                print(json.dumps(proxy.redact(result), ensure_ascii=False, separators=(",", ":"), allow_nan=False), flush=True)
    finally:
        proxy.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Transactional private plugin installer. The installation spec arrives on stdin."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import uuid

_spec = importlib.util.spec_from_file_location("via_stdio_proxy", Path(__file__).with_name("stdio_proxy.py"))
proxy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(proxy)
private = proxy.private

PACKAGE_FILES = (
    "plugin.json", "mcp.json", ".codex-plugin/plugin.json", ".mcp.json",
    "client-config.example.json", "client/stdio_proxy.py", "client/private.py",
    "client/install.py", "install-client.ps1", "README.md",
    "skills/via-manager/SKILL.md", "skills/via-manager/agents/openai.yaml",
)
OWNER = "via-private-codex-client-v1"


def materialize_manifests(stage: Path, target: Path, python: Path, *, portable_layout=False):
    for name in ("mcp.json", ".mcp.json"):
        manifest = proxy.strict_json((stage / name).read_bytes())
        entry = manifest["mcpServers"]["via_manager"]
        entry["command"] = str(python)
        if name == ".mcp.json":
            # Current Codex compatibility loader does not expand PLUGIN_ROOT.
            entry["args"] = ["-I", "-B", str(target / "client" / "stdio_proxy.py"), "--config", str(target / "client-config.json")]
            entry["cwd"] = str(target)
        _write_json(stage / name, manifest)
    if not portable_layout:
        # Portable manifests remain available in the package; use the tested
        # compatibility entry point on current Codex desktop/CLI versions.
        (stage / "plugin.json").rename(stage / "plugin.portable.json")
        (stage / "mcp.json").rename(stage / "mcp.portable.json")


def _write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _atomic_json(path, value, expected=None):
    fd, name = tempfile.mkstemp(prefix=".via-marketplace-", suffix=".json", dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        _write_json(temporary, value)
        private.safe_path(path)
        if (path.read_bytes() if path.exists() else None) != expected:
            raise ValueError("The marketplace changed during installation. Retry to preserve its newer entries.")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _marketplace(root, target):
    path = root / ".agents" / "plugins" / "marketplace.json"
    private.safe_path(path)
    catalog = {"name": "via-private", "interface": {"displayName": "Private local plugins"}, "plugins": []}
    previous = None
    if path.exists():
        private.safe_path(path, regular=True)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("Marketplace is too large.")
        previous = path.read_bytes()
        catalog = proxy.strict_json(previous)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("name"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", catalog["name"]) or not isinstance(catalog.get("plugins"), list):
        raise ValueError("The existing marketplace has an unsupported format; it was left untouched.")
    relative = "./" + target.relative_to(root).as_posix()
    entries = catalog["plugins"]
    matches = [i for i, entry in enumerate(entries) if isinstance(entry, dict) and entry.get("name") == "via"]
    if len(matches) > 1:
        raise ValueError("The marketplace has duplicate Via entries; it was left untouched.")
    if matches:
        entry = entries[matches[0]]
        source = entry.get("source")
        old_path = source.get("path") if isinstance(source, dict) and source.get("source") == "local" else source if isinstance(source, str) else None
        if not isinstance(old_path, str) or not old_path.startswith("./") or (root / old_path).absolute() != target:
            raise ValueError("A different Via plugin already exists in this marketplace; it was left untouched.")
        # Preserve entry metadata/policies provided by the existing owner.
        entry = dict(entry)
        entry["source"] = {"source": "local", "path": relative}
        entries[matches[0]] = entry
    else:
        entries.append({"name": "via", "source": {"source": "local", "path": relative}, "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"})
    return path, catalog, previous


def install(spec: dict, source: Path):
    if os.name != "nt":
        raise ValueError("This installer requires Windows and DPAPI.")
    source = source.absolute()
    root = Path(spec["marketplace_root"]).absolute()
    target = Path(spec["install_root"]).absolute()
    credential = Path(spec["credential_file"]).absolute()
    python = Path(spec["python_exe"]).absolute()
    token = private.validate_token(spec["token"])
    allow_http = spec.get("allow_loopback_http", False)
    portable_layout = spec.get("portable_layout", False)
    if type(allow_http) is not bool or type(portable_layout) is not bool:
        raise ValueError("Invalid loopback option.")
    url = proxy.validate_url(spec["server_url"], allow_http)
    for path in (source, root, target, credential, python):
        private.safe_path(path)
    source, root, target, credential, python = (path.resolve() for path in (source, root, target, credential, python))
    if target == root or not target.is_relative_to(root) or target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError("InstallRoot must be inside MarketplaceRoot, separate from this source package.")
    if credential.is_relative_to(target) or target.is_relative_to(credential.parent) or credential.is_relative_to(source):
        raise ValueError("Keep credentials outside the plugin and source package.")
    if not python.is_file() or not root.is_dir():
        raise ValueError("Python or MarketplaceRoot is unavailable.")
    if target.exists():
        marker = target / ".via-client-install.json"
        private.safe_path(marker, regular=True)
        existing = proxy.strict_json(marker.read_bytes())
        if not isinstance(existing, dict) or existing.get("owner") != OWNER:
            raise ValueError("InstallRoot already contains another folder; it was left untouched.")
    market_path, catalog, previous_marketplace = _marketplace(root, target)
    for relative in PACKAGE_FILES:
        private.safe_path(source / relative, regular=True)
    # Authenticate before changing installed files. A failed login leaves the old setup intact.
    connection = proxy.Proxy(url, token, allow_loopback_http=allow_http, timeout=30)
    try:
        result = connection.call({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "via-private-installer", "version": "1.1.0"}}})
        if "error" in result:
            raise ValueError("The Via server did not accept this personal token.")
    finally:
        connection.close()
    target.parent.mkdir(parents=True, exist_ok=True)
    private.safe_path(target.parent)
    stage = Path(tempfile.mkdtemp(prefix=".via-install-", dir=target.parent))
    backup = target.with_name(target.name + ".previous-" + uuid.uuid4().hex[:12])
    previous_credential = None
    published = False
    backed_up = False
    credential_changed = False
    try:
        for relative in PACKAGE_FILES:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, destination)
        materialize_manifests(stage, target, python, portable_layout=portable_layout)
        _write_json(stage / "client-config.json", {"version": 1, "server_url": url, "credential_file": str(credential), "allow_loopback_http": allow_http})
        _write_json(stage / ".via-client-install.json", {"owner": OWNER, "version": 1})
        if credential.exists():
            private.safe_path(credential, regular=True)
            if credential.stat().st_size > 16384:
                raise ValueError("Invalid previous credential file.")
            previous_credential = credential.read_bytes()
        credential_changed = True
        private.save_token(credential, token)
        if target.exists():
            private.safe_path(target)
            target.rename(backup)
            backed_up = True
        stage.rename(target)
        published = True
        market_path.parent.mkdir(parents=True, exist_ok=True)
        private.safe_path(market_path)
        _atomic_json(market_path, catalog, previous_marketplace)
        # Keep a previous owned installation for rollback; it contains no plaintext token.
        return {"plugin_root": str(target), "marketplace": str(market_path), "plugin_id": "via@" + catalog["name"], "previous_plugin": str(backup) if backed_up else None}
    except Exception:
        if published:
            target.rename(stage)
        if backed_up:
            backup.rename(target)
        if credential_changed:
            if previous_credential is None:
                credential.unlink(missing_ok=True)
            else:
                credential.write_bytes(previous_credential)
                private.protect_path(credential)
        raise
    finally:
        # stage is a random directory created in the verified installation parent.
        if stage.exists() and stage.parent == target.parent and stage.name.startswith(".via-install-"):
            private.safe_path(stage)
            shutil.rmtree(stage)


def main():
    try:
        if len(sys.argv) != 1:
            raise ValueError("Installation input must arrive on standard input.")
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError("Installation input is too large.")
        spec = proxy.strict_json(raw)
        result = install(spec, Path(__file__).resolve().parent.parent)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (ValueError, KeyError, TypeError, OSError, proxy.ClientError, private.PrivateError):
        # Deliberately exclude input/exception repr: it may contain the token.
        print("Via client installation failed. Check the HTTPS /mcp URL, personal token, Windows permissions and existing destination/marketplace. Existing installation files were preserved or rolled back.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

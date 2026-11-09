"""Build a credential-free private Via client archive from an explicit allowlist."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

FILES = (
    "plugin.json", "mcp.json", ".codex-plugin/plugin.json", ".mcp.json",
    "client-config.example.json", "client/stdio_proxy.py", "client/private.py",
    "client/install.py", "install-client.ps1", "README.md",
    "skills/via-manager/SKILL.md", "skills/via-manager/agents/openai.yaml",
)


def build(output: Path):
    source = Path(__file__).resolve().parent
    output = output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in FILES:
            path = source / relative
            if path.is_symlink() or not path.is_file():
                raise ValueError("Package input must be a regular source file: " + relative)
            archive.write(path, "Via-Codex-Client/" + relative)
        archive.writestr("Via-Codex-Client/PACKAGE.json", json.dumps({"name": "Via-Codex-Client", "version": "1.1.0", "private": True, "files": list(FILES)}, indent=2) + "\n")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    return {"archive": str(output), "files": len(FILES) + 1, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", "-Output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output)))

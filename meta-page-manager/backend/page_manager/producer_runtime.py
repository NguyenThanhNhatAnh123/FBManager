"""Portable executable selection; configuration never becomes shell text."""
import os
from pathlib import Path
import platform
import stat
import sys


def executable_path(value):
    try:
        path = Path(value)
        if (not path.is_absolute() or '..' in path.parts
                or os.name == 'nt' and path.suffix.lower() != '.exe'):
            return None
        for part in (path, *path.parents):
            info = part.lstat()
            if (part.is_symlink() or getattr(part, 'is_junction', lambda: False)()
                    or getattr(info, 'st_file_attributes', 0) & 0x400):
                return None
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        return path
    except (OSError, ValueError, TypeError):
        return None


def current_user_codex_cli():
    """Native npm Codex executable for the current Windows user/architecture."""
    roaming = Path(os.environ.get('APPDATA') or Path.home() / 'AppData/Roaming')
    arm = platform.machine().lower() in ('arm64', 'aarch64')
    package = 'codex-win32-arm64' if arm else 'codex-win32-x64'
    target = 'aarch64-pc-windows-msvc' if arm else 'x86_64-pc-windows-msvc'
    return roaming / 'npm/node_modules/@openai/codex/node_modules/@openai' / package / 'vendor' / target / 'bin/codex.exe'


def camera_python(producer_python=None):
    """Use the project runtime with NumPy; an invalid explicit override blocks QA."""
    override = os.environ.get('PM_CAMERA_PYTHON')
    return executable_path(override if override is not None else producer_python or sys.executable)

"""Windows CurrentUser DPAPI credentials. Never accept a credential in argv."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from ctypes import wintypes

ENTROPY = b"Via Codex MCP client / v1"
TOKEN_RE = re.compile(r"via_pat_[A-Za-z0-9_-]{43}\Z")


class PrivateError(Exception):
    pass


def safe_path(path: Path, *, regular: bool = False) -> None:
    """Reject link/reparse ancestors and hardlinks, including non-existing leafs."""
    if not path.is_absolute() or str(path).startswith(("\\\\", "//")):
        raise PrivateError("An absolute local path is required.")
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PrivateError("Links and reparse points are not allowed.")
    if regular:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise PrivateError("A regular file with one link is required.")


def protect_path(path: Path) -> None:
    safe_path(path)
    if os.name != "nt":
        raise PrivateError("This client requires Windows DPAPI.")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.DWORD)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi.SetFileSecurityW.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    token = wintypes.HANDLE()
    sid_text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    try:
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise PrivateError("Cannot protect the private credential directory.")
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buf = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
            raise PrivateError("Cannot determine the Windows account.")
        sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
        if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise PrivateError("Cannot determine the Windows account.")
        inherit = "OICI" if path.is_dir() else ""
        sddl = f"D:P(A;{inherit};FA;;;SY)(A;{inherit};FA;;;BA)(A;{inherit};FA;;;{sid_text.value})"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise PrivateError("Cannot build private file permissions.")
        if not advapi.SetFileSecurityW(str(path), 0x80000004, descriptor):
            raise PrivateError("Cannot apply private file permissions.")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ctypes.cast(sid_text, ctypes.c_void_p))
        if token:
            kernel.CloseHandle(token)


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != "nt":
        raise PrivateError("This client requires Windows DPAPI.")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    input_buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    entropy_buf = (ctypes.c_ubyte * len(ENTROPY)).from_buffer_copy(ENTROPY)
    source = _Blob(len(data), input_buf)
    entropy = _Blob(len(ENTROPY), entropy_buf)
    output = _Blob()
    if decrypt:
        fun = crypt.CryptUnprotectData
        fun.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    else:
        fun = crypt.CryptProtectData
        fun.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fun.restype = wintypes.BOOL
    if not fun(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
        raise PrivateError("The credential cannot be opened by this Windows account. Rerun the installer.")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.memset(output.pbData, 0, output.cbData)
        kernel.LocalFree(output.pbData)


def validate_token(token: str) -> str:
    if not isinstance(token, str) or not TOKEN_RE.fullmatch(token):
        raise PrivateError("Use a personal Via token created in your account, beginning with via_pat_.")
    return token


def save_token(path: Path, token: str) -> None:
    validate_token(token)
    safe_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    protect_path(path.parent)
    encrypted = _dpapi(token.encode("ascii"), decrypt=False)
    fd, temporary = tempfile.mkstemp(prefix=".token-", dir=path.parent)
    tmp = Path(temporary)
    try:
        os.close(fd)
        protect_path(tmp)
        tmp.write_bytes(encrypted)
        safe_path(path)
        if path.exists():
            safe_path(path, regular=True)
        os.replace(tmp, path)
        protect_path(path)
    finally:
        tmp.unlink(missing_ok=True)


def load_token(path: Path) -> str:
    safe_path(path, regular=True)
    if path.stat().st_size > 16384:
        raise PrivateError("Invalid credential file. Rerun the installer.")
    try:
        return validate_token(_dpapi(path.read_bytes(), decrypt=True).decode("ascii"))
    except (UnicodeError, OSError) as exc:
        raise PrivateError("Invalid credential file. Rerun the installer.") from exc


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3 or sys.argv[1] != "--write":
            raise PrivateError("Credential writer requires --write and an absolute filename.")
        secret = sys.stdin.buffer.read(256).decode("ascii").strip()
        save_token(Path(sys.argv[2]), secret)
    except (PrivateError, OSError, UnicodeError):
        print("Could not save the personal Via token securely.", file=sys.stderr)
        raise SystemExit(1)

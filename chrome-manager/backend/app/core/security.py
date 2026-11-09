"""Secrets are bound to the current Windows account through DPAPI."""
import base64
import ctypes
import json
import os
from ctypes import wintypes

class Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]

def _dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Proxy credential storage requires Windows DPAPI.')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('Windows could not protect/unprotect credentials for this account.')
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))

def encrypt_credentials(username: str, password: str) -> str | None:
    if not username and not password:
        return None
    return base64.b64encode(_dpapi(json.dumps([username, password]).encode(), True)).decode()

def decrypt_credentials(value: str | None) -> tuple[str, str]:
    if not value:
        return '', ''
    return tuple(json.loads(_dpapi(base64.b64decode(value), False)))

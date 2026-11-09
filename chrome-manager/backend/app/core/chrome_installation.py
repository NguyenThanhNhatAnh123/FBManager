"""Resolve the installed Google Chrome without launching or changing it."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


def installed_chrome():
    candidates = [
        Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'Google/Chrome/Application/chrome.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
    ]
    return next((path for path in candidates if path.is_file()), None)


def executable_version(path):
    if os.name != 'nt':
        raise ValueError('This migration requires Windows and installed Google Chrome.')
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ValueError('Could not read the installed Chrome version.')
    buffer = ctypes.create_string_buffer(size)
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer) or not version.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)):
        raise ValueError('Could not read the installed Chrome version.')
    values = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
    if length.value < 52 or values[0] != 0xFEEF04BD:
        raise ValueError('Installed Chrome returned invalid version metadata.')
    high, low = values[2], values[3]
    return f'{high >> 16}.{high & 65535}.{low >> 16}.{low & 65535}'

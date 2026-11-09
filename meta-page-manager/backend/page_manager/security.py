"""Windows-account-bound secret storage, isolated from other local applications."""
import base64
import ctypes
import os
from ctypes import wintypes

CONTEXT=b'Meta Page Manager / token storage / v1'


class _Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_byte))]


def _protect(value,encrypt):
    if os.name!='nt':raise ValueError('Ứng dụng cần Windows DPAPI để lưu token an toàn.')
    raw=ctypes.create_string_buffer(value);context=ctypes.create_string_buffer(CONTEXT)
    source=_Blob(len(value),ctypes.cast(raw,ctypes.POINTER(ctypes.c_byte)))
    entropy=_Blob(len(CONTEXT),ctypes.cast(context,ctypes.POINTER(ctypes.c_byte)));output=_Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    operation=crypt.CryptProtectData if encrypt else crypt.CryptUnprotectData
    operation.argtypes=[ctypes.POINTER(_Blob),ctypes.c_void_p,ctypes.POINTER(_Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(_Blob)]
    operation.restype=wintypes.BOOL
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    if not operation(ctypes.byref(source),None,ctypes.byref(entropy),None,None,1,ctypes.byref(output)):
        raise ValueError('Windows không thể mã hóa hoặc đọc token trong tài khoản này.')
    try:return ctypes.string_at(output.data,output.size)
    finally:kernel.LocalFree(ctypes.cast(output.data,ctypes.c_void_p))


class SecretBox:
    def encrypt(self,value):
        return 'mpm1:'+base64.b64encode(_protect(value.encode('utf-8'),True)).decode('ascii')

    def decrypt(self,value):
        try:
            if not isinstance(value,str) or not value.startswith('mpm1:'):raise ValueError()
            return _protect(base64.b64decode(value[5:],validate=True),False).decode('utf-8')
        except Exception:raise ValueError('Không đọc được token đã mã hóa bằng tài khoản Windows này.') from None

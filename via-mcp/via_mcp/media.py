"""Opaque, session-local handles for explicitly selected media files."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid
from .common import ToolError

BLOCKED_PARTS={'.git','.codex','.agents','.aws','.venv','node_modules','data','profiles','logs','userdata','user-data','browser-data'}
VIDEO={'.mp4','.mov','.m4v'}
IMAGE={'.jpg','.jpeg','.png'}


class MediaVault:
    def __init__(self,roots):
        self.roots=[Path(root).resolve() for root in roots];self.handles={}

    def _path(self,value,kind):
        if not isinstance(value,str) or '\0' in value:raise ToolError('Đường dẫn media không hợp lệ.','invalid_media')
        source=Path(value)
        if not source.is_absolute() or source.suffix.lower() not in (VIDEO if kind=='video' else IMAGE):
            raise ToolError('Dùng đường dẫn tuyệt đối tới video MP4/MOV/M4V hoặc ảnh JPEG/PNG.','invalid_media')
        # Reject links at every level, including Windows directory junctions.
        for part in (source,*source.parents):
            if part.is_symlink() or part.is_junction():raise ToolError('Không nhận media qua liên kết thư mục hoặc tệp.','invalid_media')
        resolved=source.resolve()
        if not any(resolved.is_relative_to(root) for root in self.roots) or any(part.lower() in BLOCKED_PARTS for part in resolved.parts):
            raise ToolError('Media phải nằm trong thư mục đã cho phép trong cấu hình MCP.','media_root_required')
        return resolved

    @staticmethod
    def _signature(info):return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)

    @contextmanager
    def _open(self,path,kind,expected=None):
        path=self._path(str(path),kind)
        try:
            before=path.lstat()
            limit=1073741824 if kind=='video' else 8388608
            if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<before.st_size<=limit:raise OSError()
            with path.open('rb') as handle:
                opened=os.fstat(handle.fileno())
                if self._signature(before)!=self._signature(opened):raise OSError()
                digest=hashlib.sha256();header=handle.read(32);digest.update(header)
                valid=header[4:8]==b'ftyp' if kind=='video' else header.startswith(b'\xff\xd8\xff') or header.startswith(b'\x89PNG\r\n\x1a\n')
                if not valid:raise OSError()
                while chunk:=handle.read(1024*1024):digest.update(chunk)
                signature=self._signature(os.fstat(handle.fileno()))
                if signature!=self._signature(before) or signature!=self._signature(path.lstat()):raise OSError()
                sha=digest.hexdigest()
                if expected and (expected['sha256']!=sha or expected['size']!=before.st_size):raise OSError()
                handle.seek(0)
                yield handle,{'name':path.name,'size':before.st_size,'sha256':sha,'path':path,'kind':kind}
        except ToolError:raise
        except (OSError,ValueError):raise ToolError('Media không hợp lệ, đã thay đổi hoặc không đọc được. Đăng ký lại tệp.','invalid_media') from None

    def register(self,path,kind):
        if kind not in ('video','image'):raise ToolError('Loại media không hợp lệ.','invalid_media')
        if len(self.handles)>=128:raise ToolError('Đã có 128 media trong phiên này. Mở lại kết nối MCP để bắt đầu phiên mới.','media_limit')
        with self._open(path,kind) as (_,row):
            file_id=uuid.uuid4().hex;self.handles[file_id]=row
            return {'file_id':file_id,'name':row['name'],'size':row['size'],'kind':kind,
                    'note':'Chỉ đăng ký media cục bộ; chưa tải lên manager hay Facebook. Handle dùng trong phiên MCP này.'}

    @contextmanager
    def open(self,file_id,kind):
        row=self.handles.get(file_id)
        if not row or row['kind']!=kind:raise ToolError('Media không thuộc phiên này hoặc sai loại. Đăng ký lại tệp.','invalid_file_id')
        with self._open(row['path'],kind,row) as result:yield result

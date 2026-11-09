"""Bounded streaming storage for user-uploaded Reel videos, without path inputs."""
import asyncio
from contextlib import suppress
import hashlib
import os
from pathlib import Path
import re
import stat
import unicodedata
import uuid


MAX_VIDEO_BYTES=1_073_741_824
HEADER_BYTES=65_536
ASSET_ID=re.compile(r'^[0-9a-f]{32}$')
SHA256=re.compile(r'^[0-9a-f]{64}$')
VIDEO_MIMES={'.mp4':'video/mp4','.m4v':'video/x-m4v','.mov':'video/quicktime'}
VIDEO_BRANDS={b'isom',b'iso2',b'iso3',b'iso4',b'iso5',b'iso6',b'mp41',b'mp42',b'avc1',b'hvc1',b'hev1',b'dash',b'M4V ',b'M4VH',b'M4VP',b'MSNV',b'av01',b'qt  '}


class ReelAssetError(ValueError):
    pass


class ReelAssetTooLarge(ReelAssetError):
    pass


def clean_video_name(filename):
    if not isinstance(filename,str):raise ReelAssetError('Chọn tệp video MP4, M4V hoặc MOV.')
    value=unicodedata.normalize('NFKC',filename)
    value=''.join(character for character in value if unicodedata.category(character) not in ('Cc','Cf','Cs'))
    value=value.replace('\\','/').rsplit('/',1)[-1].strip().rstrip('. ')
    value=re.sub(r'[<>:"|?*]','_',value)
    suffix=Path(value).suffix.lower()
    if suffix not in VIDEO_MIMES:raise ReelAssetError('Chỉ nhận video MP4, M4V hoặc MOV trong phiên bản này.')
    stem=value[:-len(suffix)].strip().strip('.')[:160] or 'video'
    return stem+suffix


def _validate_header(header,total):
    if len(header)<16 or header[4:8]!=b'ftyp':raise ReelAssetError('Tệp không có chữ ký video MP4/MOV hợp lệ.')
    box_size=int.from_bytes(header[:4],'big');payload=8
    if box_size==1:
        if len(header)<24:raise ReelAssetError('Header video chưa đầy đủ.')
        box_size=int.from_bytes(header[8:16],'big');payload=16
    if box_size<payload+8 or box_size>HEADER_BYTES or box_size>len(header) or (box_size-payload)%4 or total<=box_size:
        raise ReelAssetError('Header video không hợp lệ hoặc tệp bị thiếu dữ liệu.')
    if bytes(header[payload:payload+4]) not in VIDEO_BRANDS:
        raise ReelAssetError('Tệp không thuộc định dạng video MP4/M4V/MOV được hỗ trợ.')


class ReelAssetStore:
    def __init__(self,app_root,max_bytes=MAX_VIDEO_BYTES):
        if type(max_bytes) is not int or not 0<max_bytes<=MAX_VIDEO_BYTES:
            raise ValueError('Giới hạn video phải nằm trong khoảng 1 byte đến 1 GB.')
        self.app_root=Path(app_root).resolve()
        self.root=self.app_root/'reel-assets'
        self.max_bytes=max_bytes
        self._root_ready()

    def _root_ready(self,create=True):
        if self.root.is_symlink() or self.root.is_junction() or self.root.resolve().parent!=self.app_root:
            raise ReelAssetError('Thư mục video phải nằm trực tiếp trong ứng dụng.')
        if create:self.root.mkdir(parents=True,exist_ok=True)
        if not self.root.is_dir():raise ReelAssetError('Thư mục video không sẵn sàng.')

    def _cleanup(self,path):
        with suppress(OSError,ReelAssetError):
            self._root_ready(create=False)
            if path.parent==self.root:path.unlink(missing_ok=True)

    async def ingest(self,stream,filename,*,content_length=None):
        """Store an async iterator of raw bytes; caller persists returned metadata.

        Header validation recognizes ISO-BMFF video containers, not codecs or
        duration. Internal path/hash must be excluded from public API responses.
        """
        name=clean_video_name(filename)
        if content_length is not None:
            if type(content_length) is not int or content_length<=0:raise ReelAssetError('Kích thước video không hợp lệ.')
            if content_length>self.max_bytes:raise ReelAssetTooLarge('Video vượt quá giới hạn dung lượng được phép.')
        self._root_ready()
        asset_id=uuid.uuid4().hex;suffix=Path(name).suffix
        temporary=self.root/f'.{asset_id}.upload';final=self.root/(asset_id+suffix)
        total=0;header=bytearray();digest=hashlib.sha256()
        temp_owned=final_owned=complete=False
        try:
            handle=temporary.open('xb');temp_owned=True
            with handle:
                async for chunk in stream:
                    if not isinstance(chunk,(bytes,bytearray,memoryview)):raise ReelAssetError('Luồng tải video không hợp lệ.')
                    if not chunk:continue
                    chunk=memoryview(chunk).cast('B')
                    size=chunk.nbytes
                    if total+size>self.max_bytes:raise ReelAssetTooLarge('Video vượt quá giới hạn dung lượng được phép.')
                    if content_length is not None and total+size>content_length:raise ReelAssetError('Kích thước tải lên không khớp thông tin đã khai báo.')
                    total+=size
                    if len(header)<HEADER_BYTES:header.extend(chunk[:HEADER_BYTES-len(header)])
                    digest.update(chunk);handle.write(chunk)
                if content_length is not None and total!=content_length:raise ReelAssetError('Video tải lên chưa đầy đủ.')
                _validate_header(header,total)
                handle.flush();os.fsync(handle.fileno())
            self._root_ready(create=False)
            # Windows rename is atomic and refuses an existing destination.
            # On POSIX a new hardlink provides the same no-overwrite guarantee.
            if os.name=='nt':
                temporary.rename(final);temp_owned=False;final_owned=True
            else:
                os.link(temporary,final,follow_symlinks=False);final_owned=True
                temporary.unlink();temp_owned=False
            result={'id':asset_id,'name':name,'path':str(final.resolve()),'size':total,'sha256':digest.hexdigest(),'mime':VIDEO_MIMES[suffix]}
            complete=True
            return result
        except asyncio.CancelledError:raise
        except ReelAssetError:raise
        except Exception:
            raise ReelAssetError('Không hoàn tất tải video. Thử chọn lại tệp.') from None
        finally:
            if temp_owned:self._cleanup(temporary)
            if final_owned and not complete:self._cleanup(final)

    def resolve(self,asset_id,stored_path,expected_sha256,expected_size=None):
        """Resolve only a recorded UUID file, rechecking size and SHA-256."""
        if not isinstance(asset_id,str) or not ASSET_ID.fullmatch(asset_id):raise ReelAssetError('Mã video không hợp lệ.')
        if not isinstance(expected_sha256,str) or not SHA256.fullmatch(expected_sha256):raise ReelAssetError('Thiếu dấu vân tay video hợp lệ.')
        if expected_size is not None and (type(expected_size) is not int or not 0<expected_size<=self.max_bytes):raise ReelAssetError('Kích thước video đã lưu không hợp lệ.')
        self._root_ready(create=False)
        try:source=Path(stored_path)
        except (ValueError,TypeError):raise ReelAssetError('Video không thuộc kho tải lên của ứng dụng.') from None
        if not source.is_absolute() or source.parent!=self.root or source.name not in {asset_id+suffix for suffix in VIDEO_MIMES}:
            raise ReelAssetError('Video không thuộc kho tải lên của ứng dụng.')
        try:
            before=source.lstat()
            if source.is_symlink() or source.is_junction() or not stat.S_ISREG(before.st_mode) or before.st_nlink!=1:
                raise ReelAssetError('Video đã lưu không phải tệp riêng hợp lệ.')
            if not 0<before.st_size<=self.max_bytes or expected_size is not None and before.st_size!=expected_size:
                raise ReelAssetError('Kích thước video đã thay đổi; tải lại video trước khi đăng.')
            digest=hashlib.sha256()
            with source.open('rb') as handle:
                opened=os.fstat(handle.fileno())
                if (before.st_dev,before.st_ino)!=(opened.st_dev,opened.st_ino):raise ReelAssetError('Video đã thay đổi trong lúc kiểm tra.')
                while chunk:=handle.read(1024*1024):digest.update(chunk)
                after_open=os.fstat(handle.fileno())
            after=source.lstat()
            signature=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns)
            if signature(before)!=signature(after_open) or signature(before)!=signature(after) or digest.hexdigest()!=expected_sha256:
                raise ReelAssetError('Nội dung video đã thay đổi; tải lại video trước khi đăng.')
            return source
        except ReelAssetError:raise
        except (OSError,ValueError,TypeError):
            raise ReelAssetError('Không đọc được video đã tải lên. Chọn lại tệp video.') from None

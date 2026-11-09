"""Hash-bound idea thumbnails from the fixed idea library, never raw files."""
from collections import OrderedDict
from contextlib import contextmanager, ExitStack
import hashlib
from io import BytesIO
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import threading
import unicodedata
from uuid import uuid4
import warnings

from PIL import Image, ImageOps

from .errors import AppError


MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_DIMENSION = 20_000
THUMBNAIL_SIZE = (600, 800)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
CACHE_BYTE_BUDGET = 16 * 1024 * 1024
MAX_CACHE_ENTRIES = 48
FORMATS = {'PNG', 'JPEG', 'WEBP'}
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}
SAFE_MESSAGE = 'Ảnh idea chưa sẵn sàng hoặc đã thay đổi.'
UPLOAD_MESSAGE = 'Chọn ảnh PNG, JPEG hoặc WebP hợp lệ và tên tệp ảnh không chứa đường dẫn.'
UPLOAD_CONFLICT = 'Chưa thể thêm idea do xung đột tệp hoặc kho đang thay đổi. Thử lại sau.'


def _unavailable():
    return AppError(SAFE_MESSAGE, 'idea_image_unavailable', 404)


def _linked(path, metadata):
    return (stat.S_ISLNK(metadata.st_mode)
            or getattr(path, 'is_junction', lambda: False)()
            or bool(getattr(metadata, 'st_file_attributes', 0) & 0x400))


def _fingerprint(metadata):
    # On Windows 3.12, stat/lstat and the CRT-backed fstat can report different
    # creation-time values for a just-written file. Identity/size/mtime plus
    # the full registered SHA256 remain authoritative across both APIs.
    return (metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns)


@contextmanager
def _held(path, *, directory):
    """Hold local parents against rename/reparse changes during a save."""
    before = path.lstat()
    if _linked(path, before) or (not stat.S_ISDIR(before.st_mode) if directory
                               else not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
        raise _unavailable()
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class FileInfo(ctypes.Structure):
            _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('high', wintypes.DWORD), ('low', wintypes.DWORD),
                ('links', wintypes.DWORD), ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInfo)]
        kernel.GetFileInformationByHandle.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        # Directories share reads/writes, never deletion/rename. The final
        # source shares reads only until registry registration has completed.
        handle = kernel.CreateFileW(str(path), 0x80 if directory else 0x80000000,
            3 if directory else 1, None, 3, 0x00200000 | (0x02000000 if directory else 0), None)
        if handle == ctypes.c_void_p(-1).value:
            raise _unavailable()
        try:
            info = FileInfo()
            if (not kernel.GetFileInformationByHandle(handle, ctypes.byref(info))
                    or info.attributes & 0x400 or bool(info.attributes & 0x10) != directory
                    or not directory and info.links != 1):
                raise _unavailable()
            current = path.lstat()
            if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or _linked(path, current):
                raise _unavailable()
            yield
        finally:
            kernel.CloseHandle(handle)
    else:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        if directory:
            flags |= getattr(os, 'O_DIRECTORY', 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise _unavailable()
            yield
        finally:
            os.close(descriptor)
    after = path.lstat()
    if ((after.st_dev, after.st_ino) != (before.st_dev, before.st_ino) or _linked(path, after)
            or not directory and _fingerprint(after) != _fingerprint(before)):
        raise _unavailable()


@contextmanager
def _directory_holds(root):
    with ExitStack() as stack:
        for path in (*reversed(root.parents), root):
            stack.enter_context(_held(path, directory=True))
        yield


def _upload_name(value):
    if (not isinstance(value, str) or not value or len(value) > 200 or value != value.strip()
            or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') for c in value)
            or re.search(r'[/\\<>:"|?*]', value) or value.endswith(('.', ' '))
            or value in ('.', '..') or PureWindowsPath(value).is_reserved()
            or Path(value).suffix.lower() not in EXTENSIONS):
        raise AppError(UPLOAD_MESSAGE, 'invalid_idea_image', 422)
    return value


def _file_metadata(path, root):
    if not path.is_relative_to(root):
        raise _unavailable()
    for item in (path, *path.parents):
        metadata = item.lstat()
        if _linked(item, metadata):
            raise _unavailable()
        if item == path:
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                    or not 0 < metadata.st_size <= MAX_SOURCE_BYTES):
                raise _unavailable()
            result = metadata
        elif not stat.S_ISDIR(metadata.st_mode):
            raise _unavailable()
    return result


def _source_path(root, filename):
    if (not isinstance(filename, str) or not filename or len(filename) > 1024
            or re.search(r'[\x00-\x1f\x7f<>:"|?*]', filename)
            or PureWindowsPath(filename).drive or PureWindowsPath(filename).root):
        raise _unavailable()
    parts = filename.replace('\\', '/').split('/')
    if any(part in ('', '.', '..') or part.endswith((' ', '.')) for part in parts):
        raise _unavailable()
    target = root.joinpath(*parts)
    if target.suffix.lower() not in EXTENSIONS:
        raise _unavailable()
    return target


def _read_source(path, root, expected):
    before = _file_metadata(path, root)
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if _fingerprint(opened) != _fingerprint(before) or opened.st_nlink != 1:
            raise _unavailable()
        raw = bytearray()
        digest = hashlib.sha256()
        while True:
            block = os.read(descriptor, min(256 * 1024, MAX_SOURCE_BYTES + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            if len(raw) > MAX_SOURCE_BYTES:
                raise _unavailable()
            digest.update(block)
        after = os.fstat(descriptor)
        current = _file_metadata(path, root)
        if (len(raw) != before.st_size or _fingerprint(after) != _fingerprint(before)
                or _fingerprint(current) != _fingerprint(before) or after.st_nlink != 1
                or digest.hexdigest() != expected):
            raise _unavailable()
    finally:
        os.close(descriptor)
    return bytes(raw)


def _dimensions(image):
    if (image.format not in FORMATS or not 0 < image.width <= MAX_DIMENSION
            or not 0 < image.height <= MAX_DIMENSION or image.width * image.height > MAX_PIXELS
            or getattr(image, 'n_frames', 1) != 1):
        raise _unavailable()


def _thumbnail(raw):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(BytesIO(raw)) as image:
            _dimensions(image)
            image_format = image.format
            if (image_format == 'PNG' and not raw.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82')
                    or image_format == 'JPEG' and not raw.endswith(b'\xff\xd9')
                    or image_format == 'WEBP' and (raw[:4] != b'RIFF' or raw[8:12] != b'WEBP'
                        or int.from_bytes(raw[4:8], 'little') + 8 != len(raw))):
                raise _unavailable()
            image.verify()
        with Image.open(BytesIO(raw)) as image:
            _dimensions(image)
            image.load()
            oriented = ImageOps.exif_transpose(image)
            oriented.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            pixels = oriented.convert('RGBA')
            # Fresh pixels strip EXIF, ICC, comments, XMP and PNG text chunks.
            clean = Image.new('RGBA', pixels.size)
            clean.paste(pixels)
            output = BytesIO()
            clean.save(output, format='PNG')
            result = output.getvalue()
    if not result or len(result) > MAX_RESPONSE_BYTES:
        raise _unavailable()
    return result


class IdeaImages:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.cache = OrderedDict()
        self.cache_bytes = 0
        # A large source can decode to 160 MB. Serialize local preview work
        # so many gallery requests cannot multiply that memory requirement.
        self.lock = threading.Lock()

    def _remember(self, key, raw):
        old = self.cache.pop(key, None)
        if old is not None:
            self.cache_bytes -= len(old)
        self.cache[key] = raw
        self.cache_bytes += len(raw)
        while len(self.cache) > MAX_CACHE_ENTRIES or self.cache_bytes > CACHE_BYTE_BUDGET:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed)

    def get(self, idea_id):
        try:
            with self.lock:
                idea = self.pipeline.idea(idea_id)
                filename, expected = idea['filename'], idea['artwork_sha256']
                if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
                    raise _unavailable()
                root = Path(self.pipeline.directory).absolute()
                path = _source_path(root, filename)
                # Verify full source contents even on a cache hit. A changed
                # file with preserved timestamps must not serve a stale image.
                raw = _read_source(path, root, expected)
                key = (idea_id, filename, expected)
                cached = self.cache.get(key)
                if cached is not None:
                    self.cache.move_to_end(key)
                    return cached
                result = _thumbnail(raw)
                self._remember(key, result)
                return result
        except Exception:
            raise _unavailable() from None

    def _registered(self, digest):
        with self.pipeline.store.connect() as db:
            row = db.execute('SELECT id,filename FROM idea_pipeline_ideas WHERE artwork_sha256=?', (digest,)).fetchone()
        return dict(row) if row is not None else None

    def _same_input(self, root, digest, length, suffixes):
        # Most duplicates are already indexed and handled above. The bounded
        # fallback also finds an image still in the normal stability window.
        pending, entries, folders, read_bytes = [(root, 0)], 0, 0, 0
        while pending:
            folder, depth = pending.pop(); folders += 1
            if folders > 128:
                raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)
            with os.scandir(folder) as listing:
                for entry in listing:
                    entries += 1
                    if entries > 4000:
                        raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)
                    path = Path(entry.path)
                    metadata = path.lstat()
                    if _linked(path, metadata):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if depth < 6:
                            pending.append((path, depth + 1))
                        continue
                    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                            or path.suffix.lower() not in suffixes or metadata.st_size != length):
                        continue
                    read_bytes += length
                    if read_bytes > 256 * 1024 * 1024:
                        raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)
                    try:
                        _read_source(path, root, digest)
                        return path
                    except AppError:
                        continue
        return None

    def _register_upload(self, path, root, digest):
        from .idea_pipeline import _safe_file, _dump
        # A private upload is complete, fsynced, decoded and hash-checked. Only
        # its candidate is made immediately eligible; stability of other files
        # remains unchanged. The existing scanner creates planned variants only.
        with _held(path, directory=False):
            _read_source(path, root, digest)
            signature = _safe_file(path, root)
            stamp = self.pipeline._now().timestamp() - self.pipeline.stable_seconds - .001
            with self.pipeline.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('''INSERT INTO idea_pipeline_candidates(path,signature,first_seen) VALUES(?,?,?)
                    ON CONFLICT(path) DO UPDATE SET signature=excluded.signature,
                    first_seen=MIN(idea_pipeline_candidates.first_seen,excluded.first_seen)''',
                    (str(path), _dump(signature), stamp))
            self.pipeline.scan()
            _read_source(path, root, digest)
        row = self._registered(digest)
        if row is None:
            raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)
        return self.pipeline.idea(row['id'])

    @staticmethod
    def _safe_existing(path):
        if not os.path.lexists(path):
            return None
        metadata = path.lstat()
        if (_linked(path, metadata) or not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)
        return metadata

    @staticmethod
    def _new_name(root, filename, digest):
        original = Path(filename)
        stem, suffix = original.stem, original.suffix
        names = [filename, stem[:150] + '-' + digest[:12] + suffix,
                 stem[:100] + '-' + digest + suffix]
        for name in names:
            if IdeaImages._safe_existing(root / name) is None:
                return root / name
        raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409)

    def save(self, raw: bytes, filename: str):
        filename = _upload_name(filename)
        if not isinstance(raw, bytes) or not raw:
            raise AppError(UPLOAD_MESSAGE, 'invalid_idea_image', 422)
        if len(raw) > MAX_SOURCE_BYTES:
            raise AppError('Ảnh idea không được vượt quá 25 MB.', 'idea_image_too_large', 413)
        with self.lock:
            try:
                preview = _thumbnail(raw)
                with Image.open(BytesIO(raw)) as image:
                    fmt = image.format
                suffixes = {'PNG': {'.png'}, 'JPEG': {'.jpg', '.jpeg'}, 'WEBP': {'.webp'}}[fmt]
                if Path(filename).suffix.lower() not in suffixes:
                    raise AppError(UPLOAD_MESSAGE, 'invalid_idea_image', 422)
            except Exception:
                raise AppError(UPLOAD_MESSAGE, 'invalid_idea_image', 422) from None
            digest = hashlib.sha256(raw).hexdigest()
            root = Path(self.pipeline.directory).absolute()
            temporary, owned_identity = None, None
            try:
                if not os.path.lexists(root):
                    with _directory_holds(root.parent):
                        root.mkdir()
                with _directory_holds(root):
                    known = self._registered(digest)
                    if known:
                        path = _source_path(root, known['filename'])
                        with _held(path, directory=False):
                            _read_source(path, root, digest)
                        return {'idea': self.pipeline.idea(known['id']), 'duplicate': True,
                                'filename': known['filename']}
                    # An unsafe colliding basename is rejected, never followed
                    # or silently bypassed to another path.
                    self._safe_existing(root / filename)
                    path = self._same_input(root, digest, len(raw), suffixes)
                    duplicate = path is not None
                    if path is None:
                        path = self._new_name(root, filename, digest)
                        temporary = root / ('.idea-upload-' + uuid4().hex + '.partial')
                        with temporary.open('xb') as stream:
                            opened = os.fstat(stream.fileno())
                            owned_identity = (opened.st_dev, opened.st_ino)
                            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
                        _read_source(temporary, root, digest)
                        if os.name == 'nt':
                            temporary.rename(path)
                        else:
                            os.link(temporary, path, follow_symlinks=False)
                            temporary.unlink()
                        temporary = None
                    idea = self._register_upload(path, root, digest)
                    self._remember((idea['id'], idea['filename'], digest), preview)
                    return {'idea': idea, 'duplicate': duplicate, 'filename': idea['filename']}
            except AppError as error:
                if error.status_code == 409:
                    raise
                raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409) from None
            except Exception:
                raise AppError(UPLOAD_CONFLICT, 'idea_image_conflict', 409) from None
            finally:
                if temporary is not None and owned_identity is not None:
                    try:
                        metadata = temporary.lstat()
                        if (metadata.st_dev, metadata.st_ino) == owned_identity and not _linked(temporary, metadata):
                            temporary.unlink()
                    except OSError:
                        pass

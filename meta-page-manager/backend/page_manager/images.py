"""Bounded local Page image validation; no remote URL or filesystem input."""
from io import BytesIO
import warnings
from PIL import Image,UnidentifiedImageError
from .errors import AppError

MAX_IMAGE_BYTES=8*1024*1024
MAX_IMAGE_PIXELS=20_000_000


def validate_image(raw,mime):
    content_type=(mime or '').split(';',1)[0].strip().lower()
    expected={'image/jpeg':'JPEG','image/png':'PNG'}.get(content_type)
    if expected is None or not isinstance(raw,(bytes,bytearray)) or not raw or len(raw)>MAX_IMAGE_BYTES:
        raise AppError('Chọn ảnh JPEG hoặc PNG không quá 8 MB.','invalid_image',422)
    data=bytes(raw)
    if expected=='JPEG' and not data.endswith(b'\xff\xd9') or expected=='PNG' and not data.endswith(b'\x00\x00\x00\x00IEND\xaeB`\x82'):
        raise AppError('Ảnh chưa đầy đủ hoặc có dữ liệu thừa ở cuối. Chọn lại ảnh JPEG/PNG.','invalid_image',422)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format!=expected or image.width<1 or image.height<1 or image.width*image.height>MAX_IMAGE_PIXELS or getattr(image,'n_frames',1)!=1:
                    raise ValueError()
                image.verify()
            # Verify alone checks container structure; load also rejects
            # truncated pixel data without accepting arbitrary file formats.
            with Image.open(BytesIO(data)) as image:image.load()
    except (UnidentifiedImageError,OSError,ValueError,SyntaxError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        raise AppError('Ảnh bị lỗi, sai định dạng hoặc vượt quá 20 triệu điểm ảnh. Chọn ảnh JPEG/PNG khác.','invalid_image',422) from None
    return data,content_type

"""Local synthetic pictures only; no production library or external images."""
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import zlib
from datetime import datetime, timezone

from PIL import Image, PngImagePlugin
import pytest

from page_manager.errors import AppError
import page_manager.idea_images as module
from page_manager.idea_images import IdeaImages, SAFE_MESSAGE
from page_manager.idea_pipeline import IdeaPipeline
from page_manager.store import Store


class Pipeline:
    def __init__(self, root):
        self.directory, self.ideas = root, {}

    def idea(self, key):
        if key not in self.ideas:
            raise AppError('PRIVATE PATH AND ERROR', 'not_found', 404)
        return self.ideas[key]


def encoded(fmt='PNG', size=(900, 1600), color=(40, 100, 180)):
    output = BytesIO()
    image = Image.new('RGB', size, color)
    if fmt == 'PNG':
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('secret', 'PRIVATE IMAGE COMMENT')
        image.save(output, format=fmt, pnginfo=metadata)
    elif fmt == 'JPEG':
        exif = Image.Exif(); exif[0x010E] = 'PRIVATE IMAGE COMMENT'
        image.save(output, format=fmt, exif=exif)
    else:
        image.save(output, format=fmt)
    return output.getvalue()


@pytest.fixture
def f(tmp_path):
    root = tmp_path / 'ideas'; root.mkdir()
    pipeline = Pipeline(root)
    return SimpleNamespace(root=root, pipeline=pipeline, images=IdeaImages(pipeline))


def add(f, raw=None, filename='picture.png'):
    raw = encoded() if raw is None else raw
    target = f.root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    key = str(uuid4())
    f.pipeline.ideas[key] = {'filename': filename, 'artwork_sha256': hashlib.sha256(raw).hexdigest()}
    return key, target


def unavailable(images, key):
    with pytest.raises(AppError) as error:
        images.get(key)
    assert error.value.status_code == 404
    assert error.value.code == 'idea_image_unavailable'
    assert str(error.value) == SAFE_MESSAGE


@pytest.mark.parametrize('fmt,ext', [('PNG', '.png'), ('JPEG', '.jpg'), ('WEBP', '.webp')])
def test_raster_thumbnail_is_sanitized_png_and_source_unchanged(f, fmt, ext):
    key, source = add(f, encoded(fmt), filename='sub/picture' + ext)
    before = source.read_bytes()
    result = f.images.get(key)
    assert len(result) <= module.MAX_RESPONSE_BYTES and result != before
    assert b'PRIVATE' not in result
    with Image.open(BytesIO(result)) as image:
        assert image.format == 'PNG' and image.mode == 'RGBA'
        assert image.width <= 600 and image.height <= 800
        assert image.width / image.height == pytest.approx(900 / 1600, abs=0.002)
        assert not image.info and not image.getexif()
    assert source.read_bytes() == before
    assert list(source.parent.iterdir()) == [source]


def test_jpeg_orientation_is_applied_and_metadata_removed(f):
    output = BytesIO()
    image = Image.new('RGB', (100, 200), 'orange')
    exif = Image.Exif(); exif[0x0112] = 6
    image.save(output, 'JPEG', exif=exif)
    key, _ = add(f, output.getvalue(), 'oriented.jpg')
    with Image.open(BytesIO(f.images.get(key))) as result:
        assert result.size == (200, 100) and not result.getexif()


@pytest.mark.parametrize('filename', ['../outside.png', 'sub/../../outside.png',
    '/absolute.png', '\\outside.png', 'C:\\private.png', 'C:private.png',
    '\\\\server\\share\\image.png', 'sub//picture.png', './picture.png',
    'picture.png:stream', 'sub./picture.png', 'sub /picture.png', 'private.txt'])
def test_metadata_filename_cannot_escape_or_use_windows_aliases(f, filename):
    key, _ = add(f)
    f.pipeline.ideas[key]['filename'] = filename
    unavailable(f.images, key)


def test_unknown_idea_never_returns_pipeline_exception(f):
    unavailable(f.images, str(uuid4()))


def test_hash_mismatch_never_serves_source(f):
    key, _ = add(f)
    f.pipeline.ideas[key]['artwork_sha256'] = 'a' * 64
    unavailable(f.images, key)
    assert not f.images.cache


def test_cache_hit_still_hashes_current_file(f):
    raw = encoded(size=(20, 20))
    key, source = add(f, raw)
    assert f.images.get(key)
    before = source.stat()
    changed = bytearray(raw); changed[-20] ^= 1
    source.write_bytes(changed)
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    unavailable(f.images, key)


def test_windows_creation_time_difference_between_stat_and_fstat_is_not_false_change(f, monkeypatch):
    key, _ = add(f, encoded(size=(20, 20)))
    actual = module.os.fstat
    def differing_creation_time(descriptor):
        value = actual(descriptor)
        fields = {name: getattr(value, name) for name in
                  ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_nlink', 'st_mode')}
        return SimpleNamespace(**fields, st_ctime_ns=value.st_ctime_ns - 1_000_000)
    monkeypatch.setattr(module.os, 'fstat', differing_creation_time)
    assert f.images.get(key).startswith(b'\x89PNG\r\n\x1a\n')


@pytest.mark.parametrize('fmt', ['GIF', 'BMP', 'TIFF'])
def test_non_whitelisted_formats_are_rejected_even_with_png_extension(f, fmt):
    key, _ = add(f, encoded(fmt, size=(20, 20)))
    unavailable(f.images, key)


@pytest.mark.parametrize('raw', [b'not an image', b'\x89PNG\r\n\x1a\ntruncated', b''])
def test_corrupt_or_empty_source_is_safe_404(f, raw):
    key, _ = add(f, raw)
    unavailable(f.images, key)


def test_png_trailing_payload_is_rejected(f):
    key, _ = add(f, encoded() + b'PRIVATE TRAILING PAYLOAD')
    unavailable(f.images, key)


def test_source_size_is_bounded_before_read(f):
    key, path = add(f, encoded(size=(10, 10)))
    with path.open('r+b') as stream:
        stream.truncate(module.MAX_SOURCE_BYTES + 1)
    unavailable(f.images, key)


@pytest.mark.parametrize('size', [(10_000, 4_001), (20_001, 1), (1, 20_001)])
def test_bomb_dimensions_rejected_before_pixel_decode(f, size):
    raw = bytearray(encoded(size=(2, 2)))
    raw[16:20], raw[20:24] = size[0].to_bytes(4, 'big'), size[1].to_bytes(4, 'big')
    raw[29:33] = zlib.crc32(raw[12:29]).to_bytes(4, 'big')
    key, _ = add(f, bytes(raw))
    unavailable(f.images, key)


def test_animated_webp_is_rejected(f):
    output = BytesIO()
    Image.new('RGB', (10, 10), 'red').save(output, 'WEBP', save_all=True,
        append_images=[Image.new('RGB', (10, 10), 'blue')], duration=100, loop=0)
    key, _ = add(f, output.getvalue(), 'animation.webp')
    unavailable(f.images, key)


def test_hardlinked_source_is_rejected(f):
    key, path = add(f)
    os.link(path, f.root / 'hardlink.png')
    unavailable(f.images, key)


def test_symlink_source_is_rejected(f):
    key, original = add(f)
    link = f.root / 'link.png'
    try:
        link.symlink_to(original)
    except OSError:
        pytest.skip('OS did not grant synthetic symlink creation')
    f.pipeline.ideas[key]['filename'] = 'link.png'
    unavailable(f.images, key)


def test_linked_parent_or_junction_is_rejected(f, monkeypatch):
    key, _ = add(f, filename='sub/picture.png')
    original = module._linked
    monkeypatch.setattr(module, '_linked', lambda path, metadata:
                        path == f.root / 'sub' or original(path, metadata))
    unavailable(f.images, key)


def test_linked_library_root_is_rejected(f, monkeypatch):
    key, _ = add(f)
    original = module._linked
    monkeypatch.setattr(module, '_linked', lambda path, metadata:
                        path == f.root or original(path, metadata))
    unavailable(f.images, key)


def test_fd_content_changed_during_read_is_rejected(f, monkeypatch):
    key, _ = add(f)
    original = module.os.read
    changed = [False]
    def read(fd, limit):
        data = original(fd, limit)
        if data and not changed[0]:
            changed[0] = True
            data = b'x' + data[1:]
        return data
    monkeypatch.setattr(module.os, 'read', read)
    unavailable(f.images, key)


def test_path_replaced_during_open_file_read_is_rejected(f, monkeypatch):
    key, source = add(f)
    original = module.os.read
    replaced = [False]
    def read(fd, limit):
        data = original(fd, limit)
        if data and not replaced[0]:
            replaced[0] = True
            replacement = source.with_name('replacement.png')
            replacement.write_bytes(source.read_bytes())
            replacement.replace(source)
        return data
    monkeypatch.setattr(module.os, 'read', read)
    unavailable(f.images, key)


def test_cache_entry_limit_and_lru(f, monkeypatch):
    monkeypatch.setattr(module, 'MAX_CACHE_ENTRIES', 2)
    first, _ = add(f, encoded(size=(10, 10), color='red'), 'one.png')
    second, _ = add(f, encoded(size=(10, 10), color='green'), 'two.png')
    third, _ = add(f, encoded(size=(10, 10), color='blue'), 'three.png')
    f.images.get(first); f.images.get(second); f.images.get(first); f.images.get(third)
    assert len(f.images.cache) == 2
    assert {key[0] for key in f.images.cache} == {first, third}
    assert f.images.cache_bytes == sum(map(len, f.images.cache.values()))


def test_cache_byte_budget_is_bounded(f, monkeypatch):
    monkeypatch.setattr(module, 'CACHE_BYTE_BUDGET', 1)
    key, _ = add(f, encoded(size=(10, 10)))
    assert f.images.get(key)
    assert not f.images.cache and f.images.cache_bytes == 0


def test_output_response_limit_is_enforced(f, monkeypatch):
    monkeypatch.setattr(module, 'MAX_RESPONSE_BYTES', 1)
    key, _ = add(f, encoded(size=(10, 10)))
    unavailable(f.images, key)
    assert not f.images.cache


@pytest.fixture
def uploads(tmp_path):
    root = tmp_path / 'Idea'; root.mkdir()
    store = Store(tmp_path / 'manager')
    now = datetime(2030, 11, 1, 12, tzinfo=timezone.utc)
    pipeline = IdeaPipeline(SimpleNamespace(store=store), directory=root,
        output_directory=tmp_path / 'ABC', stable_seconds=30, clock=lambda: now)
    return SimpleNamespace(root=root, store=store, pipeline=pipeline, images=IdeaImages(pipeline))


def test_upload_preserves_bytes_and_immediately_registers_planned_idea_only(uploads):
    f = uploads
    raw = encoded(size=(100, 200))
    result = f.images.save(raw, 'httpsblueox179.comcampaignexample.png')
    assert result['duplicate'] is False
    assert result['filename'] == 'httpsblueox179.comcampaignexample.png'
    assert (f.root / result['filename']).read_bytes() == raw
    assert result['idea']['artwork_sha256'] == hashlib.sha256(raw).hexdigest()
    assert result['idea']['campaign_state'] == 'prepared' and result['idea']['campaign'] is None
    assert len(result['idea']['variants']) == 7
    assert all(v['status'] == 'planned' and v['video_sha256'] is None for v in result['idea']['variants'])
    assert not f.pipeline.status()['jobs'] and not f.store.reel_jobs() and not f.store.reel_assets()
    assert list(f.root.iterdir()) == [f.root / result['filename']]
    with Image.open(BytesIO(f.images.get(result['idea']['id']))) as preview:
        assert preview.format == 'PNG' and preview.mode == 'RGBA'


@pytest.mark.parametrize('fmt,filename', [('JPEG', 'idea.JPEG'), ('WEBP', 'idea.webp')])
def test_upload_accepts_supported_actual_format_and_preserves_original(uploads, fmt, filename):
    raw = encoded(fmt, size=(20, 30))
    result = uploads.images.save(raw, filename)
    assert result['filename'] == filename and (uploads.root / filename).read_bytes() == raw


def test_same_artwork_deduplicates_across_filenames_without_another_file(uploads):
    f = uploads
    raw = encoded(size=(100, 200))
    first = f.images.save(raw, 'first.png')
    second = f.images.save(raw, 'renamed.png')
    assert second['duplicate'] is True and second['idea']['id'] == first['idea']['id']
    assert second['filename'] == 'first.png' and len(list(f.root.iterdir())) == 1


def test_same_basename_different_artwork_gets_hash_suffix_without_overwrite(uploads):
    f = uploads
    original, other = encoded(color='red'), encoded(color='blue')
    first = f.images.save(original, 'shirt.png')
    second = f.images.save(other, 'shirt.png')
    assert second['duplicate'] is False and second['idea']['id'] != first['idea']['id']
    assert second['filename'] == 'shirt-' + hashlib.sha256(other).hexdigest()[:12] + '.png'
    assert (f.root / 'shirt.png').read_bytes() == original
    assert (f.root / second['filename']).read_bytes() == other


def test_pending_input_same_hash_is_reused_and_other_stability_is_preserved(uploads):
    f = uploads
    raw = encoded(color='red')
    existing = f.root / 'existing.png'; existing.write_bytes(raw)
    other = f.root / 'other.png'; other.write_bytes(encoded(color='blue'))
    f.pipeline.scan()
    before = existing.stat().st_mtime_ns
    result = f.images.save(raw, 'new-name.png')
    assert result['duplicate'] is True and result['filename'] == 'existing.png'
    assert existing.read_bytes() == raw and existing.stat().st_mtime_ns == before
    assert not (f.root / 'new-name.png').exists()
    assert f.pipeline.stable_seconds == 30 and f.pipeline.status()['idea_count'] == 1
    assert {path.name for path in f.root.iterdir()} == {'existing.png', 'other.png'}


@pytest.mark.parametrize('filename', ['../outside.png', 'sub/image.png', 'sub\\image.png',
    'C:\\private.png', 'CON.png', 'nul.jpeg', 'LPT1.webp', 'COM9.jpg',
    'image.png:stream', 'image.\npng', 'hidden\u200b.png', 'image.png ', 'image.svg', '', ' image.png'])
def test_upload_rejects_unsafe_basename_before_any_write(uploads, filename):
    f = uploads
    with pytest.raises(AppError) as error:
        f.images.save(encoded(), filename)
    assert error.value.status_code == 422 and error.value.code == 'invalid_idea_image'
    assert not list(f.root.iterdir()) and f.pipeline.status()['idea_count'] == 0


@pytest.mark.parametrize('raw,filename', [(b'not image', 'image.png'),
    (b'\x89PNG\r\n\x1a\ninvalid', 'image.png'), (b'', 'image.png'),
    (encoded('JPEG', size=(20, 20)), 'wrong.png')])
def test_upload_rejects_bad_image_or_extension_mismatch(uploads, raw, filename):
    with pytest.raises(AppError) as error:
        uploads.images.save(raw, filename)
    assert error.value.status_code == 422
    assert not list(uploads.root.iterdir())


def test_upload_size_limit_is_413(uploads):
    with pytest.raises(AppError) as error:
        uploads.images.save(b'x' * (module.MAX_SOURCE_BYTES + 1), 'image.png')
    assert error.value.status_code == 413 and not list(uploads.root.iterdir())


def test_upload_atomic_collision_cleans_owned_partial_without_registration(uploads, monkeypatch):
    def collision(*args, **kwargs):
        raise FileExistsError('PRIVATE path and details')
    if os.name == 'nt':
        monkeypatch.setattr(Path, 'rename', collision)
    else:
        monkeypatch.setattr(module.os, 'link', collision)
    with pytest.raises(AppError) as error:
        uploads.images.save(encoded(), 'image.png')
    assert error.value.status_code == 409 and 'PRIVATE' not in str(error.value)
    assert not list(uploads.root.iterdir()) and uploads.pipeline.status()['idea_count'] == 0


def test_upload_does_not_follow_hardlinked_collision(uploads):
    original = uploads.root / 'image.png'; original.write_bytes(encoded(color='red'))
    os.link(original, uploads.root / 'linked.png')
    before = original.read_bytes()
    with pytest.raises(AppError) as error:
        uploads.images.save(encoded(color='blue'), 'image.png')
    assert error.value.status_code == 409 and original.read_bytes() == before
    assert len(list(uploads.root.iterdir())) == 2


def test_upload_rejects_reparse_parent(uploads, monkeypatch):
    original = module._linked
    monkeypatch.setattr(module, '_linked', lambda path, metadata:
                        path == uploads.root or original(path, metadata))
    with pytest.raises(AppError) as error:
        uploads.images.save(encoded(), 'image.png')
    assert error.value.status_code == 409 and not list(uploads.root.iterdir())


def test_upload_can_create_fixed_root_if_only_that_directory_is_missing(uploads):
    uploads.root.rmdir()
    result = uploads.images.save(encoded(), 'image.png')
    assert result['idea']['filename'] == 'image.png'
    assert (uploads.root / 'image.png').is_file()


def test_upload_cannot_silently_replace_changed_registered_artwork(uploads):
    raw = encoded(color='red')
    result = uploads.images.save(raw, 'image.png')
    (uploads.root / 'image.png').write_bytes(encoded(color='blue'))
    with pytest.raises(AppError) as error:
        uploads.images.save(raw, 'second.png')
    assert error.value.status_code == 409
    assert not (uploads.root / 'second.png').exists()
    assert uploads.pipeline.status()['idea_count'] == 1

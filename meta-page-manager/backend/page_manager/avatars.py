"""Private, source-bound Page avatar cache; Graph/CDN reads only."""
from collections import OrderedDict
from io import BytesIO
import re
import time
from urllib.parse import parse_qs, unquote, urlsplit
import warnings

import httpx
from PIL import Image, UnidentifiedImageError


MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 4_000_000
MAX_OUTPUT_BYTES = 1024 * 1024
TTL_SECONDS = 15 * 60
NEGATIVE_TTL_SECONDS = 60
MAX_ENTRIES = 128
CACHE_BYTE_BUDGET = 16 * 1024 * 1024
MIME_FORMATS = {'image/jpeg': 'JPEG', 'image/png': 'PNG', 'image/webp': 'WEBP'}


class AvatarUnavailable(Exception):
    """No avatar can be returned safely; never includes provider details."""


def _cdn_url(value, forbidden=()):
    if not isinstance(value, str) or len(value) > 8192 or re.search(r'[\x00-\x20\x7f]', value):
        raise AvatarUnavailable()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        allowed = (host == 'platform-lookaside.fbsbx.com' or
                   bool(re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+fbcdn\.net', host, re.ASCII)))
        if (parsed.scheme != 'https' or not allowed or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or parsed.fragment or not parsed.path.startswith('/')):
            raise ValueError()
        query = parse_qs(parsed.query, keep_blank_values=True)
        if any(key.casefold() in ('access_token', 'token', 'appsecret_proof', 'authorization') for key in query):
            raise ValueError()
        decoded = unquote(value)
        if any(secret and secret in decoded for secret in forbidden):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise AvatarUnavailable() from None


def _raster(raw, content_type):
    mime = (content_type or '').split(';', 1)[0].strip().lower()
    expected = MIME_FORMATS.get(mime)
    if expected is None or not raw or len(raw) > MAX_DOWNLOAD_BYTES:
        raise AvatarUnavailable()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as image:
                if (image.format != expected or not 0 < image.width * image.height <= MAX_PIXELS
                        or getattr(image, 'n_frames', 1) != 1):
                    raise ValueError()
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
                image.thumbnail((512, 512), Image.Resampling.LANCZOS)
                pixels = image.convert('RGBA')
                # A fresh canvas strips EXIF, text chunks, ICC profiles and
                # provider metadata instead of serving downloaded bytes.
                clean = Image.new('RGBA', pixels.size)
                clean.paste(pixels)
                output = BytesIO()
                clean.save(output, format='PNG')
                result = output.getvalue()
                if not result or len(result) > MAX_OUTPUT_BYTES:
                    raise ValueError()
                return result
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AvatarUnavailable() from None


class PageAvatars:
    def __init__(self, manager, *, transport=None, clock=time.monotonic):
        self.manager, self.transport, self.clock = manager, transport, clock
        self.cache = OrderedDict()
        self.cache_bytes = 0

    def invalidate(self, page_id):
        for key in list(self.cache):
            if key[0] == page_id:
                _, raw = self.cache.pop(key)
                self.cache_bytes -= len(raw) if raw else 0

    def _cached(self, key):
        entry = self.cache.get(key)
        if entry is None:
            return False, None
        expires, raw = entry
        if expires <= self.clock():
            self.cache.pop(key)
            self.cache_bytes -= len(raw) if raw else 0
            return False, None
        self.cache.move_to_end(key)
        return True, raw

    def _remember(self, key, raw):
        if key in self.cache:
            _, old = self.cache.pop(key)
            self.cache_bytes -= len(old) if old else 0
        ttl = TTL_SECONDS if raw is not None else NEGATIVE_TTL_SECONDS
        self.cache[key] = (self.clock() + ttl, raw)
        self.cache_bytes += len(raw) if raw else 0
        while len(self.cache) > MAX_ENTRIES or self.cache_bytes > CACHE_BYTE_BUDGET:
            _, (_, removed) = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed) if removed else 0

    async def _download(self, url):
        async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                     timeout=httpx.Timeout(15, connect=5)) as client:
            async with client.stream('GET', url, headers={'Accept': 'image/png,image/jpeg,image/webp'}) as response:
                if response.status_code != 200:
                    raise AvatarUnavailable()
                declared = response.headers.get('content-length')
                if declared is not None and (not declared.isascii() or not declared.isdigit() or not 0 < int(declared) <= MAX_DOWNLOAD_BYTES):
                    raise AvatarUnavailable()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_DOWNLOAD_BYTES:
                        raise AvatarUnavailable()
                return _raster(bytes(raw), response.headers.get('content-type'))

    async def get(self, page_id, connection_id=None):
        if (not isinstance(page_id, str) or not re.fullmatch(r'[0-9]{1,32}', page_id)
                or connection_id is not None and (type(connection_id) is not int or connection_id <= 0)):
            raise AvatarUnavailable()
        key = None
        try:
            source = self.manager._edit_source(page_id, connection_id)
            key = (page_id, source['id'], source['token_revision'])
            hit, raw = self._cached(key)
            if hit:
                if raw is None:
                    raise AvatarUnavailable()
                return raw
            # Match the application's Page → connection → capacity ordering.
            # Recheck the cache after the Page lock to coalesce lazy requests.
            async with self.manager._page_lock(page_id):
                source = self.manager._edit_source(page_id, key[1], key[2])
                hit, raw = self._cached(key)
                if hit:
                    if raw is None:
                        raise AvatarUnavailable()
                    return raw
                if source.get('status') == 'api_access_blocked':
                    raise AvatarUnavailable()
                async with self.manager._lock(key[1]), self.manager.capacity:
                    source = self.manager._edit_source(page_id, key[1], key[2])
                    async with self.manager.graph_factory() as graph:
                        token, forbidden = await self.manager._edit_token(graph, page_id, source)
                        value = await graph.get(page_id, token, {'fields': 'id,picture.type(large)'})
                    self.manager._edit_source(page_id, key[1], key[2])
                    if not isinstance(value, dict) or value.get('id') != page_id:
                        raise AvatarUnavailable()
                    picture = value.get('picture')
                    data = picture.get('data') if isinstance(picture, dict) else None
                    if not isinstance(data, dict) or data.get('is_silhouette') is True:
                        raise AvatarUnavailable()
                    target = _cdn_url(data.get('url'), forbidden)
                    raw = await self._download(target)
                    self.manager._edit_source(page_id, key[1], key[2])
                    self._remember(key, raw)
                    return raw
        except Exception:
            if key is not None and key not in self.cache:
                self._remember(key, None)
            raise AvatarUnavailable() from None

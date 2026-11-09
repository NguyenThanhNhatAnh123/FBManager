"""Bounded, advisory worker progress. This never approves media or publishing."""
import json
import os
from pathlib import Path
import stat
from uuid import UUID


MAX_BYTES = 32 * 1024
STATES = frozenset(('pending', 'running', 'passed', 'needs_review'))
QA_STATES = STATES | {'manual_review_pending'}


def _linked(path, info):
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & 0x400)
            or getattr(path, 'is_junction', lambda: False)())


def _identity(info):
    return info.st_dev, info.st_ino


def _fingerprint(info):
    return (*_identity(info), info.st_size, info.st_mtime_ns)


def _parents(folder):
    found = []
    for path in (folder, *folder.parents):
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or _linked(path, info):
            raise ValueError('unsafe progress directory')
        found.append((path, _identity(info)))
    return found


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate progress field')
        value[key] = item
    return value


def pipeline_progress(folder, run_id, fence, count):
    """Return closed stage enums from one fixed file, or None for any unknown state.

    The owning runner supplies the trusted folder and frozen identity/count. The
    result is display-only; producer statements cannot become a QA receipt.
    Atomic replacement by the producer is supported between reads, not mid-read.
    """
    try:
        if (not isinstance(run_id, str) or str(UUID(run_id)) != run_id or UUID(run_id).int == 0
                or type(fence) is not int or fence < 1
                or type(count) is not int or not 1 <= count <= 14):
            return None
        folder = Path(folder).absolute()
        parents = _parents(folder)
        path = folder / 'pipeline-progress.json'
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or _linked(path, before)
                or before.st_nlink != 1 or not 0 < before.st_size <= MAX_BYTES):
            return None
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if _fingerprint(opened) != _fingerprint(before) or opened.st_nlink != 1:
                return None
            raw = stream.read(MAX_BYTES + 1)
            if len(raw) != opened.st_size or _fingerprint(os.fstat(stream.fileno())) != _fingerprint(opened):
                return None
        after = path.lstat()
        if _linked(path, after) or _fingerprint(after) != _fingerprint(before) or after.st_nlink != 1:
            return None
        if any(_linked(parent, parent.lstat()) or _identity(parent.lstat()) != identity
               for parent, identity in parents):
            return None
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
        if (not isinstance(value, dict) or set(value) != {
                'schema_version', 'run_id', 'lease_fence', 'variant_count', 'mode', 'product', 'items'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['run_id'] != run_id or type(value['lease_fence']) is not int
                or value['lease_fence'] != fence or type(value['variant_count']) is not int
                or value['variant_count'] != count or value['mode'] != 'rolling_v1'
                or not isinstance(value['product'], str) or value['product'] not in STATES
                or not isinstance(value['items'], list) or len(value['items']) != count):
            return None
        items = []
        for ordinal, item in enumerate(value['items'], 1):
            if (not isinstance(item, dict) or set(item) != {'ordinal', 'image', 'video', 'qa'}
                    or type(item['ordinal']) is not int or item['ordinal'] != ordinal
                    or any(not isinstance(item[key], str) or item[key] not in STATES for key in ('image', 'video'))
                    or not isinstance(item['qa'], str) or item['qa'] not in QA_STATES
                    or item['qa'] == 'manual_review_pending' and (item['image'] != 'passed' or item['video'] != 'passed')):
                return None
            items.append({key: item[key] for key in ('ordinal', 'image', 'video', 'qa')})
        return {'mode': 'rolling_v1', 'variant_count': count, 'product': value['product'], 'items': items}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return None

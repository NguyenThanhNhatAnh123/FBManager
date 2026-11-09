"""Offline Page Manager handoff validator. No generation, upload, or publishing."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid

ABC_ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.0"
MAX_JSON = 2 * 1024 * 1024
HEX = re.compile(r"[0-9a-f]{64}\Z")
CAMPAIGN = re.compile(r"https://blueox179\.com/campaign/[A-Za-z0-9][A-Za-z0-9_-]*/?\Z")
QA_NAMES = ("visual", "audio", "technical", "distinct_variant", "caption")
METHODS = {
    "visual": "source_comparison_and_video_frames",
    "audio": "listened_to_final_mix",
    "technical": "ffprobe_full_decode_bounds_and_motion",
    "distinct_variant": "independent_image_and_scene_review",
    "caption": "exact_product_link_check",
}
APPROVED_AUDIO_METHOD = "approved_mix_hash_match"
AUDIO_APPROVAL_FIELDS = ("approval_id", "actual_pcm_sha256", "approved_pcm_sha256")
DENIED = {".git", ".aws", ".ssh", ".azure", ".env", "auth", "oauth", "token", "tokens", "credentials", "secrets"}


class BridgeError(Exception):
    def __init__(self, code, detail):
        self.code, self.detail = code, str(detail)
        super().__init__(f"{code}: {detail}")


def require(condition, code, detail):
    if not condition:
        raise BridgeError(code, detail)


def object_fields(value, required, optional=(), context="object"):
    require(isinstance(value, dict), "SCHEMA", context + " must be an object")
    require(set(required) <= value.keys(), "SCHEMA", context + " missing fields: " + str(sorted(set(required) - value.keys())))
    require(value.keys() <= set(required) | set(optional), "SCHEMA", context + " unknown fields: " + str(sorted(value.keys() - set(required) - set(optional))))


def identifier(value, label):
    require(isinstance(value, str), "UUID", label)
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise BridgeError("UUID", label + " must be a canonical UUID")
    require(str(parsed) == value and parsed.int != 0 and parsed.variant == uuid.RFC_4122, "UUID", label)
    return value


def digest(value, label):
    require(isinstance(value, str) and HEX.fullmatch(value), "HASH_FORMAT", label)
    return value


def string(value, label, limit=4000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit and "\x00" not in value, "SCHEMA", label)
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(string(value, "reviewed_at", 80).replace("Z", "+00:00"))
    except ValueError:
        raise BridgeError("QA_TIMESTAMP", "reviewed_at must include an ISO8601 timezone")
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "QA_TIMESTAMP", "timezone required")
    require(parsed <= datetime.now(timezone.utc), "QA_TIMESTAMP", "future review time")


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def parse_json(data, label):
    def pairs(items):
        out = {}
        for key, value in items:
            require(key not in out, "DUPLICATE_JSON_KEY", key)
            out[key] = value
        return out
    try:
        return json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(BridgeError("SCHEMA", "nonfinite JSON: " + x)))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BridgeError("JSON", label + ": " + str(exc))


if os.name == "nt":
    import ctypes
    from ctypes import wintypes
    import msvcrt

    K = ctypes.WinDLL("kernel32", use_last_error=True)

    class FILE_INFO(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                    ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    K.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                             wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    K.CreateFileW.restype = wintypes.HANDLE
    K.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILE_INFO)]
    K.GetFileInformationByHandle.restype = wintypes.BOOL
    K.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    K.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    K.CloseHandle.argtypes = [wintypes.HANDLE]
    K.CloseHandle.restype = wintypes.BOOL
    K.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    K.CreateMutexW.restype = wintypes.HANDLE
    K.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    K.WaitForSingleObject.restype = wintypes.DWORD
    K.ReleaseMutex.argtypes = [wintypes.HANDLE]
    INVALID = ctypes.c_void_p(-1).value

    def win_info(handle):
        info = FILE_INFO()
        require(K.GetFileInformationByHandle(handle, ctypes.byref(info)), "FILE_METADATA", ctypes.get_last_error())
        return info

    def final_name(handle):
        size = K.GetFinalPathNameByHandleW(handle, None, 0, 0)
        require(size, "FILE_PATH", ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size + 1)
        require(K.GetFinalPathNameByHandleW(handle, buffer, size + 1, 0), "FILE_PATH", ctypes.get_last_error())
        name = buffer.value
        require(not name.startswith("\\\\?\\UNC\\"), "FILE_PATH", "UNC paths are not accepted")
        return name[4:] if name.startswith("\\\\?\\") else name


class Files:
    """Validate lexical paths and opened handles, including Windows reparse points."""
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))
        require(self.root.is_absolute(), "ROOT", "absolute root required")
        require(self.root.is_relative_to(Path(os.path.abspath(ABC_ROOT))), "ROOT", "root must remain within the producer project")

    def path(self, value, suffixes=None):
        require(isinstance(value, str) and value and "\x00" not in value, "PATH", "path must be text")
        p = Path(value)
        require(p.is_absolute() and not value.startswith(("\\\\", "//")), "PATH", "local absolute path required")
        require(".." not in p.parts, "PATH", "parent traversal is not accepted")
        p = Path(os.path.abspath(p))
        require(p != self.root and p.is_relative_to(self.root), "OUTSIDE_ROOT", str(p))
        for part in p.parts[1:]:
            low = part.lower()
            require(not part.endswith((".", " ")) and ":" not in part, "PATH", "ambiguous path component")
            require(low not in DENIED and Path(low).stem not in DENIED and not low.startswith(".env."), "SENSITIVE_PATH", part)
        if suffixes:
            require(p.suffix.lower() in suffixes, "FILE_TYPE", str(p))
        return p

    @contextmanager
    def directories(self, folder):
        chain = list(reversed(folder.parents)) + [folder]
        handles = []
        try:
            for p in chain:
                info = p.lstat()
                require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), "LINK_PATH", str(p))
                require(not getattr(info, "st_file_attributes", 0) & 0x400, "REPARSE_POINT", str(p))
                if os.name == "nt":
                    h = K.CreateFileW(str(p), 0x80, 3, None, 3, 0x02000000 | 0x00200000, None)
                    require(h != INVALID, "DIRECTORY_OPEN", str(p))
                    handles.append(h)
                    meta = win_info(h)
                    require(meta.attributes & 0x10 and not meta.attributes & 0x400, "REPARSE_POINT", str(p))
                    require(os.path.normcase(final_name(h)) == os.path.normcase(str(p)), "PATH_CHANGED", str(p))
            yield
        except OSError as exc:
            raise BridgeError("FILE_ACCESS", str(exc))
        finally:
            for h in reversed(handles):
                K.CloseHandle(h)

    @contextmanager
    def opened(self, value, suffixes=None):
        p = self.path(value, suffixes)
        with self.directories(p.parent):
            handle = None
            fd = None
            try:
                before = p.lstat()
                require(stat.S_ISREG(before.st_mode) and not stat.S_ISLNK(before.st_mode), "NOT_REGULAR", str(p))
                require(not getattr(before, "st_file_attributes", 0) & 0x400, "REPARSE_POINT", str(p))
                require(before.st_nlink == 1, "HARDLINK", str(p))
                if os.name == "nt":
                    handle = K.CreateFileW(str(p), 0x80000000, 1, None, 3, 0x00200000 | 0x08000000, None)
                    require(handle != INVALID, "FILE_OPEN", str(p))
                    meta = win_info(handle)
                    require(not meta.attributes & (0x400 | 0x10) and meta.links == 1, "LINK_FILE", str(p))
                    require(os.path.normcase(final_name(handle)) == os.path.normcase(str(p)), "PATH_CHANGED", str(p))
                    fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                    handle = None  # fd owns the handle now
                else:
                    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as stream:
                    fd = None
                    start = os.fstat(stream.fileno())
                    require(stat.S_ISREG(start.st_mode) and start.st_nlink == 1, "LINK_FILE", str(p))
                    require((before.st_dev, before.st_ino) == (start.st_dev, start.st_ino), "FILE_CHANGED", str(p))
                    yield p, stream
                    end = os.fstat(stream.fileno())
                    require((start.st_dev, start.st_ino, start.st_size, start.st_mtime_ns, start.st_nlink) ==
                            (end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns, end.st_nlink), "FILE_CHANGED", str(p))
            except OSError as exc:
                raise BridgeError("FILE_ACCESS", str(exc))
            finally:
                if fd is not None:
                    os.close(fd)
                if handle is not None and handle != INVALID:
                    K.CloseHandle(handle)

    def json(self, value):
        with self.opened(value, {".json"}) as (p, stream):
            data = stream.read(MAX_JSON + 1)
            require(len(data) <= MAX_JSON, "JSON_SIZE", str(p))
        return parse_json(data, str(p)), sha_bytes(data), str(p)

    def hash(self, value, expected, suffixes):
        digest(expected, "expected file SHA256")
        with self.opened(value, suffixes) as (p, stream):
            h = hashlib.sha256()
            size = 0
            while chunk := stream.read(1024 * 1024):
                h.update(chunk)
                size += len(chunk)
            require(size > 0, "EMPTY_FILE", str(p))
            actual = h.hexdigest()
        require(actual == expected, "HASH_MISMATCH", str(p))
        return str(p), actual

    def write_json(self, value, data):
        p = self.path(value, {".json"})
        with self.directories(p.parent):
            if os.path.lexists(p):
                self.json(str(p))  # refuse unsafe existing output
            temporary = None
            try:
                fd, temporary = tempfile.mkstemp(prefix=".bridge-", suffix=".tmp", dir=p.parent)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                # Refuse a link introduced after the initial output check.
                if os.path.lexists(p):
                    self.json(str(p))
                os.replace(temporary, p)
                temporary = None
            finally:
                if temporary is not None:
                    Path(temporary).unlink(missing_ok=True)
        return str(p)


@contextmanager
def export_lock(path):
    if os.name == "nt":
        name = "Local\\PageManagerBridge-" + sha_bytes(os.path.normcase(str(path)).encode("utf-8"))
        h = K.CreateMutexW(None, False, name)
        require(h, "LOCK", "cannot create export mutex")
        acquired = False
        try:
            acquired = K.WaitForSingleObject(h, 10000) in (0, 0x80)
            require(acquired, "LOCK_TIMEOUT", "another export is in progress")
            yield
        finally:
            if acquired:
                K.ReleaseMutex(h)
            K.CloseHandle(h)
    else:
        # Development/test fallback; Windows uses a named mutex without a lock file.
        import fcntl
        lock = Path(str(path) + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            require(os.fstat(fd).st_nlink == 1, "HARDLINK", str(lock))
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def validate_job(job, files):
    object_fields(job, {"schema_version", "idea_id", "source_path", "source_sha256", "campaign_url", "campaign_verified", "caption_mode", "variants"}, {"lease_fence", "trigger_mode", "variant_count"}, "job")
    require(job["schema_version"] == VERSION, "VERSION", "job")
    identifier(job["idea_id"], "idea_id")
    require(job["campaign_verified"] is True, "CAMPAIGN_UNVERIFIED", "Via uploader verification is required")
    require(isinstance(job["campaign_url"], str) and CAMPAIGN.fullmatch(job["campaign_url"]), "CAMPAIGN_URL", "exact HTTPS blueox179.com campaign URL required")
    require(job["caption_mode"] == "product_link_only", "CAPTION_MODE", "product_link_only required")
    if "trigger_mode" in job:
        require(job["trigger_mode"] in ("manual_web_run_idea", "automatic_idea_batch"), "TRIGGER_MODE", "a Page Manager authorized manual or automatic run is required")
    if "lease_fence" in job:
        require(type(job["lease_fence"]) is int and job["lease_fence"] >= 0, "FENCE", "lease_fence")
    count = job.get("variant_count", 7)
    require(type(count) is int and 1 <= count <= 14, "VARIANT_COUNT", "variant_count must be an integer from one through fourteen")
    require(isinstance(job["variants"], list) and len(job["variants"]) == count, "VARIANT_COUNT", "variants must exactly match the frozen count; legacy jobs without a count require seven")
    ids, reservation_ids = set(), set()
    for v in job["variants"]:
        object_fields(v, {"variant_id", "reservation_id"}, (), "job variant")
        identifier(v["variant_id"], "variant_id")
        identifier(v["reservation_id"], "reservation_id")
        require(v["variant_id"] not in ids, "DUPLICATE_VARIANT", v["variant_id"])
        require(v["reservation_id"] not in reservation_ids, "DUPLICATE_RESERVATION", v["reservation_id"])
        ids.add(v["variant_id"])
        reservation_ids.add(v["reservation_id"])
    files.hash(job["source_path"], job["source_sha256"], {".png", ".jpg", ".jpeg", ".webp"})


def caption_check(caption, campaign_url):
    require(type(caption) is str and caption == campaign_url, "CAPTION_URL", "caption must equal the assigned campaign_url exactly, without prose, hashtags or whitespace")


MANUAL_REVIEW_FIELDS = frozenset(('schema_version', 'record_type', 'review_policy', 'status', 'ai_qa',
    'visual_review', 'distinct_review', 'run_id', 'idea_id', 'source_sha256', 'lease_fence',
    'variant_count', 'ordinal', 'variant_id', 'reservation_id', 'source_path', 'image_path',
    'image_sha256', 'image_origin', 'video_path', 'video_sha256', 'audio_source_sha256',
    'concept_id', 'caption_mode', 'caption', 'campaign', 'technical', 'approved_audio'))


def manual_review_item(receipt_path, root=ABC_ROOT):
    """Validate one pending-human artifact receipt, never grant visual approval.

    Files/hashes and closed metadata are checked offline. Via must independently
    recheck its active run/fence, allocation, decoder, PCM approval and human gate.
    This separate contract does not weaken legacy full-set QA validation.
    """
    files = Files(root)
    doc, actual, path = files.json(str(receipt_path))
    object_fields(doc, MANUAL_REVIEW_FIELDS, (), 'manual review receipt')
    require(doc['schema_version'] == VERSION and doc['record_type'] == 'page_manager_manual_review_variant'
            and doc['review_policy'] == 'manual_review_v1' and doc['status'] == 'manual_review_pending'
            and doc['ai_qa'] is False and doc['visual_review'] == doc['distinct_review'] == 'pending',
            'MANUAL_REVIEW_POLICY', path)
    for key in ('run_id', 'idea_id', 'variant_id', 'reservation_id'):
        identifier(doc[key], key)
    require(type(doc['lease_fence']) is int and doc['lease_fence'] >= 1, 'FENCE', path)
    require(type(doc['variant_count']) is int and 1 <= doc['variant_count'] <= 14
            and type(doc['ordinal']) is int and 1 <= doc['ordinal'] <= doc['variant_count'], 'VARIANT_COUNT', path)
    require(doc['image_origin'] in ('generated_native', 'preflight_seed'), 'MANUAL_IMAGE_ORIGIN', path)
    string(doc['concept_id'], 'concept_id', 200)
    for role, suffixes in (('source', {'.png', '.jpg', '.jpeg', '.webp'}),
                           ('image', {'.png'}), ('video', {'.mp4'})):
        files.hash(doc[role + '_path'], doc[role + '_sha256'], suffixes)
    require(len({doc[key + '_path'] for key in ('source', 'image', 'video')}) == 3
            and doc['source_sha256'] != doc['image_sha256'], 'SOURCE_REUSED_AS_VARIANT', path)
    require(doc['caption_mode'] == 'product_link_only', 'CAPTION_MODE', path)
    campaign = doc['campaign']
    object_fields(campaign, {'kind', 'url', 'source_url', 'shirt_color', 'evidence'}, (), 'campaign')
    require(campaign['kind'] in ('new', 'existing') and isinstance(campaign['url'], str)
            and CAMPAIGN.fullmatch(campaign['url']), 'CAMPAIGN_URL', path)
    templates = {'https://blueox179.com/campaign/zitat25', 'https://blueox179.com/campaign/ge2610'}
    require(campaign['source_url'] in templates if campaign['kind'] == 'new' else campaign['source_url'] is None,
            'CAMPAIGN_SOURCE', path)
    require(campaign['shirt_color'] in (None, 'black') if campaign['kind'] == 'existing' else
            campaign['shirt_color'] == ('black' if campaign['source_url'] == 'https://blueox179.com/campaign/zitat25' else None),
            'CAMPAIGN_SOURCE', path)
    proof = campaign['evidence']
    object_fields(proof, {'observed_at', 'artwork_matches', 'quote_matches', 'public_url'}, (), 'campaign evidence')
    timestamp(proof['observed_at'])
    require(proof['artwork_matches'] is True and proof['quote_matches'] is True and proof['public_url'] == campaign['url'],
            'CAMPAIGN_UNVERIFIED', path)
    caption_check(doc['caption'], campaign['url'])
    technical = doc['technical']
    expected = {'width': 1080, 'height': 1920, 'fps': 30, 'frames': 240, 'video_codec': 'h264',
                'audio_codec': 'aac', 'full_decode_passed': True, 'all_frames_artwork_visible': True,
                'motion_registration': 'not_run'}
    object_fields(technical, set(expected) | {'duration_seconds', 'minimum_margin_fraction'}, (), 'manual technical')
    require(all(type(technical[k]) is type(v) and technical[k] == v for k, v in expected.items()), 'QA_CHECKS', path)
    require(all(type(technical[k]) in (int, float) and math.isfinite(technical[k])
                for k in ('duration_seconds', 'minimum_margin_fraction'))
            and abs(technical['duration_seconds'] - 8) <= .06
            and .04 <= technical['minimum_margin_fraction'] <= .5, 'QA_CHECKS', path)
    digest(doc['audio_source_sha256'], 'audio source SHA256')
    audio = doc['approved_audio']
    object_fields(audio, {'review_method', 'binding', 'checks'}, (), 'approved audio')
    require(audio['review_method'] == APPROVED_AUDIO_METHOD and audio['checks'] == {'exact_pcm_match': True}
            and type(audio['checks'].get('exact_pcm_match')) is bool, 'QA_CHECKS', path)
    audio_binding = audio['binding']
    object_fields(audio_binding, set(AUDIO_APPROVAL_FIELDS) | {'audio_source_sha256'}, (), 'approved audio binding')
    for key in AUDIO_APPROVAL_FIELDS:
        digest(audio_binding[key], key)
    require(audio_binding['actual_pcm_sha256'] == audio_binding['approved_pcm_sha256']
            and audio_binding['audio_source_sha256'] == doc['audio_source_sha256'], 'QA_BINDING', path)
    return {'receipt': doc, 'receipt_path': path, 'receipt_sha256': actual}


def evidence(files, ref, kind, binding, caption, concept):
    object_fields(ref, {"path", "sha256"}, (), "QA reference")
    doc, actual, path = files.json(ref["path"])
    require(actual == digest(ref["sha256"], "QA SHA256"), "QA_HASH_MISMATCH", path)
    object_fields(doc, {"schema_version", "kind", "status", "review_method", "reviewer", "reviewed_at", "binding", "checks"}, (), "QA evidence")
    require(doc["schema_version"] == VERSION and doc["kind"] == kind, "QA_KIND", path)
    require(doc["status"] in ("passed", "pending", "failed"), "QA_STATUS", path)
    approved_audio = kind == "audio" and doc["review_method"] == APPROVED_AUDIO_METHOD
    require(doc["review_method"] == METHODS[kind] or approved_audio, "QA_METHOD", path)
    string(doc["reviewer"], "reviewer", 200)
    timestamp(doc["reviewed_at"])
    expected_binding = binding
    approval = {}
    if approved_audio:
        require("audio_source_sha256" in binding and isinstance(doc["binding"], dict), "QA_BINDING", path)
        approval = {key: digest(doc["binding"].get(key), key) for key in AUDIO_APPROVAL_FIELDS}
        expected_binding = {**binding, **approval}
    require(doc["binding"] == expected_binding, "QA_BINDING", path)
    checks = doc["checks"]
    require(isinstance(checks, dict), "QA_CHECKS", path)
    if doc["status"] != "passed":
        return False, {"path": path, "sha256": actual, "status": doc["status"], "review_method": doc["review_method"], **approval}
    if approved_audio:
        require(approval["actual_pcm_sha256"] == approval["approved_pcm_sha256"]
                and checks == {"exact_pcm_match": True} and type(checks.get("exact_pcm_match")) is bool, "QA_CHECKS", path)
    elif kind == "technical":
        expected = {"width": 1080, "height": 1920, "fps": 30, "frames": 240,
                    "video_codec": "h264", "audio_codec": "aac", "full_decode_passed": True,
                    "all_frames_artwork_visible": True, "subpixel_zoom_verified": True}
        object_fields(checks, set(expected) | {"duration_seconds", "minimum_margin_fraction", "motion_rms_pixels"}, (), "technical checks")
        require(all(type(checks[k]) is type(v) and checks[k] == v for k, v in expected.items()), "QA_CHECKS", path)
        for k in ("duration_seconds", "minimum_margin_fraction", "motion_rms_pixels"):
            require(type(checks[k]) in (int, float) and math.isfinite(checks[k]), "QA_CHECKS", k)
        require(abs(checks["duration_seconds"] - 8) <= .06 and .04 <= checks["minimum_margin_fraction"] <= .5
                and 0 <= checks["motion_rms_pixels"] < .15, "QA_CHECKS", path)
    else:
        expected = {
            "visual": {"original_artwork_compared": True, "complete_print_visible": True, "frames_reviewed": [0, 60, 120, 180, 239]},
            "audio": {"listened_to_final_mix": True, "cuts_checked": True, "loudness_checked": True, "clipping_checked": True},
            "distinct_variant": {"independent_source_photo": True, "new_scene_reviewed": True, "concept_id": concept},
            "caption": {"exact_product_link": True, "caption_sha256": sha_bytes(caption.encode("utf-8")),
                        "campaign_url": binding["campaign_url"], "idea_id": binding["idea_id"]},
        }[kind]
        require(checks == expected and all(type(checks.get(k)) is bool for k, v in expected.items() if type(v) is bool), "QA_CHECKS", path)
    return doc["status"] == "passed", {"path": path, "sha256": actual, "status": doc["status"], "review_method": doc["review_method"], **approval}


def build_result(job_path, manifest_path, root=ABC_ROOT):
    files = Files(root)
    job, job_sha, jp = files.json(job_path)
    manifest, manifest_sha, mp = files.json(manifest_path)
    validate_job(job, files)
    object_fields(manifest, {"schema_version", "idea_id", "source_sha256", "campaign_url", "caption_mode", "variants"}, {"lease_fence", "variant_count", "trigger_mode"}, "manifest")
    require(manifest["schema_version"] == VERSION, "VERSION", "manifest")
    require(manifest["caption_mode"] == "product_link_only", "CAPTION_MODE", "manifest caption_mode")
    for k in ("idea_id", "source_sha256", "campaign_url", "caption_mode"):
        require(manifest[k] == job[k], "JOB_BINDING", k)
    require(manifest.get("lease_fence") == job.get("lease_fence"), "FENCE", "manifest and job lease_fence must match")
    count = job.get("variant_count", 7)
    require(("variant_count" not in job or "variant_count" in manifest)
            and type(manifest.get("variant_count", 7)) is int and manifest.get("variant_count", 7) == count,
            "VARIANT_COUNT", "manifest count must match the frozen job count")
    if "trigger_mode" in manifest:
        require(manifest["trigger_mode"] == job.get("trigger_mode", "manual_web_run_idea"), "TRIGGER_MODE", "manifest trigger must match the job")
    require(isinstance(manifest["variants"], list) and len(manifest["variants"]) <= count, "VARIANT_COUNT", "partial manifests cannot exceed the frozen count")
    reservations = {v["variant_id"]: v["reservation_id"] for v in job["variants"]}
    supplied = {}
    for v in manifest["variants"]:
        object_fields(v, {"variant_id", "reservation_id"}, {"video_path", "video_sha256", "image_path", "image_sha256", "audio_source_sha256", "caption", "concept_id", "qa", *[k + "_verified" for k in QA_NAMES]}, "manifest variant")
        identifier(v["variant_id"], "variant_id")
        identifier(v["reservation_id"], "reservation_id")
        require(v["variant_id"] in reservations and v["variant_id"] not in supplied, "VARIANT_ID", v["variant_id"])
        require(v["reservation_id"] == reservations[v["variant_id"]], "RESERVATION", v["variant_id"])
        supplied[v["variant_id"]] = v
    results = []
    roles = {}
    for reserved in job["variants"]:
        v = supplied.get(reserved["variant_id"], reserved)
        out = {**reserved, "video_path": None, "video_sha256": None, "image_path": None, "image_sha256": None,
               "caption": None, "captionUS": None, "caption_mode": "product_link_only", "campaign_url": job["campaign_url"],
               "concept_id": None, "qa_evidence": {}, "pending_reasons": [], "ready_to_publish": False}
        if "audio_source_sha256" in v:
            out["audio_source_sha256"] = digest(v["audio_source_sha256"], "audio source SHA256")
        for role, suffixes in (("video", {".mp4"}), ("image", {".png", ".jpg", ".jpeg", ".webp"})):
            p, h = v.get(role + "_path"), v.get(role + "_sha256")
            require((p is None) == (h is None), "MEDIA_PAIR", role + " requires path and SHA256 together")
            if p is not None:
                path, actual = files.hash(p, h, suffixes)
                require(path not in roles, "REUSED_PATH", path)
                roles[path] = reserved["variant_id"]
                out[role + "_path"], out[role + "_sha256"] = path, actual
                if role == "image":
                    require(actual != job["source_sha256"], "SOURCE_REUSED_AS_VARIANT", path)
            else:
                out["pending_reasons"].append(role + "_missing")
        caption = v.get("caption")
        if caption is not None:
            caption_check(caption, job["campaign_url"])
            out["caption"] = out["captionUS"] = caption
        else:
            out["pending_reasons"].append("caption_missing")
        if v.get("concept_id") is not None:
            out["concept_id"] = string(v["concept_id"], "concept_id", 200)
        else:
            out["pending_reasons"].append("concept_id_missing")
        qa = v.get("qa", {})
        object_fields(qa, (), QA_NAMES, "qa")
        binding = {"idea_id": job["idea_id"], **reserved, "source_sha256": job["source_sha256"],
                   "video_sha256": out["video_sha256"], "image_sha256": out["image_sha256"], "campaign_url": job["campaign_url"]}
        if "lease_fence" in job:
            binding["lease_fence"] = job["lease_fence"]
        for kind in QA_NAMES:
            flag = kind + "_verified"
            claim = v.get(flag, False)
            require(type(claim) is bool, "QA_BOOLEAN", flag)
            passed = False
            if kind in qa:
                require(out["video_sha256"] and out["image_sha256"] and caption and out["concept_id"], "QA_BINDING", "QA needs media, caption and concept first")
                qa_binding = {**binding, "audio_source_sha256": out["audio_source_sha256"]} if kind == "audio" and "audio_source_sha256" in out else binding
                passed, proof = evidence(files, qa[kind], kind, qa_binding, caption, out["concept_id"])
                out["qa_evidence"][kind] = proof
            out[flag] = bool(claim and passed)
            if not out[flag]:
                out["pending_reasons"].append(flag + "_not_proven")
        results.append(out)
    for key in ("video_sha256", "image_sha256", "concept_id"):
        values = [v[key] for v in results if v[key] is not None]
        require(len(values) == len(set(values)), "DUPLICATE_" + key.upper(), "variants must have different " + key)
    complete_set = all(v["video_sha256"] and v["image_sha256"] and v["concept_id"] for v in results)
    for out in results:
        out["sha256"] = out["video_sha256"]
        if not complete_set:
            out["distinct_variant_verified"] = False
            out["pending_reasons"].append("variant_set_incomplete" if "variant_count" in job else "seven_variant_set_incomplete")
        out["state"] = "verified_for_handoff" if not out["pending_reasons"] else "pending"
        out["ready_for_handoff"] = out["state"] == "verified_for_handoff"
    normalized_job = {**job, "variants": sorted(job["variants"], key=lambda x: x["variant_id"])}
    return {"schema_version": VERSION, "record_type": "page_manager_video_handoff", "idea_id": job["idea_id"],
            "source_sha256": job["source_sha256"], "campaign_url": job["campaign_url"], "campaign_verified": True,
            "caption_mode": "product_link_only", "production_trigger_policy": job.get("trigger_mode", "manual_web_run_idea"),
            **({"variant_count": count} if "variant_count" in job else {}),
            "lease_fence": job.get("lease_fence"), "job_path": jp, "job_sha256": job_sha,
            "job_fingerprint": sha_bytes(canonical(normalized_job)), "manifest_path": mp, "manifest_sha256": manifest_sha,
            "production_enabled": False, "publishing_enabled": False, "ready_to_publish": False,
            "status": "verified_for_handoff" if all(v["ready_for_handoff"] for v in results) else "pending",
            "variants": results, "live_lease_verified": False,
            "limitations": ["Offline evidence binding validation; reviewer truth is not independently authenticated.",
                            "No media decoder, generator, uploader, scheduler or live Via lease service is invoked.",
                            "Via must recheck its active reservation/fence, exact job/manifest/result hashes and publishing authorization before consuming."]}


def resume_check(previous, result):
    require(previous.get("record_type") == result["record_type"] and previous.get("schema_version") == VERSION,
            "RESUME_CONFLICT", "not a bridge result")
    require(previous.get("job_fingerprint") == result["job_fingerprint"], "RESUME_CONFLICT", "source, link or Via reservations changed; use a separate result for the new reservation")
    prior = {v["variant_id"]: v for v in previous.get("variants", [])}
    require(set(prior) == {v["variant_id"] for v in result["variants"]}, "RESUME_CONFLICT", "variant set changed")
    for v in result["variants"]:
        old = prior[v["variant_id"]]
        require(old.get("reservation_id") == v["reservation_id"], "RESUME_CONFLICT", "reservation changed")
        for key in ("video_sha256", "image_sha256", "audio_source_sha256"):
            require(old.get(key) is None or old[key] == v[key], "RESUME_MEDIA_CHANGED", v["variant_id"] + ": " + key)


def export(job_path, manifest_path, result_path, root=ABC_ROOT):
    files = Files(root)
    target = files.path(result_path, {".json"})
    require(target not in (files.path(job_path), files.path(manifest_path)), "OUTPUT_INPUT_COLLISION", str(target))
    with files.directories(target.parent), export_lock(target):
        result = build_result(job_path, manifest_path, root)
        referenced = {result["job_path"], result["manifest_path"]}
        for v in result["variants"]:
            referenced.update(proof["path"] for proof in v["qa_evidence"].values())
        require(str(target) not in referenced, "OUTPUT_INPUT_COLLISION", str(target))
        changed = True
        if os.path.lexists(target):
            previous, _, _ = files.json(str(target))
            resume_check(previous, result)
            if previous == result:
                changed = False
        if changed:
            files.write_json(str(target), result)
        _, result_sha, _ = files.json(str(target))
        return {"valid": True, "status": result["status"], "result_path": str(target), "result_sha256": result_sha,
                "changed": changed, "verified_variants": sum(v["ready_for_handoff"] for v in result["variants"]),
                "pending_variants": sum(not v["ready_for_handoff"] for v in result["variants"]), "ready_to_publish": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate", "export"])
    parser.add_argument("--job", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--result")
    args = parser.parse_args(argv)
    if (args.command == "export") != bool(args.result):
        parser.error("--result is required only for export")
    try:
        if args.command == "export":
            result = export(args.job, args.manifest, args.result)
        else:
            result = {"valid": True, "result": build_result(args.job, args.manifest)}
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except BridgeError as exc:
        print(json.dumps({"valid": False, "error": {"code": exc.code, "detail": exc.detail}}))
        return 2
    except OSError as exc:
        print(json.dumps({"valid": False, "error": {"code": "IO_ERROR", "detail": str(exc)}}))
        return 2


if __name__ == "__main__":
    sys.exit(main())

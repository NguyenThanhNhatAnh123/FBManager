"""Synthetic UUIDs and inert text files for offline validation tests only."""
from pathlib import Path
import hashlib
import json
import uuid


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, doc):
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return sha(path.read_bytes())


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "https://example.invalid/page-manager-fixture/" + name))


def make_fixture(root, variant_count=None, trigger_mode="manual_web_run_idea"):
    root = Path(root)
    source = root / "source.png"
    source.write_bytes(b"INERT TEST TEXT: not an image or production artwork\n")
    job = {"schema_version": "1.0", "idea_id": uid("idea"), "source_path": str(source),
           "source_sha256": sha(source.read_bytes()), "campaign_url": "https://blueox179.com/campaign/zitat25",
           "campaign_verified": True, "caption_mode": "product_link_only", "trigger_mode": trigger_mode, "lease_fence": 42,
           "variants": [{"variant_id": uid("variant-" + str(n)), "reservation_id": uid("reservation-" + str(n))} for n in range(7 if variant_count is None else variant_count)]}
    if variant_count is not None:
        job['variant_count'] = variant_count
    manifest = {k: job[k] for k in ("schema_version", "idea_id", "source_sha256", "campaign_url", "caption_mode", "lease_fence")}
    manifest["variants"] = []
    if variant_count is not None:
        manifest['variant_count'] = variant_count
    for n, reserved in enumerate(job["variants"]):
        # Deliberately not playable media. No FFmpeg/ImageGen is used in tests.
        image, video = root / f"image-{n}.png", root / f"video-{n}.mp4"
        image.write_bytes(f"INERT UNIQUE PHOTO FIXTURE {n}\n".encode())
        video.write_bytes(f"INERT UNIQUE MP4 FIXTURE {n}\n".encode())
        caption = job['campaign_url']
        v = {**reserved, "image_path": str(image), "image_sha256": sha(image.read_bytes()),
             "video_path": str(video), "video_sha256": sha(video.read_bytes()), "caption": caption,
             "concept_id": f"fixture-concept-{n}", "qa": {}}
        binding = {"idea_id": job["idea_id"], **reserved, "source_sha256": job["source_sha256"],
                   "image_sha256": v["image_sha256"], "video_sha256": v["video_sha256"],
                   "campaign_url": job["campaign_url"], "lease_fence": job["lease_fence"]}
        kinds = {
            "visual": ("source_comparison_and_video_frames", {"original_artwork_compared": True, "complete_print_visible": True, "frames_reviewed": [0, 60, 120, 180, 239]}),
            "audio": ("listened_to_final_mix", {"listened_to_final_mix": True, "cuts_checked": True, "loudness_checked": True, "clipping_checked": True}),
            "technical": ("ffprobe_full_decode_bounds_and_motion", {"width": 1080, "height": 1920, "fps": 30, "frames": 240, "duration_seconds": 8.0, "video_codec": "h264", "audio_codec": "aac", "full_decode_passed": True, "all_frames_artwork_visible": True, "subpixel_zoom_verified": True, "minimum_margin_fraction": .04, "motion_rms_pixels": .05}),
            "distinct_variant": ("independent_image_and_scene_review", {"independent_source_photo": True, "new_scene_reviewed": True, "concept_id": v["concept_id"]}),
            "caption": ("exact_product_link_check", {"exact_product_link": True, "caption_sha256": sha(caption.encode()), "campaign_url": job["campaign_url"], "idea_id": job["idea_id"]}),
        }
        for kind, (method, checks) in kinds.items():
            p = root / f"qa-{n}-{kind}.json"
            proof = {"schema_version": "1.0", "kind": kind, "status": "passed", "review_method": method,
                     "reviewer": "SIMULATED TEST REVIEWER - never a real QA attestation",
                     "reviewed_at": "2026-01-01T00:00:00Z", "binding": binding, "checks": checks}
            v["qa"][kind] = {"path": str(p), "sha256": save(p, proof)}
            v[kind + "_verified"] = True
        manifest["variants"].append(v)
    jp, mp = root / "job.json", root / "manifest.json"
    save(jp, job)
    save(mp, manifest)
    return job, manifest, jp, mp

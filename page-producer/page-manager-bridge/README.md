# Page Manager video handoff bridge

Offline Python adapter for validating and exporting the frozen reserved video variants for each idea. New jobs carry `variant_count` from 1 through 14; historical jobs without it retain exactly seven. It has **no media-generation, rendering, network, upload, scheduling, authentication or publishing code**. Production and publication stay disabled. This directory is independent of the installed global skills.

## Commands

### Manual human-review receipts

New Page Manager runs may freeze `review_policy:manual_review_v1`. They use separate immutable `handoff/NN.json` receipts, committed after each local render; they do not fabricate the legacy full-set QA documents below. Import API:

```python
validated = bridge.manual_review_item(receipt_path, root=run_directory)
# Exactly: {"receipt": document, "receipt_path": absolute_path, "receipt_sha256": actual_sha256}
```

Each closed receipt carries `schema_version:"1.0"`, `record_type:"page_manager_manual_review_variant"`, `review_policy:"manual_review_v1"`, `status:"manual_review_pending"`, `ai_qa:false`, `visual_review:"pending"`, `distinct_review:"pending"`; run/idea/variant/reservation UUIDs, positive `lease_fence`, strict `variant_count` 1–14 and local `ordinal`; source/image/video paths and SHA256, `image_origin` generated_native or preflight_seed, `audio_source_sha256`, `concept_id`, `caption_mode:"product_link_only"`, exact product URL caption, campaign, technical and approved_audio objects. The code's `MANUAL_REVIEW_FIELDS` is the complete key set.

Campaign has exactly kind/url/source_url/shirt_color/evidence. Existing verified products use source_url null and shirt_color black or null. New products use the actual frozen template; this policy maps zitat25 to shirt_color black, ge2610 to null. Evidence has observed_at/artwork_matches/quote_matches/public_url. Caption equals the verified product URL.

Technical facts require 1080×1920, 30 fps, 240 frames, eight seconds ±0.06, H.264/AAC, full_decode_passed true, all_frames_artwork_visible true for the proposed print box, minimum_margin_fraction 0.04–0.5 and motion_registration `not_run`. No camera matcher or visual approval is claimed. `approved_audio` contains review_method `approved_mix_hash_match`, exact approval/actual-PCM/approved-PCM/source hashes in binding and checks `{"exact_pcm_match":true}`. This is reuse of a previously human-approved mix, not a listening claim.

The adapter only validates closed metadata, files and hashes. The trusted consumer must independently bind each item to the current run/fence, frozen allocation and source, probe/decode the MP4, check current human PCM approval and require the per-video human publication gate. A receipt may be imported before the remaining variants finish; it grants no visual, scene or publishing approval. Later item failure does not revoke or replay earlier immutable receipts. Legacy validate/export semantics below remain unchanged.

Use Python 3.12+ on Windows from the Via installation directory. This offline validator has no third-party dependencies. Activate the server's project Python environment first.

```powershell
$producerRoot = (Resolve-Path .\page-producer).Path
$bridgeScript = Join-Path $producerRoot 'page-manager-bridge\bridge.py'
python -X utf8 $bridgeScript validate --job (Join-Path $producerRoot 'JOB.json') --manifest (Join-Path $producerRoot 'MANIFEST.json')
python -X utf8 $bridgeScript export --job (Join-Path $producerRoot 'JOB.json') --manifest (Join-Path $producerRoot 'MANIFEST.json') --result (Join-Path $producerRoot 'RESULT.json')
```

These names are placeholders for a future job; the commands were not run against production media. `validate` writes nothing. `export` atomically writes a result only after validation. It does not create output directories. Success exits 0 even when variants are pending; consumers must inspect status. Invalid input exits 2 with structured JSON on stdout. An invalid invocation does not overwrite an existing result: do not mistake an older result for that invocation's output.

## Job contract, schema_version 1.0

All JSON objects use closed field sets; duplicate keys, NaN, malformed UUIDs and unknown fields are rejected. Canonical lowercase, non-nil RFC 4122 UUID strings are required. The adapter never generates production idea, variant or reservation IDs.

Required job fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | `"1.0"` |
| `idea_id` | UUID supplied by Via |
| `source_path`, `source_sha256` | Original design staged inside the producer project; SHA256 of actual bytes |
| `campaign_url` | Exact `https://blueox179.com/campaign/SLUG` product URL |
| `campaign_verified` | Must be the boolean `true`, representing the trusted Via uploader's prior verification |
| `caption_mode` | Must be `"product_link_only"` |
| `variants` | Exactly `variant_count` objects, each with Via's `variant_id` and `reservation_id` UUIDs |
| `variant_count` | New jobs: strict integer 1–14, frozen by Page Manager; omission means legacy seven only |
| `lease_fence` | Optional nonnegative integer; must match the manifest and every QA binding if supplied |
| `trigger_mode` | `"manual_web_run_idea"` or `"automatic_idea_batch"`; omission retains the legacy manual policy |

Variant UUIDs and reservation UUIDs must each be unique and exactly match Via's supplied mapping. Page Manager allocates the count before dispatch, for example 14 Pages and seven ideas give two variants per idea; three ideas give 5,5,4. The producer must not recalculate the plan. This offline adapter checks data consistency, not the existence or authorization of a live automatic batch. Via must bind trigger/count and its internal batch context to the persisted active run before accepting a result. The adapter neither contacts the shop nor independently authenticates the uploader's attestation.

The idea inbox is `media/ideas` under the Via installation; Via must stage the source into the producer project and confirm its SHA256 before handing it to this adapter. Direct paths outside the producer project are rejected, including the inbox. Source artwork may be PNG/JPG/JPEG/WEBP. WEBP support here means hashing and referencing its original bytes, with no decoding, conversion or artwork modification. Every path must be a local absolute path to an existing regular file inside the producer project, with no symlink, junction/reparse component or hardlink. Device/UNC paths, ADS, traversal and ambiguous Windows suffixes are rejected. Known auth/token/credential paths are rejected before opening. No credentials are needed.

Uploader template references are `https://blueox179.com/campaign/zitat25` and, for the black shirt template, `https://blueox179.com/campaign/ge2610`. They are **template references only**: the adapter never substitutes them for an idea's verified URL and never calls an uploader. It rejects 247smileworld, Wildy05, other domains, ports, credentials, tracking query strings and fragments.

## Producer manifest contract

Required top-level fields: `schema_version`, `idea_id`, `source_sha256`, `campaign_url`, `caption_mode:"product_link_only"`, `variants`. New jobs with `variant_count` require the same strict integer in the manifest. Optional `trigger_mode` and `lease_fence` must match the job. An empty or partial variant array is accepted and produces exactly the frozen count of output records in job order, with missing variants pending. Unknown/duplicate UUIDs or mismatched reservations are errors.

Each supplied variant requires `variant_id` and `reservation_id`. Additional fields:

| Fields | Rules |
| --- | --- |
| `video_path`, `video_sha256` | Both present together, or both missing/null; MP4, actual hash verified |
| `image_path`, `image_sha256` | Both present together, or both missing/null; PNG/JPG/JPEG/WEBP, actual hash verified |
| `audio_source_sha256` | When Page Manager supplies an audio assignment, the exact assigned source track hash; the audio QA binding must also include this field |
| `caption` | Exactly equal to the idea's assigned `campaign_url`, without prose, hashtags, prefix/suffix or whitespace |
| `concept_id` | Nonempty producer scene/concept identifier, unique within the frozen variant set |
| `visual_verified`, `audio_verified`, `technical_verified`, `distinct_variant_verified`, `caption_verified` | Explicit boolean claims; absent/false remains false |
| `qa` | Optional evidence references keyed by `visual`, `audio`, `technical`, `distinct_variant`, `caption` |

Duplicate final MP4 hashes, image hashes or concept IDs are errors. Captions may be identical because they must equal the same idea's product URL. An original source file cannot count as a newly generated image. Distinct hashes are necessary but do not prove visually distinct content: separate image/scene review evidence is also required, preventing a rename/re-encode from qualifying by hash alone.

The user's link-only caption policy overrides all earlier US prose/hashtag defaults. There is no caption-language field or English prose review. A caller importing `link.txt` may remove its terminal CR/LF before assembling JSON; this validator does not silently trim captions. A suffix, trailing text, extra URL or whitespace in the JSON caption is rejected.

## Evidence and pending rules

Each QA reference is `{"path":"ABSOLUTE_JSON_PATH","sha256":"64-lowercase-hex"}`. The evidence file follows the same path/link/hardlink restrictions and is limited to 2 MiB. Evidence is data; the adapter never executes commands from a report or visits any evidence URL.

Evidence document fields:

```json
{
  "schema_version": "1.0",
  "kind": "audio",
  "status": "pending",
  "review_method": "listened_to_final_mix",
  "reviewer": "producer-reviewer-id",
  "reviewed_at": "2026-01-01T00:00:00Z",
  "binding": {
    "idea_id": "VIA_IDEA_UUID",
    "variant_id": "VIA_VARIANT_UUID",
    "reservation_id": "VIA_RESERVATION_UUID",
    "source_sha256": "SOURCE_SHA256",
    "video_sha256": "VIDEO_SHA256",
    "image_sha256": "IMAGE_SHA256",
    "campaign_url": "https://blueox179.com/campaign/ACTUAL_IDEA_SLUG",
    "lease_fence": 42
  },
  "checks": {"listened_to_final_mix": false}
}
```

The placeholders illustrate fields; replace them with real Via IDs/hashes for a future job. Omit `binding.lease_fence` when absent from the job. Binding must match exactly. `reviewed_at` requires a timezone and cannot be in the future. A passed evidence document requires the exact checks below; pending/failed evidence never promotes a claim, regardless of its checks. Missing evidence also leaves the variant pending. A true flag without proof is never sufficient.

For a variant with `audio_source_sha256`, add the same hash to **only the audio evidence binding**; other evidence bindings retain the fields shown above. Page Manager compares this hash to the frozen assignment by ordinal. The only authorized music source for new runs is a direct regular file in `media/audio` under the Via installation matching the imported `source-library.json` filename/bytes/SHA-256 catalog. The historical origin in that manifest is provenance only; new producers never open Downloads or ABC audio copies. Legacy records remain unchanged.

New runs with `audio_mix_policy:"human_approved_exact_pcm_v1"` use audio `review_method:"approved_mix_hash_match"`. Add three lowercase 64-hex values to the audio binding: `approval_id`, `actual_pcm_sha256`, `approved_pcm_sha256`. Passed checks must be exactly `{"exact_pcm_match":true}` and the two PCM hashes must match. These claims say the final audio matches a previously human-approved reference, **not that this producer listened**. Decode using `ffmpeg -v error -i FILE -map 0:a:0 -vn -c:a pcm_s16le -f hash -hash sha256 -`, retaining native sample rate/channels and the full stream. Page Manager independently decodes the MP4, checks source hash, approval ID, render signature, rate/channels/duration against its trusted registry and frozen `approved_audio_mixes` snapshot, and rejects missing/revoked/mismatched approvals. This offline bridge cannot grant or authenticate human approval.

Historical runs without the new policy retain the `listened_to_final_mix` method and its honest listening checks. Source hashes, a successful decoder, or a pending reference file alone never substitute for either actual listening or a valid human-approved exact PCM match.

| Kind | `review_method` | Exact passed `checks` |
| --- | --- | --- |
| visual | `source_comparison_and_video_frames` | `original_artwork_compared:true`, `complete_print_visible:true`, `frames_reviewed:[0,60,120,180,239]` |
| audio | `listened_to_final_mix` | `listened_to_final_mix:true`, `cuts_checked:true`, `loudness_checked:true`, `clipping_checked:true` |
| audio (approved reuse) | `approved_mix_hash_match` | `exact_pcm_match:true`; audio binding additionally includes approval ID and actual/approved PCM hashes |
| technical | `ffprobe_full_decode_bounds_and_motion` | width 1080, height 1920, fps 30, frames 240, duration_seconds 8 ±0.06, video_codec h264, audio_codec aac; `full_decode_passed`, `all_frames_artwork_visible`, `subpixel_zoom_verified` all true; margin 0.04–0.5; motion RMS 0–<0.15 px |
| distinct_variant | `independent_image_and_scene_review` | `independent_source_photo:true`, `new_scene_reviewed:true`, exact `concept_id` |
| caption | `exact_product_link_check` | `exact_product_link:true`, `caption_sha256` of exact UTF-8 URL bytes, exact `campaign_url` and `idea_id` matching the binding |

Technical check field names are `width`, `height`, `fps`, `frames`, `duration_seconds`, `video_codec`, `audio_codec`, `full_decode_passed`, `all_frames_artwork_visible`, `subpixel_zoom_verified`, `minimum_margin_fraction`, `motion_rms_pixels`. No unused extra fields are accepted. Review tools must emit this normalized attestation after performing their real checks; old workflow reports are not silently interpreted as passed attestations.

Audio stream presence, decode or volume statistics do not count as listening. Leave audio false and omit audio proof or attach pending proof if nobody has heard the final mix. The adapter validates evidence structure, hashes and binding; it cannot authenticate reviewer honesty or independently establish actual listening or visual fidelity. Those remain producer QA responsibilities. Caption verification checks the exact product link and its idea/campaign/hash binding. It does not decode MP4s in this offline adapter: a passed technical report is an explicitly bound attestation of the workflow's prior decoder/motion checks.

## Output and resume

Result includes top-level `idea_id`, `source_sha256`, `campaign_url`, `caption_mode:"product_link_only"`, `job_sha256`, `job_fingerprint`, `manifest_sha256`, paths and optional fence. New jobs also emit `variant_count`; legacy jobs keep their previous result shape and fingerprint. `production_trigger_policy` reflects the job trigger. Each entry includes `variant_id`, `reservation_id`, `video_path`, `video_sha256`, `sha256` (same MP4 hash alias), `image_path`, `image_sha256`, `caption`, `captionUS` (legacy alias containing the same URL, not an English caption), `caption_mode:"product_link_only"`, `campaign_url`, `concept_id`, all QA booleans, evidence references and pending reasons.

`verified_for_handoff` requires complete media/caption/concept, all five explicit claims and passed bound evidence, plus the complete distinct set at the frozen count. Otherwise the state is `pending`. `ready_to_publish` is always false at the top level and for every variant; `production_enabled` and `publishing_enabled` are false. This is an export artifact, not posting authorization.

Export is protected by a Windows named mutex and uses fsync plus an atomic replace. Existing files are checked before overwriting. Identical exports are idempotent. A pending result can gain QA evidence later; media already recorded for a reserved variant cannot change or disappear under the same reservation. Changed source/link/reservation/fence requires a separate result for the newly authorized reservation. Validate before commit, then Via must recheck its active lease/fence and the exact expected job, manifest and result hashes. This bridge does not renew or confirm live leases. An expired job can pass offline validation; Via must reject it at consumption.

Production is dispatched by Page Manager after **Chạy idea / Run idea** or through a separately authorized automatic idea batch. A new file in `media/ideas` under the Via installation alone does not authorize this adapter to generate or publish anything. The adapter has no watcher, polling loop, intake, generator or batch controller; the trigger field cannot substitute for Via's real authorization, frozen batch context and lease checks. Results record the supplied trigger policy, defaulting to manual for legacy jobs.

Via retains ownership of uploader dispatch, performance-based priority, 14-Page round robin allocation, one video on one Page only, and one Reel/Page/day at 06:00 America/Los_Angeles. New ideas retain trial allocation. None of these services is invoked here.

## Fixtures and tests

Run `tests/run_tests.py` with the same Python interpreter. It writes `test-report.json`; it does not change historical production batches. `tests/fixture_factory.py` provides complete synthetic job/manifest/QA fixtures with test-only UUIDs and unique inert text bytes carrying `.png`/`.mp4` suffixes. These files are **not images or playable videos**, and simulated reviewer claims are never real QA. Temporary fixtures remain inside this bridge directory and are removed after each test.

Tests cover URL, caption, hash, UUID, reservation/fence, dedup, missing/pending QA, true claims without proof, unsafe paths, idempotent resume and CLI export. Real symlink/junction/hardlink fixture creation may be denied by the host sandbox; such cases are explicitly skipped, with separate deterministic metadata-guard tests. This is simulated validation coverage, not a live media or Page Manager integration test. A future authorized live test remains necessary before enabling production.

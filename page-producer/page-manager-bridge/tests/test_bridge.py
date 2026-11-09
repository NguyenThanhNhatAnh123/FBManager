from pathlib import Path
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))
import bridge
from fixture_factory import make_fixture, save, sha, uid


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bridge-test-", dir=HERE)
        self.root = Path(self.temp.name)
        self.assertTrue(self.root.resolve().is_relative_to(HERE))
        self.job, self.manifest, self.jp, self.mp = make_fixture(self.root)
        self.result = self.root / "result.json"

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to(HERE))
        self.temp.cleanup()

    def build(self):
        save(self.jp, self.job)
        save(self.mp, self.manifest)
        return bridge.build_result(str(self.jp), str(self.mp), self.root)

    def rejects(self, code):
        with self.assertRaises(bridge.BridgeError) as cm:
            self.build()
        self.assertEqual(cm.exception.code, code, str(cm.exception))

    def edit_proof(self, kind, fn, index=0):
        ref = self.manifest["variants"][index]["qa"][kind]
        path = Path(ref["path"])
        doc = json.loads(path.read_text())
        fn(doc)
        ref["sha256"] = save(path, doc)

    def test_complete_synthetic_handoff_does_not_enable_production(self):
        result = self.build()
        self.assertEqual(result["status"], "verified_for_handoff")
        self.assertEqual(len(result["variants"]), 7)
        self.assertTrue(all(v["ready_for_handoff"] for v in result["variants"]))
        self.assertFalse(result["production_enabled"])
        self.assertFalse(result["publishing_enabled"])
        self.assertFalse(result["ready_to_publish"])
        self.assertFalse(result["live_lease_verified"])
        self.assertEqual(result["caption_mode"], "product_link_only")
        self.assertEqual(result["production_trigger_policy"], "manual_web_run_idea")
        for expected, actual in zip(self.job["variants"], result["variants"]):
            self.assertEqual(actual["variant_id"], expected["variant_id"])
            self.assertEqual(actual["reservation_id"], expected["reservation_id"])
            self.assertEqual(actual["caption"], actual["captionUS"])
            self.assertEqual(actual["caption"], self.job["campaign_url"])

    def test_seven_identical_link_captions_allowed(self):
        result = self.build()
        self.assertEqual(len({v["caption"] for v in result["variants"]}), 1)
        self.assertEqual(result["status"], "verified_for_handoff")
        self.assertEqual(len({v["video_sha256"] for v in result["variants"]}), 7)
        self.assertEqual(len({v["image_sha256"] for v in result["variants"]}), 7)

    def test_missing_all_variants_exports_seven_pending_records(self):
        self.manifest["variants"] = []
        result = self.build()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(len(result["variants"]), 7)
        self.assertTrue(all(not v["audio_verified"] and not v["distinct_variant_verified"] for v in result["variants"]))

    def test_missing_audio_proof_is_pending_despite_true_claim(self):
        self.manifest["variants"][0]["qa"].pop("audio")
        v = self.build()["variants"][0]
        self.assertFalse(v["audio_verified"])
        self.assertEqual(v["state"], "pending")

    def test_pending_audio_not_listened_remains_false(self):
        self.edit_proof("audio", lambda d: d.update(status="pending", checks={"listened_to_final_mix": False}))
        v = self.build()["variants"][0]
        self.assertFalse(v["audio_verified"])
        self.assertEqual(v["state"], "pending")

    def test_explicit_false_claim_is_never_promoted(self):
        self.manifest["variants"][0]["visual_verified"] = False
        self.assertFalse(self.build()["variants"][0]["visual_verified"])

    def test_absent_claim_is_never_promoted(self):
        self.manifest["variants"][0].pop("technical_verified")
        self.assertFalse(self.build()["variants"][0]["technical_verified"])

    def test_proof_hash_tampered(self):
        Path(self.manifest["variants"][0]["qa"]["audio"]["path"]).write_text("{}")
        self.rejects("QA_HASH_MISMATCH")

    def test_proof_bound_to_other_variant(self):
        self.edit_proof("visual", lambda d: d["binding"].update(variant_id=uid("other")))
        self.rejects("QA_BINDING")

    def test_proof_bound_to_old_reservation(self):
        self.edit_proof("audio", lambda d: d["binding"].update(reservation_id=uid("old")))
        self.rejects("QA_BINDING")

    def test_fake_audio_measurement_does_not_count_as_listening(self):
        self.edit_proof("audio", lambda d: d.update(review_method="volume_statistics_only"))
        self.rejects("QA_METHOD")

    def test_incomplete_video_frame_review_rejected(self):
        self.edit_proof("visual", lambda d: d["checks"].update(frames_reviewed=[0, 239]))
        self.rejects("QA_CHECKS")

    def test_bad_technical_decode_rejected(self):
        self.edit_proof("technical", lambda d: d["checks"].update(full_decode_passed=False))
        self.rejects("QA_CHECKS")

    def test_bad_technical_dimensions_rejected(self):
        self.edit_proof("technical", lambda d: d["checks"].update(width=720))
        self.rejects("QA_CHECKS")

    def test_future_qa_rejected(self):
        self.edit_proof("visual", lambda d: d.update(reviewed_at="2999-01-01T00:00:00Z"))
        self.rejects("QA_TIMESTAMP")

    def test_campaign_domains_rejected(self):
        for url in ["https://247smileworld.com/campaign/x", "https://blueox179.com.evil.test/campaign/x", "https://blueox179.com@evil.test/campaign/x", "http://blueox179.com/campaign/x", "https://blueox179.com:443/campaign/x", "https://blueox179.com/campaign/x?next=evil"]:
            with self.subTest(url=url):
                self.job["campaign_url"] = url
                self.rejects("CAMPAIGN_URL")

    def test_unverified_campaign_rejected(self):
        self.job["campaign_verified"] = False
        self.rejects("CAMPAIGN_UNVERIFIED")

    def test_job_manifest_link_mismatch_rejected(self):
        self.manifest["campaign_url"] = "https://blueox179.com/campaign/ge2610"
        self.rejects("JOB_BINDING")

    def test_caption_wrong_link_or_prefix_rejected(self):
        for value in ["Wrong https://blueox179.com/campaign/zitat250", "Wrong https://blueox179.com/campaign/ge2610", "Wrong https://blueox179.com/campaign/zitat25 https://example.com"]:
            with self.subTest(caption=value):
                self.manifest["variants"][0]["caption"] = value
                self.rejects("CAPTION_URL")

    def test_caption_mode_rejected(self):
        self.job["caption_mode"] = "us_english_prose"
        self.rejects("CAPTION_MODE")

    def test_caption_suffix_trailing_text_and_whitespace_rejected(self):
        url = self.job["campaign_url"]
        for value in [url + "0", url + ".", url + " extra", url + " #shirt", "Get Yours Here: " + url, url + "\n", " " + url]:
            with self.subTest(caption=value):
                self.manifest["variants"][0]["caption"] = value
                self.rejects("CAPTION_URL")

    def test_caption_proof_required_even_when_url_matches(self):
        self.manifest["variants"][0]["qa"].pop("caption")
        v = self.build()["variants"][0]
        self.assertFalse(v["caption_verified"])
        self.assertEqual(v["state"], "pending")

    def test_caption_proof_idea_mismatch_rejected(self):
        self.edit_proof("caption", lambda d: d["checks"].update(idea_id=uid("wrong-caption-idea")))
        self.rejects("QA_CHECKS")

    def test_auto_inbox_trigger_rejected(self):
        self.job["trigger_mode"] = "automatic_inbox_watcher"
        self.rejects("TRIGGER_MODE")

    def test_caption_hash_in_qa_mismatch_rejected(self):
        self.edit_proof("caption", lambda d: d["checks"].update(caption_sha256="0" * 64))
        self.rejects("QA_CHECKS")

    def test_uuid_formats_rejected(self):
        for bad in ["not-uuid", "00000000-0000-0000-0000-000000000000", self.job["idea_id"].upper()]:
            with self.subTest(uuid=bad):
                self.job["idea_id"] = bad
                self.rejects("UUID")

    def test_seven_variants_required_in_job(self):
        self.job["variants"].pop()
        self.rejects("VARIANT_COUNT")

    def test_frozen_dynamic_counts_and_automatic_trigger_are_supported(self):
        for count in (1, 2, 5, 14):
            with self.subTest(count=count):
                self.job,self.manifest,self.jp,self.mp=make_fixture(self.root,variant_count=count,trigger_mode='automatic_idea_batch')
                result=self.build()
                self.assertEqual(result['variant_count'],count)
                self.assertEqual(len(result['variants']),count)
                self.assertEqual(result['status'],'verified_for_handoff')
                self.assertEqual(result['production_trigger_policy'],'automatic_idea_batch')
                self.assertFalse(result['production_enabled'] or result['publishing_enabled'] or result['ready_to_publish'])

    def test_count_is_strict_bounded_and_bound_to_job_and_manifest(self):
        for bad in (True,False,0,-1,15,2.0,'2',None):
            with self.subTest(count=bad):
                self.job['variant_count']=bad
                self.rejects('VARIANT_COUNT')
        self.job['variant_count']=7
        self.rejects('VARIANT_COUNT')  # New jobs require manifest count too.
        self.manifest['variant_count']=2
        self.rejects('VARIANT_COUNT')
        self.manifest['variant_count']=True
        self.rejects('VARIANT_COUNT')
        self.manifest['variant_count']=7
        self.assertEqual(self.build()['status'],'verified_for_handoff')

    def test_partial_dynamic_output_keeps_all_frozen_reservations_pending(self):
        self.job,self.manifest,self.jp,self.mp=make_fixture(self.root,variant_count=14)
        self.manifest['variants']=self.manifest['variants'][:2]
        result=self.build()
        self.assertEqual(len(result['variants']),14)
        self.assertEqual(result['status'],'pending')
        self.assertTrue(all(not v['ready_for_handoff'] for v in result['variants']))
        self.assertTrue(all('variant_set_incomplete' in v['pending_reasons'] for v in result['variants']))

    def test_legacy_shape_and_fingerprint_are_preserved_without_count(self):
        result=self.build()
        self.assertNotIn('variant_count',result)
        self.assertEqual(result['production_trigger_policy'],'manual_web_run_idea')
        normalized={**self.job,'variants':sorted(self.job['variants'],key=lambda v:v['variant_id'])}
        self.assertEqual(result['job_fingerprint'],sha(bridge.canonical(normalized)))
        self.manifest['variants']=[]
        self.assertTrue(all('seven_variant_set_incomplete' in v['pending_reasons'] for v in self.build()['variants']))

    def test_manifest_trigger_and_resume_cannot_change_frozen_authorization(self):
        self.job,self.manifest,self.jp,self.mp=make_fixture(self.root,variant_count=2,trigger_mode='automatic_idea_batch')
        previous=self.build()
        self.manifest['trigger_mode']='manual_web_run_idea'
        self.rejects('TRIGGER_MODE')
        self.manifest['trigger_mode']='automatic_idea_batch'
        self.assertEqual(self.build()['status'],'verified_for_handoff')
        self.job['trigger_mode']=self.manifest['trigger_mode']='manual_web_run_idea'
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.resume_check(previous,self.build())
        self.assertEqual(caught.exception.code,'RESUME_CONFLICT')

    def test_audio_assignment_hash_requires_separate_bound_audio_evidence(self):
        self.manifest['variants'][0]['audio_source_sha256']='a'*64
        self.rejects('QA_BINDING')
        self.edit_proof('audio',lambda proof:proof['binding'].update(audio_source_sha256='a'*64))
        previous=self.build()
        self.assertEqual(previous['variants'][0]['audio_source_sha256'],'a'*64)
        self.manifest['variants'][0]['audio_source_sha256']='b'*64
        self.edit_proof('audio',lambda proof:proof['binding'].update(audio_source_sha256='b'*64))
        with self.assertRaises(bridge.BridgeError) as caught:
            bridge.resume_check(previous,self.build())
        self.assertEqual(caught.exception.code,'RESUME_MEDIA_CHANGED')

    def approved_audio_proof(self):
        self.manifest['variants'][0]['audio_source_sha256']='a'*64
        def change(proof):
            proof['review_method']='approved_mix_hash_match'
            proof['binding'].update(audio_source_sha256='a'*64,approval_id='b'*64,
                actual_pcm_sha256='c'*64,approved_pcm_sha256='c'*64)
            proof['checks']={'exact_pcm_match':True}
        self.edit_proof('audio',change)

    def test_approved_mix_match_does_not_claim_new_listening(self):
        self.approved_audio_proof()
        result=self.build()
        proof=result['variants'][0]['qa_evidence']['audio']
        self.assertEqual(proof['review_method'],'approved_mix_hash_match')
        self.assertEqual(proof['approval_id'],'b'*64)
        self.assertEqual(proof['actual_pcm_sha256'],proof['approved_pcm_sha256'])
        self.assertTrue(result['variants'][0]['audio_verified'])

    def test_approved_mix_match_rejects_wrong_pcm_false_or_listening_checks(self):
        for change in ('mismatch','false','listening','approval_id','extra_binding'):
            with self.subTest(change=change):
                self.approved_audio_proof()
                def mutate(proof):
                    if change=='mismatch':proof['binding']['actual_pcm_sha256']='d'*64
                    elif change=='false':proof['checks']['exact_pcm_match']=False
                    elif change=='listening':proof['checks']={'listened_to_final_mix':True,'cuts_checked':True,'loudness_checked':True,'clipping_checked':True}
                    elif change=='approval_id':proof['binding']['approval_id']='not-a-hash'
                    else:proof['binding']['untrusted']='extra'
                self.edit_proof('audio',mutate)
                with self.assertRaises(bridge.BridgeError):self.build()

    def test_duplicate_job_uuid_rejected(self):
        self.job["variants"][1] = copy.deepcopy(self.job["variants"][0])
        self.rejects("DUPLICATE_VARIANT")

    def test_duplicate_reservation_uuid_rejected(self):
        self.job["variants"][1]["reservation_id"] = self.job["variants"][0]["reservation_id"]
        self.rejects("DUPLICATE_RESERVATION")

    def test_webp_original_source_hash_reference_supported(self):
        webp = self.root / "source.webp"
        shutil.copyfile(self.job["source_path"], webp)
        self.job["source_path"] = str(webp)
        before = webp.read_bytes()
        self.assertEqual(self.build()["status"], "verified_for_handoff")
        self.assertEqual(webp.read_bytes(), before)

    def test_webp_variant_image_hash_reference_supported(self):
        v = self.manifest["variants"][0]
        webp = self.root / "variant.webp"
        shutil.copyfile(v["image_path"], webp)
        v["image_path"] = str(webp)
        before = webp.read_bytes()
        out = self.build()["variants"][0]
        self.assertTrue(out["ready_for_handoff"])
        self.assertEqual(out["image_sha256"], sha(before))
        self.assertEqual(webp.read_bytes(), before)

    def test_duplicate_manifest_uuid_rejected(self):
        self.manifest["variants"][1] = copy.deepcopy(self.manifest["variants"][0])
        self.rejects("VARIANT_ID")

    def test_unknown_variant_rejected(self):
        self.manifest["variants"][0]["variant_id"] = uid("unknown")
        self.rejects("VARIANT_ID")

    def test_reservation_mismatch_rejected(self):
        self.manifest["variants"][0]["reservation_id"] = uid("wrong-reservation")
        self.rejects("RESERVATION")

    def test_fence_mismatch_rejected(self):
        self.manifest["lease_fence"] -= 1
        self.rejects("FENCE")

    def test_source_hash_mismatch_rejected(self):
        Path(self.job["source_path"]).write_bytes(b"changed")
        self.rejects("HASH_MISMATCH")

    def test_video_hash_mismatch_rejected(self):
        Path(self.manifest["variants"][0]["video_path"]).write_bytes(b"changed")
        self.rejects("HASH_MISMATCH")

    def test_image_hash_mismatch_rejected(self):
        self.manifest["variants"][0]["image_sha256"] = "0" * 64
        self.rejects("HASH_MISMATCH")

    def test_boolean_string_rejected(self):
        self.manifest["variants"][0]["audio_verified"] = "true"
        self.rejects("QA_BOOLEAN")

    def test_duplicate_video_content_rejected_even_with_valid_proofs(self):
        self.manifest["variants"] = self.manifest["variants"][:2]
        first, second = self.manifest["variants"]
        second["qa"] = {}
        Path(second["video_path"]).write_bytes(Path(first["video_path"]).read_bytes())
        second["video_sha256"] = first["video_sha256"]
        self.rejects("DUPLICATE_VIDEO_SHA256")

    def test_reencoded_video_cannot_reuse_same_image(self):
        self.manifest["variants"] = self.manifest["variants"][:2]
        first, second = self.manifest["variants"]
        second["qa"] = {}
        Path(second["image_path"]).write_bytes(Path(first["image_path"]).read_bytes())
        second["image_sha256"] = first["image_sha256"]
        self.rejects("DUPLICATE_IMAGE_SHA256")

    def test_original_artwork_not_a_new_variant(self):
        v = self.manifest["variants"][0]
        v["image_path"], v["image_sha256"] = self.job["source_path"], self.job["source_sha256"]
        self.rejects("SOURCE_REUSED_AS_VARIANT")

    def test_duplicate_concept_rejected(self):
        v = self.manifest["variants"][1]
        v["concept_id"] = self.manifest["variants"][0]["concept_id"]
        self.edit_proof("distinct_variant", lambda d: d["checks"].update(concept_id=v["concept_id"]), 1)
        self.rejects("DUPLICATE_CONCEPT_ID")

    def test_path_outside_subroot_rejected(self):
        self.job["source_path"] = str(HERE / "README.md")
        self.rejects("OUTSIDE_ROOT")

    def test_parent_traversal_rejected(self):
        self.job["source_path"] = str(self.root / ".." / self.root.name / "source.png")
        self.rejects("PATH")

    def test_sensitive_path_rejected_before_open(self):
        self.job["source_path"] = str(self.root / "auth" / "source.png")
        self.rejects("SENSITIVE_PATH")

    def test_directory_not_regular_rejected(self):
        d = self.root / "folder.png"
        d.mkdir()
        self.job["source_path"] = str(d)
        self.rejects("NOT_REGULAR")

    def test_hardlink_rejected(self):
        original = Path(self.job["source_path"])
        link = self.root / "hardlink.png"
        try:
            os.link(original, link)
        except OSError as exc:
            self.skipTest("OS denied hardlink creation: " + str(exc))
        self.job["source_path"] = str(link)
        self.rejects("HARDLINK")

    def test_symlink_rejected(self):
        link = self.root / "symlink.png"
        try:
            link.symlink_to(Path(self.job["source_path"]))
        except OSError as exc:
            self.skipTest("OS denied symlink creation: " + str(exc))
        self.job["source_path"] = str(link)
        with self.assertRaises(bridge.BridgeError):
            self.build()

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_junction_parent_rejected(self):
        target, link = self.root / "target", self.root / "junction"
        target.mkdir()
        shutil.copy2(self.job["source_path"], target / "source.png")
        # Fixed operands passed as separate subprocess arguments; no deletion shell.
        proc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if proc.returncode:
            self.skipTest("OS denied junction creation: " + proc.stderr)
        try:
            self.job["source_path"] = str(link / "source.png")
            self.rejects("REPARSE_POINT")
        finally:
            self.assertTrue(link.absolute().is_relative_to(self.root))
            os.rmdir(link)  # removes the junction entry only, never its target tree

    def test_duplicate_json_keys_rejected(self):
        self.jp.write_text('{"schema_version":"1.0","schema_version":"2.0"}')
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.build_result(str(self.jp), str(self.mp), self.root)
        self.assertEqual(cm.exception.code, "DUPLICATE_JSON_KEY")

    def test_nonfinite_json_rejected(self):
        self.jp.write_text('{"value":NaN}')
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.build_result(str(self.jp), str(self.mp), self.root)
        self.assertEqual(cm.exception.code, "SCHEMA")

    def test_export_and_idempotent_resume(self):
        a = bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        b = bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertTrue(a["changed"])
        self.assertFalse(b["changed"])
        self.assertEqual(a["result_sha256"], b["result_sha256"])

    def test_pending_resume_can_gain_audio_proof(self):
        original = copy.deepcopy(self.manifest)
        self.manifest["variants"][0]["qa"].pop("audio")
        save(self.mp, self.manifest)
        a = bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertEqual(a["pending_variants"], 1)
        save(self.mp, original)
        b = bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertEqual(b["verified_variants"], 7)

    def test_new_reservation_cannot_overwrite_old_result(self):
        bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        previous = self.result.read_bytes()
        self.job["variants"][0]["reservation_id"] = uid("replacement")
        self.manifest["variants"] = []
        save(self.jp, self.job)
        save(self.mp, self.manifest)
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertEqual(cm.exception.code, "RESUME_CONFLICT")
        self.assertEqual(self.result.read_bytes(), previous)

    def test_media_cannot_change_during_resume(self):
        bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        v = self.manifest["variants"][0]
        Path(v["video_path"]).write_bytes(b"new inert data")
        v["video_sha256"] = sha(Path(v["video_path"]).read_bytes())
        v["qa"] = {}
        save(self.mp, self.manifest)
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertEqual(cm.exception.code, "RESUME_MEDIA_CHANGED")

    def test_export_cannot_overwrite_job(self):
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.export(str(self.jp), str(self.mp), str(self.jp), self.root)
        self.assertEqual(cm.exception.code, "OUTPUT_INPUT_COLLISION")

    def test_existing_hardlinked_result_rejected(self):
        bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        try:
            os.link(self.result, self.root / "result-hardlink.json")
        except OSError as exc:
            self.skipTest("OS denied hardlink creation: " + str(exc))
        with self.assertRaises(bridge.BridgeError) as cm:
            bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        self.assertEqual(cm.exception.code, "HARDLINK")

    def mocked_metadata(self, target, **changes):
        original = Path.lstat
        def changed(path, *args, **kwargs):
            data = original(path, *args, **kwargs)
            if path != target:
                return data
            fields = {key: getattr(data, key) for key in ["st_mode", "st_nlink", "st_dev", "st_ino", "st_size", "st_mtime_ns"]}
            fields["st_file_attributes"] = getattr(data, "st_file_attributes", 0)
            fields.update(changes)
            return SimpleNamespace(**fields)
        return patch.object(Path, "lstat", changed)

    def test_metadata_hardlink_guard_without_creating_link(self):
        with self.mocked_metadata(Path(self.job["source_path"]), st_nlink=2):
            self.rejects("HARDLINK")

    def test_metadata_symlink_guard_without_creating_link(self):
        with self.mocked_metadata(Path(self.job["source_path"]), st_mode=stat.S_IFLNK | 0o777):
            self.rejects("NOT_REGULAR")

    def test_metadata_reparse_file_guard_without_creating_link(self):
        with self.mocked_metadata(Path(self.job["source_path"]), st_file_attributes=0x400):
            self.rejects("REPARSE_POINT")

    def test_metadata_junction_parent_guard_without_creating_link(self):
        with self.mocked_metadata(self.root, st_file_attributes=0x400):
            self.rejects("REPARSE_POINT")

    def test_metadata_hardlinked_result_guard_without_creating_link(self):
        bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
        with self.mocked_metadata(self.result, st_nlink=2):
            with self.assertRaises(bridge.BridgeError) as cm:
                bridge.export(str(self.jp), str(self.mp), str(self.result), self.root)
            self.assertEqual(cm.exception.code, "HARDLINK")

    def test_cli_validate_and_export_are_offline(self):
        for args in (["validate"], ["export", "--result", str(self.result)]):
            proc = subprocess.run([sys.executable, str(HERE / "bridge.py"), *args, "--job", str(self.jp), "--manifest", str(self.mp)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(json.loads(proc.stdout)["valid"])
        self.assertFalse(json.loads(self.result.read_text())["ready_to_publish"])


class ManualReviewBridgeTests(unittest.TestCase):
    """Inert synthetic files prove schema/bindings, never playable media or QA."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='manual-test-', dir=HERE)
        self.root = Path(self.temp.name)
        job, manifest, _, _ = make_fixture(self.root, variant_count=3)
        v = manifest['variants'][0]
        self.path = self.root / 'pending.json'
        self.doc = {'schema_version': '1.0', 'record_type': 'page_manager_manual_review_variant',
            'review_policy': 'manual_review_v1', 'status': 'manual_review_pending', 'ai_qa': False,
            'visual_review': 'pending', 'distinct_review': 'pending', 'run_id': uid('manual-run'),
            'idea_id': job['idea_id'], 'source_sha256': job['source_sha256'], 'source_path': job['source_path'],
            'lease_fence': 4, 'variant_count': 3, 'ordinal': 1,
            **{k: v[k] for k in ('variant_id', 'reservation_id', 'image_path', 'image_sha256', 'video_path', 'video_sha256', 'concept_id')},
            'image_origin': 'generated_native', 'audio_source_sha256': 'a' * 64,
            'caption_mode': 'product_link_only', 'caption': job['campaign_url'],
            'campaign': {'kind': 'existing', 'url': job['campaign_url'], 'source_url': None, 'shirt_color': None,
                'evidence': {'observed_at': '2026-01-01T00:00:00Z', 'artwork_matches': True,
                    'quote_matches': True, 'public_url': job['campaign_url']}},
            'technical': {'width': 1080, 'height': 1920, 'fps': 30, 'frames': 240, 'duration_seconds': 8.0,
                'video_codec': 'h264', 'audio_codec': 'aac', 'full_decode_passed': True,
                'all_frames_artwork_visible': True, 'minimum_margin_fraction': .04, 'motion_registration': 'not_run'},
            'approved_audio': {'review_method': 'approved_mix_hash_match', 'binding': {'approval_id': 'b' * 64,
                'actual_pcm_sha256': 'c' * 64, 'approved_pcm_sha256': 'c' * 64, 'audio_source_sha256': 'a' * 64},
                'checks': {'exact_pcm_match': True}}}

    def tearDown(self): self.temp.cleanup()

    def build(self):
        save(self.path, self.doc)
        return bridge.manual_review_item(str(self.path), root=self.root)

    def test_one_item_does_not_require_other_allocated_media_or_visual_approval(self):
        result = self.build()
        self.assertEqual(set(result), {'receipt', 'receipt_path', 'receipt_sha256'})
        self.assertEqual(result['receipt'], self.doc)
        self.assertEqual(result['receipt_sha256'], sha(self.path.read_bytes()))
        self.assertEqual(result['receipt']['visual_review'], 'pending')
        self.assertFalse(result['receipt']['ai_qa'])

    def test_existing_actual_black_shirt_does_not_invent_a_template(self):
        self.doc['campaign']['shirt_color'] = 'black'
        self.assertIsNone(self.build()['receipt']['campaign']['source_url'])

    def test_rejects_policy_boolean_geometry_caption_audio_and_extra_fields(self):
        mutations = [lambda d: d.update(ai_qa=True), lambda d: d.update(ai_qa=0),
            lambda d: d.update(visual_review='passed'), lambda d: d.update(distinct_review='passed'),
            lambda d: d.update(ordinal=True), lambda d: d.update(ordinal=4),
            lambda d: d.update(lease_fence=0), lambda d: d.update(review_policy=None),
            lambda d: d.update(caption=d['caption']+' #tag'), lambda d: d.update(qa={}),
            lambda d: d['technical'].update(motion_registration='passed'),
            lambda d: d['technical'].update(full_decode_passed=False),
            lambda d: d['technical'].update(minimum_margin_fraction=.039),
            lambda d: d['technical'].update(duration_seconds=8.2),
            lambda d: d['technical'].update(frames=239),
            lambda d: d['approved_audio']['binding'].update(actual_pcm_sha256='d'*64),
            lambda d: d['approved_audio']['binding'].update(audio_source_sha256='d'*64),
            lambda d: d['approved_audio']['checks'].update(exact_pcm_match=1),
            lambda d: d['campaign'].update(source_url=d['caption']),
            lambda d: d['campaign']['evidence'].update(quote_matches=False)]
        original = copy.deepcopy(self.doc)
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                self.doc = copy.deepcopy(original); mutate(self.doc)
                with self.assertRaises(bridge.BridgeError): self.build()

    def test_tampered_media_or_missing_source_cannot_validate(self):
        Path(self.doc['video_path']).write_bytes(b'TAMPERED SYNTHETIC FILE')
        with self.assertRaises(bridge.BridgeError): self.build()


if __name__ == "__main__":
    unittest.main(verbosity=2)

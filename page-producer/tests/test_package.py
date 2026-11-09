"""Read-only package checks and inert import-manifest boundary fixtures."""
import importlib.util
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
AUDIO_SCRIPT=ROOT/'.agents/skills/page-shirt-zoom-8s/scripts/audio_tools.py'
spec=importlib.util.spec_from_file_location('vendored_audio_tools',AUDIO_SCRIPT)
audio=importlib.util.module_from_spec(spec);spec.loader.exec_module(audio)


class ProjectPackageTests(unittest.TestCase):
    def test_vendored_current_files_match_recorded_sizes_and_hashes(self):
        manifest=json.loads((ROOT/'VENDORED-MANIFEST.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['path_base'],'via_project_root')
        for entry in manifest['files']+manifest['generated_documents']:
            path=ROOT.parent/entry['path']
            self.assertTrue(path.is_file(),entry['path'])
            self.assertFalse(path.is_symlink(),entry['path'])
            self.assertFalse(getattr(path,'is_junction',lambda:False)(),entry['path'])
            raw=path.read_bytes()
            self.assertEqual(len(raw),entry['bytes'],entry['path'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(),entry['sha256'],entry['path'])

    def test_imported_six_sources_match_manifest_and_are_physical(self):
        entries=audio.imported_sources()
        self.assertEqual(len(entries),6)
        for name,entry in entries.items():
            file=audio.source_audio(audio.SOURCE_DIRECTORY/name)
            self.assertEqual(file.stat().st_size,entry['bytes'])
            self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(),entry['sha256'])

    def test_both_photo_styles_resolve_shared_physical_references(self):
        paths=[]
        for name in ('realistic-shirt-photos-9x16','realistic-shirt-photos-closeup-9x16'):
            skill=ROOT/'.agents/skills'/name
            doc=json.loads((skill/'references/styles.json').read_text(encoding='utf-8'))
            for entry in doc['styles']:
                file=(skill/entry['reference_image']).resolve(strict=True)
                self.assertTrue(file.is_relative_to(ROOT/'assets/shirt-style-references'))
                self.assertFalse(file.is_symlink())
                paths.append(file)
        self.assertLess(len(set(paths)),len(paths))

    def test_imported_manifest_rejects_extra_changed_and_outside_files(self):
        with tempfile.TemporaryDirectory(prefix='package-boundary-',dir=ROOT/'tests') as temporary:
            base=Path(temporary);allowed=base/'allowed';allowed.mkdir();other=base/'other';other.mkdir()
            file=allowed/'fixture.mp3';raw=b'INERT TEST TEXT, NOT PLAYABLE MEDIA';file.write_bytes(raw)
            manifest=allowed/'source-library.json'
            def write():
                manifest.write_text(json.dumps({'schema_version':1,'source_directory':'PROVENANCE ONLY',
                    'tracks':[{'filename':file.name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}]}))
            write()
            with patch.object(audio,'SOURCE_DIRECTORY',allowed),patch.object(audio,'SOURCE_MANIFEST',manifest):
                self.assertEqual(audio.source_audio(file),file)
                copied=other/file.name;copied.write_bytes(raw)
                with self.assertRaises(ValueError):audio.source_audio(copied)
                extra=allowed/'unlisted.mp3';extra.write_bytes(raw)
                with self.assertRaises(ValueError):audio.imported_sources()
                extra.unlink();file.write_bytes(raw+b'changed')
                with self.assertRaises(ValueError):audio.source_audio(file)
                file.write_bytes(raw);manifest.write_text('[]')
                with self.assertRaises(ValueError):audio.imported_sources()


if __name__=='__main__':unittest.main()

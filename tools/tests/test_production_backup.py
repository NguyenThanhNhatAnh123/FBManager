"""Only temporary synthetic workspaces; never opens production data or processes."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'production_backup.py'
spec = importlib.util.spec_from_file_location('via_production_backup_tested', SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


class ProductionBackupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='via-backup-test-')
        self.workspace = Path(self.directory.name)
        self.output = self.workspace / 'private-backups'
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.close()
        self.directory.cleanup()

    def database(self, relative, *, wal=False):
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE synthetic(value TEXT)')
        db.execute('INSERT INTO synthetic VALUES(?)', ('synthetic-secret-never-log',))
        db.commit()
        self.connections.append(db)
        return path

    def test_all_known_databases_are_snapshotted_and_uncheckpointed_wal_is_included(self):
        for relative in backup.EXPECTED_DATABASES:
            self.database(relative, wal=True)
        snapshot, manifest = backup.create_backup(self.workspace, self.output)
        self.assertEqual(len(manifest['entries']), 4)
        self.assertEqual(manifest['missing_expected_databases'], [])
        self.assertFalse(manifest['full_backup'])
        self.assertEqual(manifest['kind'], 'databases-only')
        for entry in manifest['entries']:
            with closing(sqlite3.connect(snapshot / entry['path'])) as db:
                self.assertEqual(db.execute('SELECT value FROM synthetic').fetchone()[0], 'synthetic-secret-never-log')
        self.assertEqual(backup.verify_backup(snapshot, self.workspace)['total_bytes'], manifest['total_bytes'])

    def test_partial_database_inventory_is_explicit_and_files_are_not_silently_included(self):
        self.database(backup.EXPECTED_DATABASES[0])
        media = self.workspace / 'media' / 'video.mp4'
        media.parent.mkdir()
        media.write_bytes(b'synthetic video')
        snapshot, manifest = backup.create_backup(self.workspace, self.output)
        self.assertEqual(len(manifest['missing_expected_databases']), 3)
        self.assertFalse((snapshot / 'media').exists())
        self.assertTrue(any('DPAPI' in item for item in manifest['restore_notes']))

    def test_file_backup_requires_offline_attestation_before_creating_output(self):
        with self.assertRaisesRegex(backup.BackupError, 'requires_offline'):
            backup.create_backup(self.workspace, self.output, include_files=True)
        self.assertFalse(self.output.exists())

    def test_offline_copy_includes_profiles_media_run_state_and_key_without_sqlite_sidecars(self):
        self.database(backup.EXPECTED_DATABASES[0], wal=True)
        resources = ['chrome-manager/profiles/Profile_fixture/Default/History',
                     'media/videos/fixture.mp4', 'page-producer/page-manager-bridge/runs/fixture/state.json',
                     'chrome-manager/reel-assets/fixture.mp4', 'chrome-manager/page-assets/fixture.png',
                     'meta-page-manager/reel-assets/fixture.mp4',
                     'meta-page-manager/.runtime/audio-mixes/fixture/preview.mp4',
                     'meta-page-manager/.runtime/producer-capability-evidence.json',
                     'chrome-manager/.runtime/extensions/authenticator/fixture/manifest.json',
                     'via-auth/data/local-service.key']
        for relative in resources:
            path = self.workspace / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'synthetic resource')
        snapshot, manifest = backup.create_backup(self.workspace, self.output, include_files=True, offline=True)
        self.assertTrue(manifest['offline_attested'])
        self.assertFalse(manifest['full_backup'])
        for relative in resources:
            self.assertEqual((snapshot / relative).read_bytes(), b'synthetic resource')
        self.assertFalse(any(item['path'].endswith(('-wal', '-shm', '.lock')) for item in manifest['entries']))

    def test_active_manager_lock_blocks_offline_copy(self):
        from filelock import FileLock
        self.database(backup.EXPECTED_DATABASES[0])
        with FileLock(str(self.workspace / 'chrome-manager/data/manager.lock')):
            with self.assertRaisesRegex(backup.BackupError, 'manager_is_running'):
                backup.create_backup(self.workspace, self.output, include_files=True, offline=True)

    def test_secondary_backup_follows_registered_roots_and_honors_their_locks(self):
        from filelock import FileLock
        self.database(backup.EXPECTED_DATABASES[0])
        registered = 'workspaces/user-2/chrome-manager/data/manager.sqlite3'
        self.database(registered)
        self.database('workspaces/user-9/chrome-manager/data/manager.sqlite3')
        config = self.workspace/'deployment/server-config.json'
        config.parent.mkdir()
        config.write_text(json.dumps({'version':1,'bind_host':'127.0.0.1','port':8040,'manager_port':8000,
            'public_origin':None,'allow_http_loopback':False}),encoding='utf-8')
        registry = self.workspace/backup.REGISTRY
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps({'version':1,'workspaces':[
            {'user_id':1,'manager_port':8000,'root':'.'},
            {'user_id':2,'manager_port':8051,'root':'workspaces/user-2'}]}),encoding='utf-8')
        media = self.workspace/'workspaces/user-2/meta-page-manager/reel-assets/fixture.mp4'
        media.parent.mkdir(parents=True)
        media.write_bytes(b'operator fixture')
        with FileLock(str(self.workspace/'workspaces/user-2/chrome-manager/data/manager.lock')):
            with self.assertRaisesRegex(backup.BackupError,'manager_is_running'):
                backup.create_backup(self.workspace,self.output,include_files=True,offline=True)
        snapshot, manifest = backup.create_backup(self.workspace,self.output,include_files=True,offline=True)
        entries = {entry['path'] for entry in manifest['entries']}
        self.assertTrue({backup.REGISTRY,registered,media.relative_to(self.workspace).as_posix()} <= entries)
        self.assertFalse(any(name.startswith('workspaces/user-9/') for name in entries))
        self.assertEqual((snapshot/media.relative_to(self.workspace)).read_bytes(),b'operator fixture')

    def test_only_named_capability_proofs_are_copied_from_runtime(self):
        self.database(backup.EXPECTED_DATABASES[0])
        proof = self.workspace / 'page-producer/.runtime/production-proof/native.json'
        proof.parent.mkdir(parents=True)
        proof.write_text('{}', encoding='utf-8')
        unrelated = proof.parent / 'unrelated-cache.json'
        unrelated.write_text('{}', encoding='utf-8')
        config = self.workspace / 'meta-page-manager/.runtime/producer-capability-evidence.json'
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({'native_generation': {'path': str(proof)}}), encoding='utf-8')
        snapshot, manifest = backup.create_backup(self.workspace, self.output, include_files=True, offline=True)
        self.assertTrue((snapshot / proof.relative_to(self.workspace)).is_file())
        self.assertFalse((snapshot / unrelated.relative_to(self.workspace)).exists())
        self.assertEqual(manifest['unresolved_proof_references'], 0)

    def test_tamper_and_manifest_path_escape_are_rejected_without_opening_external_files(self):
        self.database(backup.EXPECTED_DATABASES[0])
        snapshot, manifest = backup.create_backup(self.workspace, self.output)
        target = snapshot / manifest['entries'][0]['path']
        original = target.read_bytes()
        target.write_bytes(b'x' + original[1:])
        with self.assertRaisesRegex(backup.BackupError, 'hash_mismatch'):
            backup.verify_backup(snapshot, self.workspace)
        target.write_bytes(original)
        manifest['entries'][0]['path'] = '../outside.sqlite3'
        (snapshot / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(backup.BackupError, 'invalid_manifest_path'):
            backup.verify_backup(snapshot, self.workspace)

    def test_size_budget_prevents_snapshot_and_source_overlap_is_rejected(self):
        self.database(backup.EXPECTED_DATABASES[0])
        with self.assertRaisesRegex(backup.BackupError, 'resource_limit'):
            backup.create_backup(self.workspace, self.output, max_bytes=1)
        with self.assertRaisesRegex(backup.BackupError, 'overlaps_source'):
            backup.create_backup(self.workspace, self.workspace / 'media/backups')
        with self.assertRaisesRegex(backup.BackupError, 'path_outside_workspace'):
            backup.create_backup(self.workspace, self.workspace.parent / 'outside')

    def test_hardlinked_source_is_rejected(self):
        source = self.database(backup.EXPECTED_DATABASES[0])
        source.parent.joinpath('linked.sqlite3').hardlink_to(source)
        with self.assertRaisesRegex(backup.BackupError, 'hardlinked_file'):
            backup.create_backup(self.workspace, self.output)

    def test_verify_only_cli_reports_metadata_without_database_content(self):
        self.database(backup.EXPECTED_DATABASES[0])
        snapshot, _ = backup.create_backup(self.workspace, self.output)
        before = {file: file.stat().st_mtime_ns for file in snapshot.rglob('*') if file.is_file()}
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(SCRIPT), '--workspace', str(self.workspace), '--verify-only', str(snapshot)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'verified': True, 'files': 1, 'full_backup': False})
        self.assertNotIn('synthetic-secret', result.stdout + result.stderr)
        self.assertEqual(before, {file: file.stat().st_mtime_ns for file in before})

    @unittest.skipUnless(os.name == 'nt' and sys.prefix != sys.base_prefix, 'Requires Windows venv launcher')
    def test_offline_cli_exempts_its_venv_launcher_but_blocks_other_workspace_processes(self):
        self.database(backup.EXPECTED_DATABASES[0])
        arguments = [sys.executable, '-I', '-B', str(SCRIPT), '--workspace', str(self.workspace),
                     '--output-root', str(self.output), '--offline', '--include-files']
        blocker = subprocess.Popen([sys._base_executable, '-I', '-B', '-c', 'import time; time.sleep(60)',
                                    str(self.workspace)], creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            rejected = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
            self.assertEqual(rejected.returncode, 1)
            self.assertEqual(json.loads(rejected.stderr), {'error': 'workspace_process_is_running'})
        finally:
            blocker.terminate()
            blocker.wait(timeout=10)
        accepted = subprocess.run(arguments, capture_output=True, text=True, timeout=30)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)['kind'], 'databases-and-listed-files')


if __name__ == '__main__':
    unittest.main()

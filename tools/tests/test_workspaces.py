"""Offline workspace preparation tests, using synthetic records only."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from deployment.install import InstallError
from deployment.start_server import core_environment
from deployment.workspaces import CORE_FILES, REGISTRY, load_workspaces, plan_workspaces, prepare_workspaces


class WorkspacePreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix='via-workspace-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'via'
        self.identity = self.root / 'via-auth/data/identity.sqlite3'
        self.identity.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.identity)) as db, db:
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY,role TEXT,active INTEGER)')
            db.executemany('INSERT INTO users VALUES(?,?,?)', [(1,'admin',1),(2,'operator',1),(3,'operator',1)])
        for name in CORE_FILES:
            self.write(name, 'fixture code: ' + name)

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding='utf-8')
        return path

    def prepare(self):
        return prepare_workspaces(self.root, 8000, 8040, protect=lambda _path: None)

    def test_first_prepare_and_code_update_keep_all_existing_business_files(self):
        forbidden = ('chrome-manager/.env','chrome-manager/data/manager.sqlite3','chrome-manager/profiles/secret',
                     'meta-page-manager/.env','meta-page-manager/data/page-manager.sqlite3',
                     'meta-page-manager/reel-assets/secret.mp4','media/videos/secret.mp4',
                     'via-auth/data/local-service.key','via-auth/data/identity.sqlite3')
        for path in forbidden[:-1]:
            self.write(path, 'primary only')
        identity_before = self.identity.read_bytes()
        bindings = plan_workspaces(self.root, 8000, 8040)
        self.assertFalse((self.root/'workspaces').exists())
        self.assertEqual(bindings, {1:{'manager_port':8000,'root':'.'},
            2:{'manager_port':8051,'root':'workspaces/user-2'},
            3:{'manager_port':8052,'root':'workspaces/user-3'}})
        self.assertEqual(self.prepare(), bindings)
        self.assertEqual(load_workspaces(self.root, 8000), bindings)
        for user_id in (2, 3):
            child = self.root/bindings[user_id]['root']
            for path in forbidden:
                self.assertFalse((child/path).exists(), path)
            for path in CORE_FILES:
                self.assertEqual((child/path).read_bytes(), (self.root/path).read_bytes())

        operator_data = self.write('workspaces/user-2/chrome-manager/data/manager.sqlite3','operator only')
        self.write('workspaces/user-2/chrome-manager/.env','operator config')
        registry_before = (self.root/REGISTRY).read_bytes()
        self.write('chrome-manager/backend/run.py','updated code')
        self.assertEqual(self.prepare(), bindings)
        self.assertEqual(operator_data.read_text(), 'operator only')
        self.assertEqual((self.root/'workspaces/user-2/chrome-manager/.env').read_text(), 'operator config')
        self.assertEqual((self.root/'workspaces/user-2/chrome-manager/backend/run.py').read_text(), 'updated code')
        self.assertEqual(self.identity.read_bytes(), identity_before)
        self.assertEqual((self.root/REGISTRY).read_bytes(), registry_before)

    def test_failed_code_copy_rolls_back_secondary_and_retains_original_binding(self):
        bindings = self.prepare()
        targets = [self.root / bindings[uid]['root'] / 'chrome-manager/backend/run.py' for uid in (2,3)]
        before = [target.read_bytes() for target in targets]
        registry_before = (self.root/REGISTRY).read_bytes()
        self.write('chrome-manager/backend/run.py','updated code')
        replace = Path.replace

        def fail_second(source, target):
            if 'new' in source.parts and target == targets[1]:
                raise OSError('synthetic interruption')
            return replace(source, target)

        with patch.object(Path,'replace',fail_second), self.assertRaises(OSError):
            self.prepare()
        self.assertEqual([target.read_bytes() for target in targets], before)
        self.assertEqual((self.root/REGISTRY).read_bytes(), registry_before)

    def test_registry_rejects_aliases_and_never_adopts_unregistered_business_data(self):
        saved = self.write('workspaces/user-2/chrome-manager/data/manager.sqlite3','unknown owner')
        with self.assertRaisesRegex(InstallError,'unregistered_workspace_directory_exists'):
            self.prepare()
        self.assertEqual(saved.read_text(), 'unknown owner')
        primary = {'user_id':1,'manager_port':8000,'root':'.'}
        secondary = {'user_id':2,'manager_port':8051,'root':'workspaces/user-2'}
        for update in ({'root':'../other'},{'root':'workspaces/user-1'},{'user_id':True},{'manager_port':8000}):
            self.write(REGISTRY,json.dumps({'version':1,'workspaces':[primary,secondary|update]}))
            with self.subTest(update=update), self.assertRaises(InstallError):
                load_workspaces(self.root,8000)

    def test_secondary_core_uses_its_own_storage_and_omits_parent_provider_secrets(self):
        environment = {'Path':'machine','CM_DATA_DIR':'primary-custom','PM_PODSACH_TOOL_ROOT':'primary',
                       'VIA_WORKSPACE_USER_ID':'99','OPENAI_API_KEY':'synthetic secret'}
        primary = core_environment(self.root,1,{'manager_port':8000,'root':'.'},environment)
        secondary = core_environment(self.root,2,{'manager_port':8051,'root':'workspaces/user-2'},environment)
        self.assertEqual(primary['CM_DATA_DIR'],'primary-custom')
        self.assertEqual(primary['VIA_WORKSPACE_PRIMARY'],'1')
        self.assertEqual(secondary['Path'],'machine')
        self.assertEqual(secondary['CM_DATA_DIR'],'data')
        self.assertEqual(secondary['VIA_WORKSPACE_USER_ID'],'2')
        self.assertEqual(secondary['VIA_WORKSPACE_PRIMARY'],'0')
        self.assertEqual(secondary['CM_PORT'],'8051')
        self.assertNotIn('OPENAI_API_KEY',secondary)
        self.assertNotIn('PM_PODSACH_TOOL_ROOT',secondary)


if __name__=='__main__':
    unittest.main()

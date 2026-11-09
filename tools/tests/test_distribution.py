"""Offline release/install tests. All writes stay inside TemporaryDirectory."""
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from deployment.package_policy import REQUIRED_FILES,source_file_allowed
from deployment.install import InstallError,install,lock_existing,plan,public_origin,verify_source
from tools.build_distribution import build_server,make_manifest,selected_files
from deployment import start_server


def put(root,relative,value=b'code-v1\n'):
    path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value);return path


def release(root):
    for relative in REQUIRED_FILES:put(root,relative)
    put(root,'via-auth/manage_users.py')
    put(root,'deployment/preflight.py')
    refresh(root)
    return root


def refresh(root):
    manifest,_=make_manifest(root,'1.1.0')
    put(root,'distribution-manifest.json',(json.dumps(manifest)+'\n').encode())


def runtime_runner(argv,step,timeout=0):
    if step=='venv_create':
        venv=Path(argv[-1]);put(venv,'Scripts/python.exe',b'new runtime');put(venv,'new-marker')
    if step=='fresh_user_bootstrap':
        root=Path(argv[-2]).parents[1]
        put(root,'via-auth/data/identity.sqlite3',b'fresh three-account database')
    return ''


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='via-release-test-')
        self.base=Path(self.temporary.name);self.source=release(self.base/'source');self.target=self.base/'installed'

    def tearDown(self):self.temporary.cleanup()

    def test_deterministic_archive_and_private_artifact_exclusion(self):
        secret=b'SHOULD_NEVER_ENTER_RELEASE_8e2a'
        forbidden=('chrome-manager/.env','chrome-manager/data/manager.sqlite3','chrome-manager/profiles/one/Cookies',
            'via-auth/data/initial-users.txt','via-mcp/data/operations.sqlite3','private-backups/users.txt',
            'deployment/server-config.json','deployment/initial-users.txt','deployment/installed-release.json',
            'page-producer/.runtime/pilot.json','page-producer/page-manager-bridge/runs/run.json',
            'page-producer/.agents/skills/unrelated/SKILL.md','LanguageCheck/main.py','old-release.zip',
            'chrome-manager/frontend/node_modules/pkg/index.js','chrome-manager/backend/tests/test.py',
            'via-plugin/client-config.json','.install-backups/release/anything.py')
        for name in forbidden:put(self.source,name,secret)
        a=self.base/'a.zip';b=self.base/'b.zip'
        first=build_server(self.source,a);second=build_server(self.source,b)
        self.assertEqual(first['sha256'],second['sha256'])
        with zipfile.ZipFile(a) as archive:
            names=archive.namelist()
            self.assertTrue(all(name.startswith('via-server/') for name in names))
            self.assertFalse(any(('via-server/'+name) in names for name in forbidden))
            self.assertFalse(any(secret in archive.read(name) for name in names))
        self.assertEqual(first['files'],len(selected_files(self.source)))

    def test_check_only_inventories_without_creating_target_or_resetting_auth(self):
        result=plan(self.source,self.target)
        self.assertFalse(self.target.exists());self.assertTrue(result['fresh_accounts_only'])
        put(self.target,'via-auth/data/identity.sqlite3',b'existing identities')
        result=plan(self.source,self.target,self.target)
        self.assertFalse(result['fresh_accounts_only']);self.assertFalse(result['automatic_data_import'])
        self.assertEqual((self.target/'via-auth/data/identity.sqlite3').read_bytes(),b'existing identities')

    def test_fresh_install_and_update_preserve_data_and_configuration(self):
        first=install(self.source,self.target,python=self.base/'external-python.exe',runner=runtime_runner,process_probe=lambda _:0)
        self.assertTrue(first['installed']);self.assertFalse(first['services_started'])
        self.assertTrue((self.target/'via-auth/data/identity.sqlite3').is_file())
        private=('chrome-manager/data/manager.sqlite3','chrome-manager/profiles/one/Cookies',
            'meta-page-manager/data/page-manager.sqlite3','via-mcp/data/operations.sqlite3',
            'via-auth/data/local-service.key','chrome-manager/.env','meta-page-manager/.env',
            'meta-page-manager/.runtime/audio-mixes/approved.json','chrome-manager/.runtime/extensions/authenticator/file.json',
            'page-producer/page-manager-bridge/runs/a/checkpoint.json','media/audio/source-library.json',
            'deployment/server-config.json','via-auth/data/initial-users.txt')
        for name in private:put(self.target,name,('private-'+name).encode())
        before={name:(self.target/name).read_bytes() for name in private}
        identity=(self.target/'via-auth/data/identity.sqlite3').read_bytes()
        put(self.source,'chrome-manager/backend/app/main.py',b'code-v2');refresh(self.source)
        steps=[]
        def runner(argv,step,timeout):steps.append(step);return runtime_runner(argv,step,timeout)
        result=install(self.source,self.target,self.target,origin='https://provided.example',python=self.base/'external-python.exe',runner=runner,process_probe=lambda _:0)
        self.assertEqual((self.target/'chrome-manager/backend/app/main.py').read_bytes(),b'code-v2')
        self.assertEqual(before,{name:(self.target/name).read_bytes() for name in private})
        self.assertEqual(identity,(self.target/'via-auth/data/identity.sqlite3').read_bytes())
        self.assertNotIn('fresh_user_bootstrap',steps)
        self.assertTrue((Path(result['code_backup'])/'previous-venv/Scripts/python.exe').is_file())
        self.assertEqual((Path(result['code_backup'])/'code/chrome-manager/backend/app/main.py').read_bytes(),b'code-v1\n')

    def test_busy_process_or_application_lock_refuses_before_code_change(self):
        marker=put(self.target,'chrome-manager/backend/app/main.py',b'old')
        with self.assertRaisesRegex(InstallError,'workspace_processes_are_running'):
            install(self.source,self.target,self.target,runtime=False,process_probe=lambda _:1)
        self.assertEqual(marker.read_bytes(),b'old')
        lock=put(self.target,'chrome-manager/data/manager.lock',b'\0')
        with lock_existing(lock):
            with self.assertRaisesRegex(InstallError,'installation_is_running'):
                install(self.source,self.target,self.target,runtime=False,process_probe=lambda _:0)
        self.assertEqual(marker.read_bytes(),b'old')

    def test_manifest_tampering_and_mutable_paths_refused(self):
        manifest=json.loads((self.source/'distribution-manifest.json').read_text())
        manifest['entries'].append({'path':'via-auth/data/identity.sqlite3','bytes':1,'sha256':'0'*64})
        put(self.source,'distribution-manifest.json',json.dumps(manifest).encode())
        with self.assertRaisesRegex(InstallError,'release_path_not_allowed'):plan(self.source,self.target)
        self.assertFalse(self.target.exists())
        refresh(self.source);put(self.source,'via-mcp/server.py',b'tampered')
        with self.assertRaisesRegex(InstallError,'release_hash_mismatch'):plan(self.source,self.target)

    def test_runtime_and_code_rollback_after_bootstrap_failure(self):
        put(self.target,'chrome-manager/backend/app/main.py',b'old-code')
        put(self.target,'chrome-manager/.venv/old-marker',b'old-runtime')
        put(self.target,'meta-page-manager/data/page-manager.sqlite3',b'old-schedules')
        def broken(argv,step,timeout):
            if step=='fresh_user_bootstrap':raise InstallError('bootstrap_test_failure')
            return runtime_runner(argv,step,timeout)
        with self.assertRaisesRegex(InstallError,'bootstrap_test_failure'):
            install(self.source,self.target,self.target,python=self.base/'external-python.exe',runner=broken,process_probe=lambda _:0)
        self.assertEqual((self.target/'chrome-manager/backend/app/main.py').read_bytes(),b'old-code')
        self.assertEqual((self.target/'chrome-manager/.venv/old-marker').read_bytes(),b'old-runtime')
        self.assertFalse((self.target/'chrome-manager/.venv/new-marker').exists())
        self.assertEqual((self.target/'meta-page-manager/data/page-manager.sqlite3').read_bytes(),b'old-schedules')

    def test_cross_root_migration_and_nonempty_target_require_explicit_handling(self):
        put(self.target,'existing-user-data',b'private')
        with self.assertRaisesRegex(InstallError,'nonempty_target'):plan(self.source,self.target)
        with self.assertRaisesRegex(InstallError,'cross_root_data_migration'):plan(self.source,self.target,self.base/'other')
        self.assertEqual((self.target/'existing-user-data').read_bytes(),b'private')

    def test_hardlinked_or_redirected_sources_and_destinations_refused(self):
        original=self.source/'via-mcp/server.py';alias=self.base/'alias.py'
        try:os.link(original,alias)
        except OSError:self.skipTest('hardlinks not supported by this temporary filesystem')
        with self.assertRaises(ValueError):selected_files(self.source)
        alias.unlink();verify_source(self.source)
        target=put(self.target,'chrome-manager/backend/app/main.py')
        os.link(target,alias)
        with self.assertRaisesRegex(InstallError,'hardlinked_file_rejected'):plan(self.source,self.target,self.target)

    def test_package_paths_reject_noncanonical_and_windows_names(self):
        for name in ('deployment/../via-auth/store.py','deployment\\install.py','/deployment/install.py',
                     'deployment//install.py','deployment/./install.py','C:/Via/deployment/install.py'):
            with self.subTest(name=name):self.assertFalse(source_file_allowed(name))

    def test_windows_aliases_cannot_escape_package_or_installation_root(self):
        prefix='page-producer/.agents/skills/page-shirt-zoom-8s/'
        aliases=('.. ','.. .','data.','data ','file.txt.','file.txt ', 'CON','con.txt','NUL.json','LPT9.log',
                 'COM1.py','COM\u00b9.txt','LPT\u00b2.txt','CON .txt','COM1 .py','CONIN$','a\tb','a\x85b','a<bad','a?bad')
        for component in aliases:
            with self.subTest(component=component):
                self.assertFalse(source_file_allowed(prefix+component+'/outside.txt'))
                with self.assertRaisesRegex(InstallError,'unsafe_windows_path_component'):
                    plan(self.source,self.target/component/'escape')
        traversal=prefix+'.. /.. /.. /.. /.. /outside.txt'
        self.assertFalse(source_file_allowed(traversal))
        self.assertFalse(self.target.exists())
        for component in ('.. ','folder.','NUL'):
            with self.subTest(source_component=component),self.assertRaises(ValueError):
                build_server(self.source,self.base/component/'release.zip')
        if os.name=='nt':
            for root in ('\\\\server\\share.\\outside','\\\\server.\\share\\outside','\\\\.\\C:\\outside'):
                with self.subTest(unc_root=root),self.assertRaisesRegex(InstallError,'unsafe_windows_path_component'):
                    plan(self.source,Path(root))

    def test_https_origin_validation_and_no_runtime_config_in_allowlist(self):
        self.assertEqual(public_origin('https://example.test:8443'),'https://example.test:8443')
        self.assertEqual(public_origin('https://EXAMPLE.test:443'),'https://example.test')
        self.assertEqual(public_origin('https://BÜCHER.example:443'),'https://xn--bcher-kva.example')
        self.assertEqual(public_origin('https://[2001:DB8::1]:443'),'https://[2001:db8::1]')
        self.assertIsNone(public_origin(None))
        for value in ('http://example.test','https://user:pass@example.test','https://example.test/','https://example.test/path',
                      'https://example.test?secret=1','https://example.test\\bad'):
            with self.subTest(value=value),self.assertRaises(InstallError):public_origin(value)
        for value in ('../via-auth/data/identity.sqlite3','via-auth/data/identity.sqlite3','deployment/server-config.json','deployment/initial-users.txt'):
            self.assertFalse(source_file_allowed(value))

    def test_launcher_configuration_keeps_core_and_gateway_on_loopback(self):
        config={'version':1,'bind_host':'127.0.0.1','port':8040,'manager_port':8000,'public_origin':None,'allow_http_loopback':False}
        put(self.target,'deployment/server-config.json',json.dumps(config).encode())
        self.assertEqual(start_server.read_config(self.target),config)
        for fields in ({'bind_host':'0.0.0.0'},{'manager_port':8040},{'port':True},{'unexpected':'secret'}):
            put(self.target,'deployment/server-config.json',json.dumps({**config,**fields}).encode())
            with self.subTest(fields=fields),self.assertRaises(InstallError):start_server.read_config(self.target)

    def test_launcher_health_response_requires_matching_process_and_config(self):
        config={'port':8040,'public_origin':None}
        arguments=['python.exe','-B',str(self.target/'via-mcp/remote-server.py'),'--config',str(self.target/'deployment/server-config.json')]
        process=SimpleNamespace(exe=lambda:'python.exe',cmdline=lambda:arguments,cwd=lambda:str(self.target))
        psutil=SimpleNamespace(CONN_LISTEN='listen',net_connections=lambda **_: [SimpleNamespace(status='listen',pid=123,laddr=SimpleNamespace(ip='127.0.0.1',port=8040))],Process=lambda _:process)
        with patch.dict(sys.modules,{'psutil':psutil}),patch.object(start_server,'gateway_ready',return_value=True):
            self.assertTrue(start_server.gateway_owned(self.target,config))
            arguments[-1]=str(self.base/'other/deployment/server-config.json')
            self.assertFalse(start_server.gateway_owned(self.target,config))
            arguments[:]=['python.exe','-c','arbitrary-code']
            self.assertFalse(start_server.gateway_owned(self.target,config))

    def test_launcher_shutdown_signals_only_owned_children_without_force_kill(self):
        signals=[]
        process=SimpleNamespace(poll=lambda:None,send_signal=signals.append,wait=lambda **_:None)
        self.assertFalse(start_server.graceful_stop([process],timeout=0))
        self.assertEqual(len(signals),1)

    @unittest.skipUnless(os.name=='nt','Windows PowerShell wrapper only')
    def test_powershell_51_wrapper_executes_check_only_in_staging(self):
        # Exercise native argument passing, not only the PowerShell parser. PS5
        # drops quotes embedded in a Python -c JSON literal; the probe has none.
        for name in ('install-server.ps1','install.py','package_policy.py'):
            put(self.source,'deployment/'+name,(ROOT/'deployment'/name).read_bytes())
        refresh(self.source)
        result=subprocess.run(['powershell.exe','-NoProfile','-File',str(self.source/'deployment/install-server.ps1'),
            '-InstallRoot',str(self.target),'-PythonPath',sys.executable,'-PublicOrigin','https://EXAMPLE.test:443','-CheckOnly'],
            capture_output=True,text=True,timeout=30,check=False)
        self.assertEqual(result.returncode,0,result.stderr)
        value=json.loads(result.stdout.strip().splitlines()[-1])
        self.assertTrue(value['check_only']);self.assertFalse(value['writes_performed'])
        self.assertEqual(value['public_origin'],'https://example.test')
        self.assertFalse(self.target.exists())
        result=subprocess.run(['powershell.exe','-NoProfile','-File',str(self.source/'deployment/install-server.ps1'),
            '-InstallRoot',str(self.target/'.. '/'outside'),'-PythonPath',sys.executable,'-CheckOnly'],
            capture_output=True,text=True,timeout=30,check=False)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('unsafe_windows_path_component',result.stderr)
        self.assertFalse(self.target.exists())


if __name__=='__main__':unittest.main()

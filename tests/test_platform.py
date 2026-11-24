import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

installer = load('release_installer', ROOT / 'install.py')
launcher = load('release_launcher', ROOT / 'launch.py')

class PlatformTests(unittest.TestCase):
    def test_new_paths_unicode_metacharacters_are_preserved_as_argv(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve() / '中文 space % ! & ^'
            app, vault, workspace = installer.preflight(base / 'app', base / 'vault', base / 'workspace')
            command = installer.pip_command(installer.runtime_python(app, 'nt'), app / 'requirements-lock.txt', base)
            self.assertEqual(command[0], str(app / '.venv/Scripts/python.exe'))
            self.assertEqual(command[-1], str(base.resolve()))
            self.assertIn('--isolated', command)
            self.assertIn('--require-hashes', command)
            self.assertIn('--no-index', command)
            self.assertNotIn('--index-url', command)
            self.assertFalse(base.exists())

    def test_existing_overlap_and_links_are_refused_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            existing = base / 'existing'; existing.mkdir()
            sentinel = existing / 'private.txt'; sentinel.write_text('preserve')
            for paths in [(existing, base/'v', base/'w'), (base/'a', base/'a/v', base/'w'), (base/'a', base/'a', base/'w')]:
                with self.assertRaises(ValueError): installer.preflight(*paths)
            link = base / 'linked'; link.symlink_to(existing, target_is_directory=True)
            with self.assertRaises(ValueError): installer.preflight(link/'app', base/'v', base/'w')
            self.assertEqual(sentinel.read_text(), 'preserve')
            self.assertFalse((base/'v').exists())

    def test_no_network_authorization_stops_before_target_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            with patch.object(installer.sys, 'version_info', (3,12)):
                with self.assertRaisesRegex(ValueError, 'allow-network'):
                    installer.install(base/'a', base/'v', base/'w')
            self.assertEqual(list(base.iterdir()), [])

    def test_dependency_failure_records_exact_app_and_preserves_other_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve(); source = base/'source'; source.mkdir()
            for name in ('core','eju-study-coach-codex'): (source/name).mkdir()
            for name in ('install.py','launch.py','INSTALL.md'): (source/name).write_text('placeholder')
            (source/'requirements-lock.txt').write_text('x==1 --hash=sha256:abcd')
            (source/'manifest.json').write_text('{}')
            with patch.object(installer, 'HERE', source), patch.object(installer.sys, 'version_info', (3,12)), patch.object(installer, 'verify_release_manifest', return_value=True), patch.object(installer.venv.EnvBuilder, 'create'), patch.object(installer.subprocess, 'run', side_effect=subprocess.CalledProcessError(1,['pip'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(base/'app', base/'vault', base/'workspace', allow_network=True)
            state = json.loads((base/'app/installation.json').read_text())
            self.assertEqual(state['stage'], 'failed')
            self.assertEqual(state['failed_stage'], 'installing_dependencies')
            self.assertEqual(state['created_paths'], [str(base/'app')])
            self.assertFalse((base/'vault').exists()); self.assertFalse((base/'workspace').exists())

    def test_completed_beta_upgrade_sources_and_unknown_versions(self):
        class PayloadReached(Exception): pass
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp).resolve();source=base/'source';source.mkdir()
            (source/'manifest.json').write_text('{}')
            (source/'requirements-lock.txt').write_text('x==1 --hash=sha256:abcd')
            old_app=base/'old-app';old_app.mkdir();old_vault=base/'old-vault';old_vault.mkdir()
            (old_vault/'learning.sqlite').write_bytes(b'preserved synthetic vault')
            for version in ('1.0.0b1','1.0.0b2','1.0.0b3','1.0.0b4','1.0.0b5','1.0.0b6','1.0.0b7','1.0.0b8','1.0.0b9','1.0.0b10','1.0.0b11','1.0.0b12','unknown'):
                with self.subTest(version=version):
                    state={'stage':'complete','version':version,'app':str(old_app),'vault':str(old_vault),'workspace':str(base/'old-workspace')}
                    (old_app/'installation.json').write_text(json.dumps(state))
                    app=base/(version+'-new-app');vault=base/(version+'-new-vault');workspace=base/(version+'-new-workspace')
                    with patch.object(installer,'HERE',source),patch.object(installer,'verify_release_manifest',return_value=True),patch.object(installer,'copy_payload',side_effect=PayloadReached):
                        if version in ('1.0.0b12','unknown'):
                            with self.assertRaisesRegex(ValueError,'completed Beta'):installer.install(app,vault,workspace,allow_network=True,upgrade_from=old_app)
                            self.assertFalse(app.exists())
                        else:
                            with self.assertRaises(PayloadReached):installer.install(app,vault,workspace,allow_network=True,upgrade_from=old_app)
                            self.assertEqual(json.loads((app/'installation.json').read_text())['upgrade_from'],str(old_app))
                    self.assertFalse(vault.exists());self.assertFalse(workspace.exists())
                    self.assertEqual((old_vault/'learning.sqlite').read_bytes(),b'preserved synthetic vault')
                    self.assertEqual(json.loads((old_app/'installation.json').read_text()),state)

    def test_restore_and_official_import_are_argv_and_do_not_switch_vault(self):
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp).resolve()
            state={'stage':'complete', 'vault':str(app/'private')}
            (app/'installation.json').write_text(json.dumps(state))
            with patch.object(launcher,'HERE',app), patch.object(launcher.subprocess,'call',return_value=0) as call:
                self.assertEqual(launcher.main(['restore','backup space','new vault']),0)
                self.assertEqual(call.call_args.args[0][-3:],['restore','backup space','new vault'])
                source=app/'中文 % ! & ^'
                self.assertEqual(launcher.main(['import-official',str(source)]),0)
                self.assertEqual(call.call_args.args[0], [str(launcher.runtime_python(app)),str(app/'import_official.py'),'--source',str(source)])
            self.assertEqual(json.loads((app/'installation.json').read_text()),state)

    def test_manifest_tamper_rejected_before_any_installation(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            source=Path(temp).resolve();file=source/'payload.txt';file.write_text('release')
            rows={'payload.txt':{'size':file.stat().st_size,'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}}
            (source/'manifest.json').write_text(json.dumps({'format':'eju-release-manifest','version':installer.VERSION,'files':rows}))
            self.assertTrue(installer.verify_release_manifest(source))
            file.write_text('tamper!')
            with self.assertRaises(ValueError):installer.verify_release_manifest(source)

    def test_windows_cmd_wrappers_have_no_forwarded_shell_arguments(self):
        # Contract only: does not execute Windows CMD on this host.
        wrapper = (ROOT/'安装.cmd').read_text()
        self.assertIn('DisableDelayedExpansion', wrapper)
        self.assertIn('"%~dp0install.py"', wrapper)
        self.assertNotIn('%*', wrapper)
        self.assertNotIn('--allow-network\npy', wrapper)

    def test_launch_uses_private_interpreter_and_argv_without_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            app = Path(temp).resolve()
            vault = app/'中文 % ! & ^ vault'
            (app/'installation.json').write_text(json.dumps({'stage':'complete', 'vault':str(vault)}))
            with patch.object(launcher, 'HERE', app), patch.object(launcher, 'runtime_python', return_value=app/'.venv/Scripts/python.exe'), patch.object(launcher.subprocess, 'call', return_value=0) as call:
                self.assertEqual(launcher.main(['doctor']), 0)
                self.assertEqual(call.call_args.args[0], [str(app/'.venv/Scripts/python.exe'),str(app/'core/run.py'),'--vault',str(vault),'doctor'])
                self.assertEqual(call.call_args.kwargs,{})

if __name__ == '__main__': unittest.main()

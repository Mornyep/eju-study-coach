import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

CORE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CORE/'src'))
from eju_core.store import Core
from eju_core.errors import CoreError
spec=importlib.util.spec_from_file_location('eju_configure',CORE/'configure_codex.py')
configure_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(configure_module)


class ConfigureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name).resolve();self.vault=self.base/'private vault'
        Core.initialize(self.vault)

    def test_new_entry_has_working_absolute_config_and_no_seeded_progress(self):
        entry=self.base/'学习入口'
        result=configure_module.configure(entry,self.vault,Path(sys.executable))
        self.assertFalse(result['desktop_loaded']);self.assertFalse(result['global_config_changed'])
        config=tomllib.loads((entry/'.codex/config.toml').read_text())['mcp_servers']['eju_core']
        self.assertTrue(Path(config['command']).is_file())
        self.assertTrue((entry/'.agents/skills/eju-study-coach-codex/SKILL.md').is_file())
        command=[config['command'],*config['args'][:-1],'status']
        status=json.loads(subprocess.check_output(command,text=True,cwd=entry))
        self.assertEqual(status['revision'],0);self.assertEqual(status['event_count'],0)
        self.assertEqual(status['profile']['subjects'],[])

    def test_existing_entry_and_nested_vault_refused(self):
        entry=self.base/'entry';entry.mkdir();(entry/'AGENTS.md').write_text('user')
        with self.assertRaises(CoreError) as error:configure_module.configure(entry,self.vault,Path(sys.executable))
        self.assertEqual(error.exception.code,'destination_exists')
        self.assertEqual((entry/'AGENTS.md').read_text(),'user')
        with self.assertRaises(CoreError) as error:configure_module.configure(self.vault/'nested',self.vault,Path(sys.executable))
        self.assertEqual(error.exception.code,'private_location')
        self.assertFalse((self.vault/'nested').exists())

    def test_windows_launcher_uses_fixed_helper_and_docling_is_explicit(self):
        entry=self.base/'中文 space % ! & ^'
        result=configure_module.configure(entry,self.vault,Path(sys.executable))
        self.assertFalse(result['docling_configured'])
        wrapper=(entry/'打开工作台.cmd').read_text()
        self.assertIn('DisableDelayedExpansion',wrapper)
        self.assertIn('"%~dp0open_workbench.py"',wrapper)
        self.assertNotIn('%*',wrapper)
        helper=(entry/'open_workbench.py').read_text()
        self.assertIn(repr(str(self.vault.resolve())),helper)
        self.assertNotIn('shell=True',helper)
        with self.assertRaises(CoreError) as error:
            configure_module.configure(self.base/'docling-entry',self.vault,Path(sys.executable),docling=True)
        self.assertEqual(error.exception.code,'docling_unavailable')
        self.assertFalse((self.base/'docling-entry').exists())

    def test_cli_export_backup_restore_are_readable_and_refuse_overwrite(self):
        def run(*args):
            return subprocess.run([sys.executable,str(CORE/'run.py'),*map(str,args)],capture_output=True,text=True)
        output=self.base/'export.json';self.assertEqual(run('--vault',self.vault,'export',output).returncode,0)
        original=output.read_bytes();self.assertNotEqual(run('--vault',self.vault,'export',output).returncode,0)
        self.assertEqual(output.read_bytes(),original)
        backup=self.base/'backup';self.assertEqual(run('--vault',self.vault,'backup',backup).returncode,0)
        restored=self.base/'restored';self.assertEqual(run('restore',backup,restored).returncode,0)
        result=run('--vault',restored,'doctor');self.assertEqual(result.returncode,0)
        self.assertTrue(json.loads(result.stdout)['ok'])
        self.assertNotEqual(run('restore',backup,restored).returncode,0)

if __name__=='__main__':unittest.main()

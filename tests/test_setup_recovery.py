import hashlib, json, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from test_platform import installer

class SetupRecoveryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name).resolve()
  self.source=self.base/'source';self.source.mkdir()
  for name in ('core','eju-study-coach-codex'):(self.source/name).mkdir()
  for name in ('install.py','launch.py','INSTALL.md','core/run.py','eju-study-coach-codex/SKILL.md'):(self.source/name).write_text('payload')
  (self.source/'requirements-lock.txt').write_text('x==1 --hash=sha256:abcd')
  (self.source/'manifest.json').write_text('{}')
  self.app=self.base/'app';self.vault=self.base/'vault';self.workspace=self.base/'workspace'
  for obj,attr,value in [(installer,'HERE',self.source),(installer.sys,'version_info',(3,12))]:
   p=patch.object(obj,attr,value);p.start();self.addCleanup(p.stop)
  p=patch.object(installer,'verify_release_manifest',return_value=True);p.start();self.addCleanup(p.stop)
 def fail_dependencies(self):
  with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',side_effect=subprocess.CalledProcessError(1,['pip'])):
   with self.assertRaises(subprocess.CalledProcessError):installer.install(self.app,self.vault,self.workspace,allow_network=True)
 def test_retry_keeps_source_identity_and_completes_without_overwriting(self):
  self.fail_dependencies();before=(self.app/'core/run.py').read_bytes();old=json.loads((self.app/'installation.json').read_text())
  def run(argv,**kw):
   if 'init' in argv:self.vault.mkdir()
   if '--workspace' in argv:self.workspace.mkdir()
  with patch.object(installer.venv.EnvBuilder,'create'),patch.object(installer.subprocess,'run',side_effect=run):
   result=installer.install(self.app,self.base/'ignored',self.base/'ignored2',allow_network=True,resume=True)
  self.assertEqual(result['stage'],'complete');self.assertEqual(old['installation_id'],result['installation_id']);self.assertEqual((self.app/'core/run.py').read_bytes(),before)
  self.assertTrue(self.vault.exists());self.assertTrue(self.workspace.exists())
 def test_retry_refuses_changed_payload_source_and_existing_vault(self):
  self.fail_dependencies();original=(self.app/'core/run.py').read_bytes();(self.app/'core/run.py').write_text('user work')
  with self.assertRaisesRegex(ValueError,'differs'):installer.install(self.app,self.vault,self.workspace,allow_network=True,resume=True)
  self.assertEqual((self.app/'core/run.py').read_text(),'user work');(self.app/'core/run.py').write_bytes(original)
  (self.source/'manifest.json').write_text('{"new":true}')
  with self.assertRaisesRegex(ValueError,'exact release'):installer.install(self.app,self.vault,self.workspace,allow_network=True,resume=True)
  (self.source/'manifest.json').write_text('{}');self.vault.mkdir();(self.vault/'private').write_text('retain')
  with self.assertRaisesRegex(ValueError,'existing vault'):installer.install(self.app,self.vault,self.workspace,allow_network=True,resume=True)
  self.assertEqual((self.vault/'private').read_text(),'retain')
 def test_retry_never_runs_linked_interpreter_or_late_failure(self):
  self.fail_dependencies();venv=self.app/'.venv';venv.mkdir();(venv/'python').symlink_to(self.source/'launch.py')
  with patch.object(installer.subprocess,'run') as run:
   with self.assertRaises(ValueError):installer.install(self.app,self.vault,self.workspace,allow_network=True,resume=True)
   run.assert_not_called()
  state=json.loads((self.app/'installation.json').read_text());state['failed_stage']='configuring_workspace';installer.state_write(self.app,state)
  with self.assertRaisesRegex(ValueError,'before vault creation'):installer.install(self.app,self.vault,self.workspace,allow_network=True,resume=True)
 def test_wizard_requires_explicit_network_choice_and_final_local_action(self):
  with patch.object(installer.sys.stdin,'isatty',return_value=True),patch('builtins.input',side_effect=['1','','','','1','cancel']),patch('builtins.print'):
   self.assertIsNone(installer.wizard())
  with patch.object(installer.sys.stdin,'isatty',return_value=True),patch('builtins.input',side_effect=['1','','','','2',str(self.source),'INSTALL']),patch('builtins.print'):
   options=installer.wizard();self.assertFalse(options['allow_network']);self.assertEqual(options['wheelhouse'],self.source)
  self.assertFalse(self.app.exists())
 def test_upgrade_cannot_write_inside_old_learning_workspace(self):
  old=self.base/'old';old.mkdir();private=self.base/'old-vault';private.mkdir();(private/'learning.sqlite').write_bytes(b'placeholder')
  workspace=self.base/'old-workspace';workspace.mkdir();(workspace/'user-note.txt').write_text('keep')
  (old/'installation.json').write_text(json.dumps({'stage':'complete','app':str(old),'vault':str(private),'workspace':str(workspace),'version':'1.0.0b1'}))
  with patch.object(installer.subprocess,'run') as run:
   with self.assertRaisesRegex(ValueError,'separate from the old'):installer.install(workspace/'new-app',self.vault,self.workspace,allow_network=True,upgrade_from=old)
   run.assert_not_called()
  self.assertEqual((workspace/'user-note.txt').read_text(),'keep');self.assertFalse((workspace/'new-app').exists())
 def test_upgrade_rejects_unfinished_source_without_creating_target(self):
  old=self.base/'old';old.mkdir();(old/'installation.json').write_text(json.dumps({'stage':'failed','app':str(old),'version':'1.0.0b1'}))
  with self.assertRaisesRegex(ValueError,'completed Beta'):installer.install(self.app,self.vault,self.workspace,allow_network=True,upgrade_from=old)
  self.assertFalse(self.app.exists())

import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from eju_core.errors import CoreError
from eju_core.store import Core
from eju_core import sealed_backup as sealed

PASSWORD = 'synthetic-test-only-passphrase'


class EncryptedBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.vault = self.base / 'vault'
        Core.initialize(self.vault)
        self.core = Core(self.vault)
        r = self.core.resume_learning(); self.session = r['session_id']
        self.core.set_profile(self.session, 0, 'profile-encrypted', {'subjects': ['integrated']})

    def seal(self, name='private.ejubak'):
        file = self.base / name
        result = sealed.seal_backup(self.core, file, PASSWORD)
        self.assertTrue(result['encrypted'])
        return file

    def test_roundtrip_preserves_events_and_attachment_but_removes_sessions(self):
        content = b'PRIVATE synthetic attachment marker 8734921'
        (self.vault/'inbox'/'notes.txt').write_bytes(content)
        self.core.import_material(self.session, 1, 'import-encrypted', 'notes.txt')
        before = self.core.export_json()
        file = self.seal()
        self.assertNotIn(content, file.read_bytes())
        if os.name != 'nt': self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
        target = self.base/'restored'
        result = sealed.restore_sealed(file, target, PASSWORD)
        self.assertTrue(result['restored']); self.assertFalse(result['current_vault_changed'])
        after = Core(target).export_json()
        for key in ('events','profile','revision','vault_id','artifacts'):
            self.assertEqual(before[key], after[key])
            self.assertEqual(before[key], self.core.export_json()[key])
        with sqlite3.connect(target/'learning.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0], 0)
        self.assertFalse(list(self.base.glob('.eju-*')))

    def test_wrong_password_and_modified_data_never_create_target_parent(self):
        file = self.seal(); original = file.read_bytes()
        cases = [('wrong-password', original, PASSWORD+'wrong'),
                 ('modified-header', original[:10]+bytes([original[10]^1])+original[11:], PASSWORD),
                 ('modified-body', original[:-1]+bytes([original[-1]^1]), PASSWORD),
                 ('truncated', original[:-20], PASSWORD)]
        for name, data, password in cases:
            with self.subTest(name=name):
                source = self.base/(name+'.ejubak'); source.write_bytes(data)
                target = self.base/name/'restore'
                with self.assertRaises(CoreError): sealed.restore_sealed(source, target, password)
                self.assertFalse(target.parent.exists())
        self.assertEqual(len(self.core.export_json()['events']), 1)

    def test_existing_destinations_and_links_preserved(self):
        file = self.seal(); before = file.read_bytes()
        with self.assertRaises(CoreError): sealed.seal_backup(self.core, file, PASSWORD)
        self.assertEqual(before, file.read_bytes())
        target = self.base/'existing'; target.mkdir(); (target/'sentinel').write_text('keep')
        with self.assertRaises(CoreError): sealed.restore_sealed(file, target, PASSWORD)
        self.assertEqual((target/'sentinel').read_text(),'keep')
        link = self.base/'linked'; link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(CoreError): sealed.seal_backup(self.core, link/'x', PASSWORD)
        self.assertFalse((target/'x').exists())

    def test_random_salt_and_nonce_and_format_cost_not_controlled_by_input(self):
        first = self.seal('a.ejubak').read_bytes(); second = self.seal('b.ejubak').read_bytes()
        self.assertNotEqual(first, second)
        self.assertNotEqual(first[8:24], second[8:24]); self.assertNotEqual(first[24:36], second[24:36])
        invalid = self.base/'unsupported'; invalid.write_bytes(b'EJUBAK\x00\x03'+first[8:])
        with patch.object(sealed, 'derive') as derive:
            with self.assertRaises(CoreError): sealed.restore_sealed(invalid, self.base/'no', PASSWORD)
            derive.assert_not_called()

    def encrypted_archive(self, rows):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name,content in rows:z.writestr(name,content)
        salt,nonce=secrets.token_bytes(16),secrets.token_bytes(12)
        header=sealed.MAGIC+salt+nonce
        return header+sealed.AESGCM(sealed.derive(PASSWORD,salt)).encrypt(nonce,stream.getvalue(),header)

    def test_authenticated_malicious_archive_is_still_rejected(self):
        rows=[('manifest.json','{}'),('learning.sqlite','synthetic invalid database'),('../escape','bad')]
        source=self.base/'malicious.ejubak';source.write_bytes(self.encrypted_archive(rows))
        with self.assertRaises(CoreError):sealed.restore_sealed(source,self.base/'nested'/'restore',PASSWORD)
        self.assertFalse((self.base/'nested').exists());self.assertFalse((self.base/'escape').exists())

    def test_size_limit_preserves_source_and_removes_plaintext_temporary(self):
        with patch.object(sealed,'MAX_PLAINTEXT',4*1024*1024):
            with self.assertRaises(CoreError) as error:sealed.seal_backup(self.core,self.base/'too-large.ejubak',PASSWORD)
        self.assertEqual(error.exception.code,'backup_too_large')
        self.assertFalse((self.base/'too-large.ejubak').exists());self.assertFalse(list(self.base.glob('.eju-*')))
        self.assertEqual(len(self.core.export_json()['events']),1)

    def test_interactive_prompt_does_not_accept_pipe_and_mismatch_cancels(self):
        with patch('sys.stdin.isatty',return_value=False),patch('getpass.getpass') as getpass:
            with self.assertRaises(CoreError):sealed.prompt_passphrase()
            getpass.assert_not_called()
        import getpass
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=getpass.GetPassWarning('echo unavailable')):
            with self.assertRaises(CoreError) as error:sealed.prompt_passphrase()
            self.assertEqual(error.exception.code,'hidden_input_unavailable')
        with patch('sys.stdin.isatty',return_value=True),patch('getpass.getpass',side_effect=[PASSWORD,PASSWORD+'wrong']):
            with self.assertRaises(CoreError) as error:sealed.prompt_passphrase(confirm=True)
            self.assertEqual(error.exception.code,'passphrase_mismatch')


if __name__ == '__main__':unittest.main()

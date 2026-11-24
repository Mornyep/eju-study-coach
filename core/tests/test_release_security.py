import http.client,json,os,stat,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from eju_core.store import Core
from eju_core.errors import CoreError
from eju_core.safe_files import portable_relative,is_link,private_text_create
from eju_core.http_server import make_server

class PortableFileTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
 def test_platform_ambiguous_paths_rejected_on_every_host(self):
  for value in ['../a','a/../b','/absolute','C:/secret',r'C:\secret',r'..\a','a:stream','a//b','a/./b','CON.txt','originals/NUL','COM1.pdf','a.','a ','a\x00b']:
   with self.subTest(value=value),self.assertRaises(CoreError):portable_relative(value)
  self.assertEqual(portable_relative('originals/中文 名称.pdf'),Path('originals')/'中文 名称.pdf')
 def test_reparse_attribute_detected_without_windows(self):
  class Info:st_mode=stat.S_IFDIR;st_file_attributes=0x400
  with patch.object(Path,'lstat',return_value=Info()):self.assertTrue(is_link(self.root/'junction'))
 def test_private_create_is_exclusive_and_restricts_before_write(self):
  path=self.root/'secret'
  with private_text_create(path) as f:
   if os.name!='nt':self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
   f.write('private')
  with self.assertRaises(FileExistsError):
   with private_text_create(path):pass
  self.assertEqual(path.read_text(),'private')
 def test_database_hardlink_and_sidecar_link_refused(self):
  vault=self.root/'vault';Core.initialize(vault);core=Core(vault)
  os.link(vault/'learning.sqlite',self.root/'alias')
  with self.assertRaises(CoreError):Core(vault)
  (self.root/'alias').unlink()
  target=self.root/'outside';target.write_text('retain')
  (vault/'learning.sqlite-journal').symlink_to(target)
  with self.assertRaises(CoreError):core.resume_learning()
  self.assertEqual(target.read_text(),'retain')
 def test_restore_windows_stream_path_rejected_before_copy(self):
  vault=self.root/'vault';Core.initialize(vault);core=Core(vault);backup=self.root/'backup';core.backup(backup)
  path=backup/'manifest.json';m=json.loads(path.read_text());m['files'].append({'path':'originals/x:payload','size':0,'sha256':'bad'});path.write_text(json.dumps(m))
  with self.assertRaises(CoreError):Core.restore(backup,self.root/'restored')
  self.assertFalse((self.root/'restored').exists());self.assertEqual(core.export_json()['events'],[])

class HeaderTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);v=Path(self.tmp.name)/'vault';Core.initialize(v);self.core=Core(v)
  self.server,self.token=make_server(self.core);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.addCleanup(self.close)
  self.host='127.0.0.1:'+str(self.server.server_port);self.origin='http://'+self.host
 def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
 def request(self,extra=(),target='/api/call'):
  connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
  connection.putrequest('POST',target,skip_host=True)
  headers=[('Host',self.host),('Origin',self.origin),('Authorization','Bearer '+self.token),('Content-Type','application/json')]
  body=json.dumps({'method':'resume_learning','params':{}}).encode()
  for key,value in [*headers,*extra,('Content-Length',str(len(body)))]:connection.putheader(key,value)
  connection.endheaders(body);response=connection.getresponse();data=response.read();connection.close();return response.status,data
 def test_duplicate_sensitive_headers_blocked(self):
  for name,value,status in [('Authorization','Bearer '+self.token,401),('Origin',self.origin,403),('Host',self.host,403),('Content-Type','application/json',415),('Content-Length','1',400)]:
   with self.subTest(name=name):self.assertEqual(self.request([(name,value)])[0],status)
  self.assertEqual(self.request()[0],200)
 def test_absolute_target_and_non_ascii_auth_do_not_crash(self):
  self.assertEqual(self.request(target='http://'+self.host+'/api/call')[0],400)
  self.assertEqual(self.request([('Authorization','Bearer é')])[0],401)
  self.assertEqual(self.request()[0],200)
 def test_transfer_encoding_fails_without_events(self):
  self.assertEqual(self.request([('Transfer-Encoding','chunked')])[0],400)
  self.assertEqual(self.core.export_json()['events'],[])

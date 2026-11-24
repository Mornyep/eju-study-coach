import sys,json,tempfile,unittest,threading,http.client,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from eju_core.store import Core
from eju_core.errors import CoreError
from eju_core.http_server import make_server

class WorkbenchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.vault=self.root/'vault';Core.initialize(self.vault);self.core=Core(self.vault)
  r=self.core.resume_learning();self.sid=r['session_id'];self.rev=0
  r=self.core.present_learning_task(self.sid,self.rev,'task1','STEM-M1-CP-F','先独立判断条件集合。');self.rev=r['revision']
 def action(self,kind,payload=None,rid=None):
  r=self.core.workbench_action(self.sid,self.rev,rid or 'r'+str(self.rev),'task1',kind,payload);self.rev=r['revision'];return r
 def test_draft_reopen_submit_exact_and_idempotent(self):
  self.action('show');self.action('draft',dict(answer='2/3',reason='3张中2张',guessed=False))
  core2=Core(self.vault);s=core2.resume_learning();task=core2.read_workbench(s['session_id'])['task'];self.assertEqual(task['answer'],'2/3');self.assertEqual(s['event_count'],3)
  payload=dict(answer='2/3',reason='3张中2张',guessed=False);r=self.action('submit',payload,'submit1')
  self.assertEqual(r['result'],'correct');self.assertTrue(self.core.workbench_action(self.sid,2,'submit1','task1','submit',payload)['replayed'])
  events=self.core.export_json()['events'];self.assertEqual(len([e for e in events if e['type']=='attempt']),1)
  self.assertEqual(self.core.read_workbench(self.sid)['task']['status'],'completed')
  self.assertEqual(self.core.resume_learning()['evidence'][0]['stage'],0)
 def test_hints_cannot_reset_on_reload_and_pause_preserves(self):
  self.action('show');self.action('hint');self.action('pause')
  r=self.core.resume_learning();self.sid=r['session_id'];self.action('resume');self.action('show');self.action('submit',dict(answer='2/3',reason='理由',guessed=False))
  e=next(e for e in self.core.export_json()['events'] if e['type']=='attempt');self.assertEqual(e['hint'],'H1');self.assertTrue(e['seenBefore'])
 def test_conflict_and_no_overwrite(self):
  r=self.core.resume_learning();self.action('draft',dict(answer='old',reason='',guessed=False))
  with self.assertRaises(CoreError) as raised:self.core.workbench_action(r['session_id'],1,'conflict','task1','draft',dict(answer='new'))
  self.assertEqual(raised.exception.code,'revision_conflict');self.assertEqual(self.core.read_workbench(self.sid)['task']['answer'],'old')
  with self.assertRaises(CoreError):self.core.present_learning_task(self.sid,self.rev,'task2','STEM-M1-CP-C','new')
 def test_atomic_empty_submit(self):
  self.action('show')
  before=self.core.export_json();
  with self.assertRaises(CoreError):self.action('submit',dict(answer='',reason='',guessed=False))
  self.assertEqual(self.core.export_json()['events'],before['events']);self.assertEqual(self.core.resume_learning()['revision'],self.rev)
 def test_actions_require_this_task_show(self):
  before=self.core.export_json()['events']
  for kind in ('hint','answer','submit'):
   with self.assertRaises(CoreError) as raised:self.action(kind,dict(answer='2/3',reason='test',guessed=False))
   self.assertEqual(raised.exception.code,'question_not_shown')
   self.assertEqual(self.core.export_json()['events'],before)
  self.action('show');self.action('pause')
  r=self.core.present_learning_task(self.sid,self.rev,'task2','STEM-M1-CP-C','新的任务');self.rev=r['revision']
  with self.assertRaises(CoreError) as raised:self.core.workbench_action(self.sid,self.rev,'attempt2','task2','submit',dict(answer='1'))
  self.assertEqual(raised.exception.code,'question_not_shown')
 def test_backup_restore_and_a_backup_upgrade(self):
  self.action('draft',dict(answer='saved',reason='exact',guessed=False));self.core.backup(self.root/'backup');Core.restore(self.root/'backup',self.root/'restored')
  other=Core(self.root/'restored');self.assertEqual(other.export_json()['events'],self.core.export_json()['events'])
  # Simulate a genuine A-format empty vault snapshot; never overwrite an existing vault.
  Core.initialize(self.root/'a');a=Core(self.root/'a');a.backup(self.root/'a-backup');dbpath=self.root/'a-backup/learning.sqlite'
  with sqlite3.connect(dbpath) as db:db.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
  db.close()
  from eju_core.store import sha
  mp=self.root/'a-backup/manifest.json';m=json.loads(mp.read_text());m['schema_version']=1;m['files'][0].update(sha256=sha(dbpath.read_bytes()),size=dbpath.stat().st_size);mp.write_text(json.dumps(m))
  Core.restore(self.root/'a-backup',self.root/'b-copy');self.assertEqual(Core(self.root/'b-copy').resume_learning()['revision'],0)
  self.assertEqual(json.loads(mp.read_text())['schema_version'],1)

class HTTPTests(unittest.TestCase):
 def setUp(self):
  WorkbenchTests.setUp(self);self.server,self.token=make_server(self.core);self.addCleanup(self.server.server_close);thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start();self.addCleanup(self.server.shutdown)
  self.host='127.0.0.1:'+str(self.server.server_port);self.origin='http://'+self.host
 def req(self,path='/api/call',method='POST',body=None,headers=None):
  h={'Authorization':'Bearer '+self.token,'Content-Type':'application/json','Origin':self.origin};h.update(headers or {})
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port);c.request(method,path,json.dumps(body or {'method':'read_workbench','params':{'session_id':self.sid}}),h);r=c.getresponse();raw=r.read();result=(r.status,dict(r.getheaders()),raw);c.close();return result
 def test_security_boundaries(self):
  self.assertEqual(self.req(headers={'Authorization':'Bearer bad'})[0],401)
  self.assertEqual(self.req(headers={'Origin':'https://example.com'})[0],403)
  self.assertEqual(self.req(headers={'Host':'evil.example'})[0],403)
  self.assertEqual(self.req(headers={'Content-Type':'text/plain'})[0],415)
  self.assertEqual(self.req(body={'method':'read_learning_records','params':{'session_id':self.sid}})[0],400)
  self.assertEqual(self.req(body={'method':'backup','params':{'destination':'/tmp/forbidden'}})[0],400)
  self.assertEqual(self.req('/../store.py','GET')[0],404)
  self.assertEqual(self.req('/api/asset?id=../../etc/passwd&session_id='+self.sid,'GET')[0],400)
  code,headers,raw=self.req('/',method='GET');self.assertEqual(code,200);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy']);self.assertNotIn('Access-Control-Allow-Origin',headers)
 def test_shared_http_and_core(self):
  code,_,raw=self.req();self.assertEqual(code,200);self.assertEqual(json.loads(raw)['task']['id'],'task1')
  b={'method':'workbench_action','params':dict(session_id=self.sid,expected_revision=self.rev,request_id='http-draft',task_id='task1',action='draft',payload=dict(answer='from browser',reason='durable',guessed=False))}
  self.assertEqual(self.req(body=b)[0],200);self.assertEqual(self.core.read_workbench(self.sid)['task']['answer'],'from browser')
  b['params']['request_id']='stale';self.assertEqual(self.req(body=b)[0],409)
  self.assertEqual(self.core.read_workbench(self.sid)['task']['answer'],'from browser')

 def test_coaching_read_and_redo_http_write_boundary(self):
  self.core.workbench_action(self.sid,self.rev,'http-show','task1','show');self.rev+=1
  self.core.workbench_action(self.sid,self.rev,'http-submit','task1','submit',dict(answer='2/3',reason='条件中3张2张偶数',guessed=False));self.rev+=1
  attempt=self.core.read_workbench(self.sid)['task']['attempt_id']
  context=self.core.read_coaching_context(self.sid,attempt)
  params=dict(session_id=self.sid,expected_revision=self.rev,request_id='http-note',attempt_id=attempt,summary='should not save',next_step='next')
  self.assertEqual(self.req(body={'method':'save_coaching_note','params':params})[0],400)
  self.assertEqual(self.core.resume_learning()['revision'],self.rev)
  code,_,raw=self.req(body={'method':'read_coaching_context','params':dict(session_id=self.sid,attempt_id=attempt)})
  self.assertEqual(code,200);self.assertEqual(json.loads(raw)['context']['attempt']['attempt_id'],attempt)
  queue=self.req(body={'method':'read_review_queue','params':dict(session_id=self.sid)})
  self.assertEqual(queue[0],200);self.assertEqual(json.loads(queue[2])['pending_count'],1)
  redo=dict(session_id=self.sid,expected_revision=self.rev,request_id='http-redo',source_attempt_id=attempt,item_version=context['context']['attempt']['item_version'],base_task_id='task1')
  self.assertEqual(self.req(body={'method':'start_review_practice','params':redo})[0],200)
  task=self.core.read_workbench(self.sid)['task'];self.assertEqual(task['practice_mode'],'review');self.assertEqual(task['answer'],'');self.assertEqual(task['exposure'],'none')

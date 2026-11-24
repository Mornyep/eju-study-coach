"""Real loopback transport on an isolated synthetic vault, including tutor denial."""
import http.client,json,threading
from test_writing_loop import WritingFixtures
import unittest
from eju_core.http_server import make_server

class WritingHTTPTests(WritingFixtures,unittest.TestCase):
 def setUp(self):
  super().setUp();self.server,self.token=make_server(self.core);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
  self.addCleanup(lambda:(self.server.shutdown(),self.server.server_close(),self.thread.join()))
 def call(self,name,args,auth=True,origin=True):
  host=f'127.0.0.1:{self.server.server_port}';headers={'Content-Type':'application/json'}
  if auth:headers['Authorization']='Bearer '+self.token
  if origin:headers['Origin']='http://'+host
  con=http.client.HTTPConnection('127.0.0.1',self.server.server_port);con.request('POST','/api/call',json.dumps({'method':name,'params':args},ensure_ascii=False).encode(),headers);r=con.getresponse();d=json.loads(r.read());code=r.status;con.close();return code,d
 def test_authenticated_draft_revision_and_history_are_real(self):
  code,listing=self.call('read_writing_drafts',{'session_id':self.sid});self.assertEqual(code,200);self.assertEqual(len(listing['available_items']),1)
  params=self.draft_params(text='  原稿\n意味を保つ。🙂  ');code,saved=self.call('save_writing_draft',params);self.assertEqual(code,200);self.assertTrue(saved['saved']);self.rev=saved['revision']
  code,context=self.call('read_writing_context',{'session_id':self.sid,'draft_id':'draft'});self.assertEqual(code,200);self.assertEqual(context['context']['original_version']['text'],params['text'])
  revised=self.draft_params(request_id='v2',text='  自分で直した稿。\n',draft_id='draft');code,saved=self.call('save_writing_draft',revised);self.assertEqual(code,200);self.assertTrue(saved['saved'])
  code,context=self.call('read_writing_context',{'session_id':self.sid,'draft_id':'draft'});self.assertEqual(context['context']['comparison']['before_text'],params['text']);self.assertEqual(context['context']['comparison']['after_text'],revised['text'])
  code,replayed=self.call('save_writing_draft',params);self.assertEqual(code,200);self.assertTrue(replayed['replayed']);self.assertEqual(len(self.core.export_json()['events']),2)
 def test_browser_cannot_write_tutor_feedback_or_bypass_boundary(self):
  self.draft();before=self.snapshot();params=self.feedback_params()
  code,data=self.call('save_writing_feedback',params);self.assertEqual(code,400);self.assertEqual(self.snapshot(),before)
  params=self.draft_params(request_id='denied',draft_id='draft')
  for auth,origin,status in [(False,True,401),(True,False,403)]:
   self.assertEqual(self.call('save_writing_draft',params,auth,origin)[0],status);self.assertEqual(self.snapshot(),before)
  params['source_fingerprint']='forged';code,data=self.call('save_writing_draft',params);self.assertEqual(code,400);self.assertEqual(data['error']['code'],'source_changed');self.assertEqual(self.snapshot(),before)

if __name__=='__main__':unittest.main()

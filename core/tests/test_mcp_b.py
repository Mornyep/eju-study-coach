import asyncio,json,threading,unittest,http.client
import test_mcp as transport_base
from eju_core.http_server import make_server
from eju_core.store import Core
class BTransportTests(unittest.IsolatedAsyncioTestCase):
 setUp=transport_base.TransportTests.setUp
 connect=transport_base.TransportTests.connect
 tool=transport_base.TransportTests.tool
 async def test_review_and_next_practice_readonly_transport(self):
  core=Core(self.vault);r=core.resume_learning();sid=r['session_id']
  r=core.present_learning_task(sid,0,'review-task','CG-JR-F','合成协议验收')
  for action,payload,rid in [('show',{},'review-show'),('submit',{'answer':'1','reason':'合成理由'},'review-submit'),('answer',{},'review-answer'),('reflect',{'cause':'not_sure','evidence':'合成疑问','base_reflection_id':None},'review-reflection')]:
   r=core.workbench_action(sid,r['revision'],rid,'review-task',action,payload)
  before=core.export_json()
  async with self.connect() as s:
   restored=await self.tool(s,'resume_learning');sid=restored['session_id']
   self.assertEqual(restored['recent_reviews'][0]['reflection']['id'],'review-reflection')
   review=await self.tool(s,'read_study_review',dict(session_id=sid,subject='japanese_reading'))
   self.assertEqual(review['reviews'][0]['reflection']['cause_label'],'还不确定')
   self.assertIsNone(review['reviews'][0]['reason_review'])
   self.assertNotIn('solution',review['reviews'][0])
   empty=await self.tool(s,'get_next_practice',dict(session_id=sid,subject='math1'))
   self.assertTrue(empty['no_candidate']);self.assertEqual(empty['candidates'],[])
   tools={t.name for t in (await s.list_tools()).tools};self.assertNotIn('start_next_practice',tools)
   denied=await self.tool(s,'workbench_action',dict(session_id=sid,expected_revision=r['revision'],request_id='pretend-reflect',task_id='review-task',action='reflect',payload={'cause':'not_sure','evidence':'must not save'}))
   self.assertEqual(denied['error']['code'],'client_action_only')
  after=core.export_json();self.assertEqual(before['events'],after['events']);self.assertEqual(before['profile'],after['profile'])
 async def test_actual_mcp_and_http_share_task_draft_and_save(self):
  core=Core(self.vault);server,token=make_server(core);self.addCleanup(server.server_close);threading.Thread(target=server.serve_forever,daemon=True).start();self.addCleanup(server.shutdown)
  async with self.connect() as s:
   r=await self.tool(s,'resume_learning');sid=r['session_id'];r=await self.tool(s,'present_learning_task',dict(session_id=sid,expected_revision=0,request_id='task1',item_id='STEM-M1-CP-F',instruction='先判断条件'))
   def request_http():
    conn=http.client.HTTPConnection('127.0.0.1',server.server_port);conn.request('POST','/api/call',json.dumps(dict(method='workbench_action',params=dict(session_id=sid,expected_revision=r['revision'],request_id='browser-draft',task_id='task1',action='draft',payload=dict(answer='2/3',reason='通过真实HTTP保存',guessed=False)))),{'Content-Type':'application/json','Authorization':'Bearer '+token,'Origin':'http://127.0.0.1:'+str(server.server_port)})
    response=conn.getresponse();data=json.loads(response.read());conn.close();return data
   saved=await asyncio.to_thread(request_http);self.assertTrue(saved['saved'])
   state=await self.tool(s,'read_workbench',dict(session_id=sid));self.assertEqual(state['task']['reason'],'通过真实HTTP保存')
   invalid=await self.tool(s,'workbench_action',dict(session_id=sid,expected_revision=state['revision'],request_id='pretend',task_id='task1',action='show'));self.assertFalse(invalid['ok'])
  async with self.connect() as s:
   r=await self.tool(s,'resume_learning');self.assertEqual(r['workbench_task']['answer'],'2/3')
 async def test_actual_recall_mcp_roundtrip(self):
  item=Core(self.vault).items['STEM-M1-CP-F'];source=dict(type='catalog',item_id=item['id'],item_version=item['version'],quote='只有结果等可能时才能直接用个数比')
  async with self.connect() as s:
   r=await self.tool(s,'resume_learning');sid=r['session_id']
   async def write(name,**params):
    nonlocal r
    r=await self.tool(s,name,dict(session_id=sid,expected_revision=r['revision'],**params));self.assertTrue(r['ok'],r);return r
   await write('create_recall_card',request_id='card1',kind='formula_conditions',prompt='用个数比算概率需要什么条件？',answer='等可能',source=source,review_note='依据原题前置说明核对')
   await write('start_recall',request_id='trial1',card_id='card1')
   await write('record_recall_event',event=dict(id='a1',type='recall_attempt',trial_id='trial1',answer='等可能',hint='H0',answer_seen_before=False,guessed=False))
   await write('review_recall_attempt',request_id='r1',attempt_id='a1',rating='Good',result='correct',note='回答准确')
   self.assertTrue(r['applied']);cards=await self.tool(s,'read_recall_cards',dict(session_id=sid));self.assertEqual(cards['cards'][0]['review_count'],1);self.assertNotIn('answer',cards['cards'][0])

 async def test_document_page_transports_real_image_content(self):
  import test_b_documents as fixtures
  fixtures.DocumentTests.setUp(self)
  fixtures.DocumentTests.accept(self)
  async with self.connect() as s:
   r=await self.tool(s,'resume_learning')
   response=await s.call_tool('read_document_page',dict(session_id=r['session_id'],document_id='doc1',page_no=1))
   kinds=[x.type for x in response.content]
   self.assertIn('image',kinds)
   self.assertIn('text',kinds)

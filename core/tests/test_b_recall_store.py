import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from eju_core.store import Core
from eju_core.errors import CoreError
class RecallStoreTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.vault=self.root/'vault';Core.initialize(self.vault);self.core=Core(self.vault);r=self.core.resume_learning();self.sid=r['session_id'];self.rev=0
  self.item=self.core.items['STEM-M1-CP-F'];self.source=dict(type='catalog',item_id=self.item['id'],item_version=self.item['version'],quote='只有结果等可能时才能直接用个数比')
  self.create()
 def save(self,r):self.rev=r['revision'];return r
 def create(self,rid='card1',kind='formula_conditions',source=None):return self.save(self.core.create_recall_card(self.sid,self.rev,rid,kind,'用个数比算概率需要什么条件？','各结果等可能',source or self.source,'已依据前置知识说明核对适用条件。'))
 def start(self,rid='trial1'):return self.save(self.core.start_recall(self.sid,self.rev,rid,'card1'))
 def event(self,type='recall_attempt',rid='attempt1',**extra):
  e=dict(id=rid,type=type,trial_id='trial1')
  if type=='recall_attempt':e.update(answer='各结果等可能',hint='H0',answer_seen_before=False,guessed=False)
  else:e.update(level='answer',source='codex_report')
  e.update(extra);return self.save(self.core.record_recall_event(self.sid,self.rev,e))
 def review(self,**extra):
  args=dict(session_id=self.sid,expected_revision=self.rev,request_id='review1',attempt_id='attempt1',rating='Good',result='correct',note='与来源中的等可能条件一致。');args.update(extra);return self.save(self.core.review_recall_attempt(**args))
 def test_complete_recall_exposure_after_answer_does_not_invalidate_first_response(self):
  self.start();self.event();self.event('recall_exposure','shown');r=self.review();self.assertTrue(r['applied'])
  replay=self.core.review_recall_attempt(self.sid,0,'review1','attempt1','Good','correct','与来源中的等可能条件一致。');self.assertTrue(replay['replayed'])
  self.assertEqual(self.core.read_recall_cards(self.sid,'card1','coach')['cards'][0]['state']['review_count'],1)
  with self.assertRaises(CoreError):self.review(request_id='duplicate-review')
  self.core.backup(self.root/'backup');Core.restore(self.root/'backup',self.root/'restored');c=Core(self.root/'restored');s=c.resume_learning();self.assertEqual(c.read_recall_cards(s['session_id'],'card1','coach')['cards'],self.core.read_recall_cards(self.sid,'card1','coach')['cards'])
 def test_hint_suppresses_same_trial_and_new_trial_cannot_reset(self):
  self.start();self.event('recall_exposure','hint',level='hint')
  with self.assertRaises(CoreError):self.start('another-trial')
  self.event();before=self.core.read_recall_cards(self.sid,'card1','coach')['cards'][0]['state'];r=self.review();self.assertFalse(r['applied']);self.assertEqual(r['reason'],'hint_used')
  self.assertEqual(self.core.read_recall_cards(self.sid,'card1','coach')['cards'][0]['state'],before)
  with self.assertRaises(CoreError) as error:self.start('again');self.assertEqual(error.exception.code,'recall_not_due')
 def test_unverified_source_and_unknown_result_preserve_schedule(self):
  with self.assertRaises(CoreError):self.create('bad','complex_procedure')
  with self.assertRaises(CoreError):self.create('bad-source',source={**self.source,'quote':'unsupported'})
  self.start();self.event();r=self.review(result='ungraded');self.assertFalse(r['applied']);self.assertEqual(r['reason'],'grading_unverified')
 def test_source_dispute_blocks_and_incorrect_not_good(self):
  self.start();self.event(answer='任何情况')
  with self.assertRaises(CoreError):self.review(result='incorrect')
  self.save(self.core.record_learning_event(self.sid,self.rev,dict(id='dispute',type='issue',itemId=self.item['id'],reason='需要重新核对条件')))
  self.assertFalse(self.core.read_recall_cards(self.sid,'card1')['cards'][0]['available'])
  r=self.review(result='incorrect',rating='Again');self.assertFalse(r['applied'])
 def test_independent_wrong_again_and_cross_session_suppression(self):
  self.start();self.event(answer='没有条件');r=self.review(result='incorrect',rating='Again');self.assertTrue(r['applied'])
  self.create('card2');self.save(self.core.start_recall(self.sid,self.rev,'trial2','card2'));r=self.core.resume_learning();self.sid=r['session_id'];self.event(rid='a2',trial_id='trial2');r=self.review(request_id='r2',attempt_id='a2');self.assertFalse(r['applied'])

 def test_browser_history_does_not_expose_recall_answers(self):
  rows=self.core.read_workbench_records(self.sid)['records']
  card=next(r['event'] for r in rows if r['event']['type']=='recall_card')
  self.assertNotIn('answer',card);self.assertNotIn('source',card);self.assertNotIn('state',card)
  self.assertEqual(self.core.read_recall_cards(self.sid,'card1','coach')['cards'][0]['answer'],'各结果等可能')

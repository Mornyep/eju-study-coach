import copy
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from eju_core.store import Core
from eju_core.errors import CoreError
from eju_core.evidence import _prior_exposure

class SubjectReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); Core.initialize(self.root/'vault'); self.core=Core(self.root/'vault')
        self.sid=self.core.resume_learning()['session_id']; self.rev=0
        r=self.core.present_learning_task(self.sid,0,'task','CG-JR-F','先读原文再判断。'); self.rev=r['revision']
    def action(self, action, payload=None, request=None):
        r=self.core.workbench_action(self.sid,self.rev,request or 'r'+str(self.rev),'task',action,payload)
        self.rev=r['revision']; return r
    def complete(self):
        self.action('show'); self.action('submit',{'answer':'1','reason':'还不确定'})
        self.action('answer')
    def test_reflection_is_explicit_and_not_mastery(self):
        with self.assertRaises(CoreError): self.action('reflect',{'cause':'not_sure','evidence':'不确定'})
        self.complete(); before=self.core.resume_learning()['evidence']
        payload={'cause':'reference','evidence':'指代仍需核对','next_action':'回读前句','base_reflection_id':None}
        old_revision=self.rev; self.action('reflect',payload,'reflection-one')
        self.assertEqual(self.core.read_workbench(self.sid)['reflection']['source'],'learner_self_report')
        self.assertEqual(before,self.core.resume_learning()['evidence'])
        replay=self.core.workbench_action(self.sid,old_revision,'reflection-one','task','reflect',payload)
        self.assertTrue(replay['replayed'])
        with self.assertRaises(CoreError) as error:self.action('reflect',payload)
        self.assertEqual(error.exception.code,'reflection_conflict')
        payload['base_reflection_id']='reflection-one'; self.action('reflect',payload,'reflection-two')
        self.core.backup(self.root/'backup');Core.restore(self.root/'backup',self.root/'restored')
        restored=Core(self.root/'restored');sid=restored.resume_learning()['session_id']
        self.assertEqual(restored.read_workbench(sid)['reflection']['previous_id'],'reflection-one')
    def test_guidance_only_after_reveal_and_version_bound(self):
        item=self.core.items['CG-JR-F']
        self.core.coaching[item['id']]={'item_version':item['version'],'reviewed':True,'questionFocus':'secret'}
        self.assertNotIn('coaching',self.core.read_workbench(self.sid))
        self.complete();self.assertEqual(self.core.read_workbench(self.sid)['coaching']['questionFocus'],'secret')
        self.core.coaching[item['id']]['item_version']='stale'
        self.assertIsNone(self.core.read_workbench(self.sid)['coaching'])
    def test_reflection_rejects_invalid_subject_and_fields(self):
        self.complete()
        for payload in ({'cause':'time_place','evidence':'x'},{'cause':'not_sure','evidence':''},{'cause':'not_sure','evidence':'x','result':'correct'}):
            with self.assertRaises(CoreError):self.action('reflect',payload)
        self.assertIsNone(self.core.read_workbench(self.sid)['reflection'])
    def test_shared_passage_only_not_general_learning_group(self):
        a=copy.deepcopy(self.core.items['CG-JR-F']);b=copy.deepcopy(self.core.items['CG-JR-C'])
        a['exposureGroupId']=b['exposureGroupId']='passage';self.core.items[a['id']]=a;self.core.items[b['id']]=b
        self.action('show')
        with self.core.connection(write=True) as db:
            attempt=self.core.validate_native(db,dict(id='sibling',type='attempt',itemId=b['id'],itemVersion=b['version'],answer='1',reason='理由',result='ungraded',hint='H0',seenBefore=False,guessed=False,seconds=None,grading='external'),self.sid)
        self.assertTrue(attempt['seenBefore'])
        events=self.core.export_json()['events'];self.assertTrue(_prior_exposure(events,attempt,by_id=self.core.items))
        del a['exposureGroupId'];del b['exposureGroupId']
        self.assertFalse(_prior_exposure(events,attempt,by_id=self.core.items))

if __name__=='__main__':unittest.main()

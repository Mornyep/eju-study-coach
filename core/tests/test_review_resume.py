"""Integration regression: the whole resume envelope, not just its new queue."""
import json
import unittest
import test_review_loop as review_fixtures


class ResumeBudgetTests(unittest.TestCase):
    def test_maximal_valid_notes_and_long_multilingual_answers_fit_full_resume(self):
        fixture=review_fixtures.ReviewLoopTests("test_queue_input_validation_is_strict_and_read_only")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for index,item in enumerate(("source","shared","coarse")):
            aid="budget-attempt-"+str(index)
            fixture.attempt(aid,item,answer="答"*1000,reason="理由"*500)
            fixture.save(dict(id="budget-reason-"+str(index),type="reason_review",targetId=aid,
                              reviewer="tutor",agrees=True,result="correct",note="核"*1000))
            fixture.note("budget-note-"+str(index),aid,summary="解"*2000,next_step="次"*1200,
                         open_question="問"*1200,hypothesis="仮"*1000)
        before=fixture.snapshot()
        view=fixture.core.resume_learning()
        self.assertLess(len(json.dumps(view,ensure_ascii=False).encode("utf-8")),20000)
        self.assertTrue(view["resume_truncated"])
        self.assertEqual(fixture.snapshot(),before)
        self.assertEqual(view["review_queue"]["pending_count"],3)
        self.assertTrue(view["review_queue"]["entries"])
        restored=fixture.core.read_coaching_context(view["session_id"],"budget-attempt-2")["context"]
        self.assertEqual(restored["coaching_note"]["summary"],"解"*2000)
        self.assertEqual(restored["attempt"]["answer"],"答"*1000)
        self.assertEqual(restored["attempt"]["reason_review"]["note"],"核"*1000)
        self.assertTrue(view["recent_reviews"][0]["text_truncated"] if view["recent_reviews"] else view["recent_reviews_truncated"])

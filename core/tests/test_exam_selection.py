import copy
import unittest

from test_core import CoreCase
from eju_core.selection import alignment_complete


class ExamSelectionTests(CoreCase):
    def test_integrated_supplement_never_defaults_to_component_arithmetic(self):
        for minutes in (1, 5, 10, 30):
            result = self.core.get_task_candidates(self.sid, minutes, "integrated", "supplement")
            self.assertTrue(result["candidates"])
            self.assertEqual(result["purpose"], "supplement")
            for candidate in result["candidates"]:
                self.assertEqual(candidate["practice_scope"], "exam_style_original")
                self.assertFalse(candidate["item_id"].startswith("CG-I-"))
                self.assertNotIn("answer", candidate)
                self.assertNotIn("optionReview", candidate)

    def test_component_drill_requires_explicit_prerequisite_selection(self):
        result = self.core.get_task_candidates(self.sid, 10, "integrated", "prerequisite")
        self.assertEqual([x["item_id"] for x in result["candidates"]], ["CG-I-F"])
        self.assertEqual(result["candidates"][0]["practice_scope"], "component_skill")
        old = self.core.read_material(self.sid, "CG-I-F", "coach")
        self.assertEqual(old["item_version"], "original-9511fa47894b91b9")
        self.assertIsNone(old["exam_alignment_review"])
        self.assert_error("invalid_purpose", self.core.get_task_candidates,
                          self.sid, 10, "integrated", "anything")

    def test_missing_review_never_silently_falls_back_to_old_drills(self):
        for item in self.core.items.values():
            if "integrated" in item.get("subjects", []):
                item.pop("examAlignment", None)
        result = self.core.get_task_candidates(self.sid, 10, "integrated", "supplement")
        self.assertTrue(result["no_candidate"])
        self.assertEqual(result["candidates"], [])
        self.assertTrue(self.core.get_task_candidates(self.sid, 10, "integrated", "prerequisite")["candidates"])

    def test_exhausted_bank_does_not_reissue_seen_items_or_downgrade(self):
        fresh = [x for x in self.core.items.values()
                 if "integrated" in x.get("subjects", []) and alignment_complete(x)]
        self.assertEqual(len(fresh), 7)
        for index, item in enumerate(fresh):
            self.save({"id": f"seen-{index}", "type": "exposure", "itemId": item["id"],
                       "itemVersion": item["version"], "level": "stem", "source": "codex_report", "note": "shown in isolated test"})
        result = self.core.get_task_candidates(self.sid, 10, "integrated", "supplement")
        self.assertTrue(result["no_candidate"])
        self.assertEqual(result["candidates"], [])

    def test_review_is_coach_only_and_internal_read_does_not_mark_exposure(self):
        chosen = self.core.get_task_candidates(self.sid, 10, "integrated", "supplement")["candidates"][0]
        stem = self.core.read_material(self.sid, chosen["item_id"])
        self.assertEqual(stem["practice_scope"], "exam_style_original")
        self.assertNotIn("answer", stem)
        self.assertNotIn("exam_alignment_review", stem)
        self.assertNotIn("solution", stem)
        coach = self.core.read_material(self.sid, chosen["item_id"], "coach")
        self.assertEqual(len(coach["exam_alignment_review"]["optionReview"]), 4)
        self.assertFalse(coach["learner_exposure_recorded"])
        self.assertEqual(self.core.resume_learning()["event_count"], 0)

    def test_label_alone_or_inconsistent_key_does_not_qualify(self):
        item = next(x for x in self.core.items.values() if alignment_complete(x))
        changed = copy.deepcopy(item)
        changed["examAlignment"] = {"status": "reviewed", "format": "four_choice_single"}
        self.assertFalse(alignment_complete(changed))
        changed = copy.deepcopy(item)
        changed["grading"]["value"] = "9"
        self.assertFalse(alignment_complete(changed))
        changed = copy.deepcopy(item)
        for option in changed["examAlignment"]["optionReview"]:
            if not option["correct"]:
                option.pop("misconception")
                break
        self.assertFalse(alignment_complete(changed))


if __name__ == "__main__":
    unittest.main()

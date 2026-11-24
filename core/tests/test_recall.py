"""Actual fsrs SDK behavior, conservative evidence gates and serialized invariants."""
import copy
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from fsrs import Card, Rating, Scheduler
from eju_core.recall import (ADAPTER_VERSION, FSRS_VERSION, RecallError, apply_review,
                             is_eligible_kind, new_state, validate_state)

UTC = timezone.utc
NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
ELIGIBLE = {"kind": "vocabulary", "hint": "H0", "answer_seen_before": False,
            "grading": "verified", "guessed": False}


class RecallCase(unittest.TestCase):
    def test_actual_sdk_and_deterministic_creation(self):
        self.assertEqual(version("fsrs"), "6.3.2")
        first = new_state("日文-card-1", NOW)
        self.assertEqual(first, new_state("日文-card-1", NOW))
        self.assertEqual(first["adapter_version"], ADAPTER_VERSION)
        self.assertEqual(first["fsrs_version"], FSRS_VERSION)
        self.assertEqual(first["card"]["due"], NOW.isoformat())
        self.assertNotEqual(first["card"]["card_id"], new_state("other", NOW)["card"]["card_id"])
        self.assertFalse(first["scheduler"]["settings"]["enable_fuzzing"])
        self.assertEqual(validate_state(json.loads(json.dumps(first))), first)

    def test_sdk_parity_all_ratings(self):
        for name in ("Again", "Hard", "Good", "Easy"):
            with self.subTest(rating=name):
                initial = new_state("fact-1", NOW)
                before = copy.deepcopy(initial)
                result = apply_review(initial, name, NOW, ELIGIBLE)
                scheduler = Scheduler.from_dict(initial["scheduler"]["settings"])
                expected_card, expected_log = scheduler.review_card(Card.from_dict(initial["card"]), getattr(Rating, name), review_datetime=NOW)
                self.assertEqual(result["state"]["card"], expected_card.to_dict())
                self.assertEqual(result["log"]["fsrs_log"], expected_log.to_dict())
                self.assertEqual(result["state"]["review_count"], 1)
                self.assertTrue(result["applied"])
                self.assertEqual(result["log"]["rating_scope"], "recall_difficulty")
                self.assertEqual(initial, before)

    def test_serialized_multiday_replay_and_lapse(self):
        initial = new_state("definition", NOW)
        schedule = [("Good", NOW), ("Good", NOW + timedelta(minutes=10)),
                    ("Easy", NOW + timedelta(days=3)), ("Again", NOW + timedelta(days=15))]
        def replay():
            state = copy.deepcopy(initial)
            logs = []
            sdk = Card.from_dict(state["card"])
            scheduler = Scheduler.from_dict(state["scheduler"]["settings"])
            for rating, clock in schedule:
                response = apply_review(json.loads(json.dumps(state)), rating, clock, {**ELIGIBLE, "kind":"definition"})
                sdk, sdk_log = scheduler.review_card(sdk, getattr(Rating, rating), review_datetime=clock)
                self.assertEqual(response["state"]["card"], sdk.to_dict())
                self.assertEqual(response["log"]["fsrs_log"], sdk_log.to_dict())
                self.assertEqual(validate_state(response["state"]), response["state"])
                logs.append(response["log"])
                state = response["state"]
            return state, logs
        first, logs = replay()
        self.assertEqual((first, logs), replay())
        self.assertEqual(first["review_count"], 4)
        self.assertEqual(first["card"]["state"], 3)  # actual FSRS Relearning after lapse
        self.assertNotIn("score", first)

    def test_kinds(self):
        for kind in ("vocabulary", "definition", "formula_conditions"):
            self.assertTrue(is_eligible_kind(kind))
            self.assertTrue(apply_review(new_state(kind, NOW), "Again", NOW, {**ELIGIBLE,"kind":kind})["applied"])
        for kind in ("complex_procedure", "writing", "problem", "formula", None, {}, [], 1):
            self.assertFalse(is_eligible_kind(kind))
            response = apply_review(new_state("f",NOW), "Easy", NOW, {**ELIGIBLE,"kind":kind})
            self.assertFalse(response["applied"])
            self.assertEqual(response["reason"], "ineligible_kind")

    def test_all_hint_levels_suppress_without_mutation(self):
        initial = new_state("f", NOW)
        initial = apply_review(initial, "Easy", NOW, ELIGIBLE)["state"]
        for hint in ("H1", "H2", "H3", "H4"):
            before = copy.deepcopy(initial)
            response = apply_review(initial, "Easy", NOW + timedelta(days=1), {**ELIGIBLE,"hint":hint})
            self.assertEqual(response["state"], initial)
            self.assertEqual(initial, before)
            self.assertFalse(response["applied"])
            self.assertEqual(response["reason"], "hint_used")
            self.assertIsNone(response["log"]["fsrs_log"])
            self.assertEqual(response["log"]["state_before_sha256"],response["log"]["state_after_sha256"])

    def test_uncertain_exposed_ungraded_guessed_suppress(self):
        mutations = [({"hint":None},"hint_unknown"), ({"answer_seen_before":True},"answer_seen_before"),
                     ({"answer_seen_before":0},"answer_exposure_unknown"), ({"grading":"ungraded"},"grading_unverified"),
                     ({"grading":"automatic"},"grading_unverified"), ({"guessed":True},"guessed"),
                     ({"guessed":0},"guessing_unknown")]
        initial = new_state("f",NOW)
        for change, reason in mutations:
            evidence = {**ELIGIBLE, **change}
            result = apply_review(initial, "Easy", NOW, evidence)
            self.assertEqual(result["reason"], reason)
            self.assertEqual(result["state"], initial)
            self.assertEqual(result["log"]["evidence"], evidence)
            self.assertFalse(result["applied"])
        for field in ELIGIBLE:
            evidence = dict(ELIGIBLE)
            del evidence[field]
            self.assertFalse(apply_review(initial,"Easy",NOW,evidence)["applied"])
        self.assertFalse(apply_review(initial,"Easy",NOW,None)["applied"])

    def test_verified_independent_wrong_recall_can_rate_again(self):
        result = apply_review(new_state("f",NOW),"Again",NOW,{**ELIGIBLE,"result":"wrong"})
        self.assertTrue(result["applied"])
        self.assertEqual(result["log"]["fsrs_log"]["rating"],1)
        self.assertEqual(result["state"]["card"]["due"],(NOW+timedelta(minutes=1)).isoformat())

    def test_future_and_non_utc_clock_rejected(self):
        initial = new_state("f", NOW)
        with self.assertRaisesRegex(RecallError,"precede"):
            apply_review(initial,"Good",NOW-timedelta(seconds=1),ELIGIBLE)
        reviewed = apply_review(initial,"Good",NOW+timedelta(days=2),ELIGIBLE)["state"]
        for evidence in (ELIGIBLE,{**ELIGIBLE,"hint":"H1"}):
            with self.assertRaisesRegex(RecallError,"precede"):
                apply_review(reviewed,"Easy",NOW+timedelta(days=1),evidence)
        # Equal timestamps are deterministic and allowed by the nondecreasing contract.
        self.assertTrue(apply_review(reviewed,"Good",NOW+timedelta(days=2),ELIGIBLE)["applied"])
        for clock in (NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=9))), "2026-10-14"):
            with self.assertRaisesRegex(RecallError,"aware UTC"):
                new_state("f",clock)
            with self.assertRaisesRegex(RecallError,"aware UTC"):
                apply_review(initial,"Good",clock,ELIGIBLE)

    def test_bad_rating_and_ids(self):
        for rating in ("good", "correct", "5", 3, True, None):
            with self.assertRaisesRegex(RecallError,"rating"):
                apply_review(new_state("f",NOW),rating,NOW,ELIGIBLE)
        for card_id in ("", " f", "f ", None, 1, "x"*513):
            with self.assertRaisesRegex(RecallError,"card_id"):
                new_state(card_id,NOW)

    def test_unknown_versions_and_modified_scheduler(self):
        initial = new_state("f",NOW)
        for key, value in (("schema_version",2),("schema_version",True),("adapter_version","next"),("fsrs_version","5.0.0")):
            changed=copy.deepcopy(initial)
            changed[key]=value
            with self.assertRaisesRegex(RecallError,"version"):
                validate_state(changed)
        for key,value in (("enable_fuzzing",True),("desired_retention",.95),("learning_steps",[60]),("parameters",[])):
            changed=copy.deepcopy(initial)
            changed["scheduler"]["settings"][key]=value
            with self.assertRaisesRegex(RecallError,"scheduler"):
                apply_review(changed,"Good",NOW,ELIGIBLE)
        with self.assertRaisesRegex(RecallError,"legacy"):
            validate_state({"stage":3,"due":"tomorrow"})

    def test_malformed_card_no_sdk_coercion(self):
        initial=apply_review(new_state("f",NOW),"Easy",NOW,ELIGIBLE)["state"]
        mutations = [("card_id",None),("card_id",str(initial["card"]["card_id"])),("state",True),
                     ("state",9),("step",0),("stability",float("nan")),("stability",10**500),("stability",-1),
                     ("difficulty",11),("difficulty",None),("due",None),
                     ("due",NOW.replace(tzinfo=None).isoformat()),("last_review",None),
                     ("last_review",(NOW-timedelta(days=1)).isoformat())]
        for key,value in mutations:
            with self.subTest(key=key,value=value):
                changed=copy.deepcopy(initial)
                changed["card"][key]=value
                with self.assertRaises(RecallError):
                    apply_review(changed,"Easy",NOW,ELIGIBLE)
        for key,value in (("review_count",-1),("review_count",True),("created_at",None)):
            changed=copy.deepcopy(initial)
            changed[key]=value
            with self.assertRaises(RecallError):
                validate_state(changed)

    def test_logs_detached_and_json_serializable(self):
        initial=new_state("f",NOW)
        evidence={**ELIGIBLE,"note":{"synthetic":"日本語"}}
        result=apply_review(initial,"Good",NOW,evidence)
        result["log"]["evidence"]["note"]["synthetic"]="changed"
        self.assertEqual(evidence["note"]["synthetic"],"日本語")
        self.assertEqual(initial["review_count"],0)
        self.assertEqual(json.loads(json.dumps(result,ensure_ascii=False)),result)
        with self.assertRaisesRegex(RecallError,"JSON"):
            apply_review(initial,"Good",NOW,{**ELIGIBLE,"invalid":object()})

if __name__ == "__main__":
    unittest.main()

"""Beta9 attribution/recommendation tests: only synthetic records and local media."""
import copy
import hashlib
import json
from pathlib import Path
import threading
import unittest

from test_practice_focus import FocusFixtures
import test_review_loop as legacy
from eju_core import review_loop, server
from eju_core.errors import CoreError
from eju_core.evidence import evidence_map
from eju_core.store import Core, canonical, sha


class ReasoningFixtures(FocusFixtures):
    context = legacy.ReviewLoopTests.context
    note = legacy.ReviewLoopTests.note
    reason_review = legacy.ReviewLoopTests.reason_review
    reflection = legacy.ReviewLoopTests.reflection
    queue = legacy.ReviewLoopTests.queue

    def setUp(self):
        FocusFixtures.setUp(self)
        source = Path(__file__).resolve().parents[1] / "src" / "eju_core"
        self.assertEqual(Path(review_loop.__file__).resolve(), source / "review_loop.py")
        self.assertEqual(Path(server.__file__).resolve(), source / "server.py")

    def assessment(self, status="supported", quote="本人作答依据", stage=None):
        return {"status": status, "reason_quote": quote,
                "error_stage": stage or ("none" if status == "supported" else "unknown")}

    def prepared(self, item_id="shared", agrees=True, **attempt):
        self.attempt(item_id=item_id, **attempt)
        self.reason_review(agrees=agrees)
        return self.context()

    def recommendation(self, view=None, **overrides):
        context = (view or self.context())["context"]
        result = dict(reasoning_assessment=self.assessment(), practice_focus_id="jr.evidence",
                      practice_focus_version=context["practice_focus_version"])
        result.update(overrides)
        return result

    def atomic_error(self, code, **kwargs):
        before = self.snapshot()
        with self.core.connection() as db:
            requests = list(db.execute("SELECT * FROM requests"))
        self.assert_error(code, self.note, **kwargs)
        self.assertEqual(self.snapshot(), before)
        with self.core.connection() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM requests")],
                             [tuple(row) for row in requests])


class ReasoningCoachingTests(ReasoningFixtures, unittest.TestCase):
    def test_supported_freezes_only_public_focus_and_preserves_evidence(self):
        self.prepared(result="incorrect")
        self.reflection(cause="not_sure")
        before = self.snapshot()
        evidence = evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"])
        view = self.context()
        self.assertEqual(view["context"]["practice_focus_options"], [{"id": "jr.evidence", "label": "明确依据",
                          "description": "练习查找原文依据。", "subject": "japanese_reading"}])
        self.assertTrue(view["context"]["practice_focus_available"])
        self.assertEqual(view["context"]["practice_focus_version"], hashlib.sha256(self.focus_path.read_bytes()).hexdigest())
        self.note(**self.recommendation(view))
        after = self.snapshot()
        self.assertEqual(after["events"][:-1], before["events"])
        self.assertEqual(after["profile"], before["profile"])
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]), evidence)
        event = after["events"][-1]
        self.assertEqual(set(event["practice_recommendation"]), {"id", "label", "description", "subject", "version"})
        note = self.context()["context"]["coaching_note"]
        self.assertEqual(note["reasoning_assessment"], self.assessment())
        self.assertTrue(note["practice_recommendation"]["available"])
        self.assertFalse(note["practice_recommendation"]["stale"])
        self.assertEqual(note["basis_reason_review_id"], "reason-check")
        self.assertEqual(self.context()["context"]["attempt"]["result"], "incorrect")
        self.assertNotIn("SECRET_", json.dumps(view) + json.dumps(note))
        self.assertNotIn("guidance_sha256", json.dumps(view) + json.dumps(note))
        self.assertFalse(any(row["type"] in ("task", "exposure") for row in after["events"]))

    def test_correct_answer_and_tutor_disagreement_stay_separate_for_all_stages(self):
        self.prepared(agrees=False, result="correct")
        for index, stage in enumerate(("question_reading", "knowledge", "method", "calculation", "graph", "unknown")):
            assessment = self.assessment("needs_revision", stage=stage)
            self.note("stage-" + str(index), **self.recommendation(reasoning_assessment=assessment))
            context = self.context()["context"]
            self.assertEqual(context["attempt"]["result"], "correct")
            self.assertFalse(context["attempt"]["reason_review"]["agrees"])
            self.assertEqual(context["coaching_note"]["reasoning_assessment"], assessment)
        self.assertEqual(sum(row["type"] == "reason_review" for row in self.snapshot()["events"]), 1)

    def test_clarification_empty_and_nonempty_reason_requires_question_without_fabricated_review(self):
        for index, original in enumerate(("", "   ", "理由还不确定")):
            attempt_id = "clarify-" + str(index)
            self.attempt(attempt_id, "shared", reason=original)
            assessment = self.assessment("needs_clarification", "" if not original.strip() else "还不确定")
            before = self.snapshot()
            self.note("question-" + str(index), attempt_id, reasoning_assessment=assessment,
                      open_question="你是根据哪一句判断的？")
            after = self.snapshot()
            self.assertEqual(len(after["events"]), len(before["events"]) + 1)
            note = self.context(attempt_id)["context"]["coaching_note"]
            self.assertIsNone(note["basis_reason_review_id"])
            self.assertEqual(note["reasoning_assessment"], assessment)
            self.assertNotIn("practice_recommendation", note)
        self.assertFalse(any(row["type"] == "reason_review" for row in self.snapshot()["events"]))

    def test_clarification_cannot_replace_tutor_judgment_or_suggest_focus(self):
        self.prepared()
        assessment = self.assessment("needs_clarification")
        self.atomic_error("reason_review_conflict", reasoning_assessment=assessment, open_question="哪一句？")
        self.append(dict(id="retract-review", type="retract", targetId="reason-check", reason="合成历史撤销"))
        self.atomic_error("invalid_field", reasoning_assessment=assessment)
        self.atomic_error("invalid_field", reasoning_assessment=assessment, open_question="  ")
        self.atomic_error("reason_review_required", **self.recommendation(reasoning_assessment=assessment), open_question="哪一句？")
        self.save(dict(id="self-check", type="reason_review", targetId="attempt", reviewer="self", agrees=True, note="本人仍不确定"))
        self.note(reasoning_assessment=assessment, open_question="哪一句？")
        self.assertEqual(self.context()["context"]["coaching_note"]["basis_reason_review_id"], "self-check")

    def test_missing_self_retracted_wrong_and_other_attempt_review_cannot_support_assessment(self):
        self.attempt(item_id="shared")
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment())
        self.attempt("other", "extra")
        self.reason_review("other", "other-review")
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment())
        self.save(dict(id="self-check", type="reason_review", targetId="attempt", reviewer="self", agrees=True, note="自己核对"))
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment())
        self.reason_review(agrees=False)
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment())
        self.append(dict(id="retract-review", type="retract", targetId="reason-check", reason="合成历史撤销"))
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment("needs_revision"))
        self.reason_review(event_id="agree", agrees=True)
        self.atomic_error("reason_review_required", reasoning_assessment=self.assessment("needs_revision"))

    def test_quote_is_exact_original_including_text_beyond_bounded_projection(self):
        original = "前" * 1100 + "原始理由尾部：ＡとＢ。" + "后" * 100
        self.prepared(reason=original)
        view = self.context()
        self.assertNotIn("原始理由尾部", view["context"]["attempt"]["reason"])
        quote = "原始理由尾部：ＡとＢ。"
        self.note(reasoning_assessment=self.assessment(quote=quote))
        self.assertEqual(self.context()["context"]["coaching_note"]["reasoning_assessment"]["reason_quote"], quote)
        for invalid in ("", "   ", "原始理由尾部：AとB。", "前…尾部", "不存在", "前" * 501):
            self.atomic_error("invalid_field" if len(invalid) > 500 else "reason_quote_mismatch",
                              request_id="bad-quote", reasoning_assessment=self.assessment(quote=invalid))
        self.note("max-quote", reasoning_assessment=self.assessment(quote="前" * 500))

    def test_unknown_fields_types_and_illegal_status_stage_are_atomic(self):
        self.prepared()
        valid = self.assessment()
        cases = [[], "supported", True, {}, {**valid, "secret": "no"}, {**valid, "open_question": "no"},
                 {key: value for key, value in valid.items() if key != "error_stage"}]
        cases += [{**valid, field: value} for field, value in (("status", None), ("status", []),
                  ("status", "correct"), ("status", True), ("reason_quote", None), ("reason_quote", {}),
                  ("reason_quote", 1), ("error_stage", []), ("error_stage", "knowledge"), ("error_stage", False))]
        cases += [self.assessment("needs_revision", stage="none"), self.assessment("needs_clarification", stage="method")]
        for assessment in cases:
            with self.subTest(assessment=assessment):
                self.atomic_error("invalid_field", reasoning_assessment=assessment)

    def test_focus_pair_types_and_assessment_are_required(self):
        self.prepared()
        version = self.context()["context"]["practice_focus_version"]
        for kwargs in (dict(practice_focus_id="jr.evidence"), dict(practice_focus_version=version),
                       dict(practice_focus_id=True, practice_focus_version=version),
                       dict(practice_focus_id="jr.evidence", practice_focus_version=[])):
            self.atomic_error("invalid_field", reasoning_assessment=self.assessment(), **kwargs)
        self.atomic_error("reason_review_required", practice_focus_id="jr.evidence", practice_focus_version=version)
        for focus_id in ("missing", "jr.main", "in.graph"):
            self.atomic_error("focus_unavailable", **self.recommendation(practice_focus_id=focus_id))

    def test_focus_rejects_stale_mapping_wrong_subject_and_unclassified_source(self):
        view = self.prepared()
        params = self.recommendation(view)
        original = copy.deepcopy(self.focus_map)
        mutations = [lambda: self.focus_map["items"]["shared"].update(reviewed=False),
                     lambda: self.focus_map["items"]["shared"].update(item_version="stale"),
                     lambda: self.focus_map["items"]["shared"].update(basis={"guidance_sha256": "0" * 64}),
                     lambda: self.focus_map["items"].pop("shared")]
        for mutation in mutations:
            self.focus_map = copy.deepcopy(original)
            mutation()
            self.write_focus()
            self.assertEqual(self.context()["context"]["practice_focus_options"], [])
            self.assertFalse(self.context()["context"]["practice_focus_available"])
            self.atomic_error("focus_changed", **params)
            self.atomic_error("focus_unavailable", **self.recommendation())
        self.focus_map = original
        self.write_focus()
        self.core.items["shared"]["subjects"] = ["integrated"]
        self.assertEqual(self.context()["context"]["practice_focus_options"], [])
        self.atomic_error("focus_unavailable", **params)
        self.core.items["shared"]["subjects"] = ["math1"]
        self.assertIsNone(self.context()["context"]["practice_focus_version"])
        self.atomic_error("focus_unavailable", **params)

    def test_missing_metadata_and_changed_guidance_disable_without_writes(self):
        self.prepared()
        self.note(**self.recommendation())
        original = self.focus_path.read_bytes()
        before = self.snapshot()
        for raw in (None, b"{}", b"broken"):
            if raw is None:
                self.focus_path.unlink()
            else:
                self.focus_path.write_bytes(raw)
            context = self.context()["context"]
            self.assertEqual(context["practice_focus_options"], [])
            self.assertIsNone(context["practice_focus_version"])
            self.assertFalse(context["coaching_note"]["practice_recommendation"]["available"])
            self.assertTrue(context["coaching_note"]["practice_recommendation"]["reason"])
        self.focus_path.write_bytes(original)
        self.core.coaching["shared"]["questionFocus"] += "改变"
        self.assertFalse(self.context()["context"]["coaching_note"]["practice_recommendation"]["available"])
        self.assertEqual(self.snapshot(), before)

    def test_recommendation_becomes_unavailable_with_new_attempt_or_material_loss(self):
        self.prepared()
        self.note(**self.recommendation())
        (self.media / "shared.png").unlink()
        self.assertFalse(self.context()["context"]["coaching_note"]["practice_recommendation"]["available"])
        self.atomic_error("material_unavailable", request_id="missing-media", **self.recommendation(
            practice_focus_version=self.focus_hash()))
        self.item("shared")
        self.attempt("newer", "shared")
        context = self.context()["context"]
        self.assertFalse(context["coaching_note"]["practice_recommendation"]["available"])
        self.assertIn("source_changed", context["coaching_note"]["practice_recommendation"]["reason"])
        self.atomic_error("source_changed", request_id="old-source", **self.recommendation(practice_focus_version=self.focus_hash()))
        self.note("legacy-history", summary="历史说明可继续追加")

    def focus_hash(self):
        return hashlib.sha256(self.focus_path.read_bytes()).hexdigest()

    def test_invalid_sources_reject_and_existing_note_is_unavailable(self):
        self.prepared()
        self.note(**self.recommendation())
        self.save(dict(id="invalid", type="retract", targetId="attempt", reason="合成撤销"))
        self.atomic_error("source_invalid", request_id="invalid-note", **self.recommendation(practice_focus_version=self.focus_hash()))
        self.assertFalse(self.context()["context"]["coaching_note"]["practice_recommendation"]["available"])
        self.assertEqual(self.context()["context"]["practice_focus_options"], [])

    def test_revision_basis_and_note_conflicts_preserve_prior_note(self):
        old = self.prepared()
        params = self.recommendation(old)
        self.reflection()
        self.atomic_error("revision_conflict", context=old, **params)
        self.atomic_error("basis_changed", context=old, expected_revision=self.rev, **params)
        current = self.context()
        self.note(**self.recommendation())
        self.atomic_error("coaching_conflict", request_id="note-race", context=current, expected_revision=self.rev, **params)
        self.reason_review(event_id="new-review", agrees=False)
        note = self.context()["context"]["coaching_note"]
        self.assertTrue(note["stale"])
        self.assertFalse(note["practice_recommendation"]["available"])
        self.assertEqual(note["reasoning_assessment"]["status"], "supported")

    def test_concurrent_optional_notes_have_exactly_one_winner(self):
        view = self.prepared()
        params = dict(attempt_id="attempt", summary="合成说明", next_step="核对", basis_reason_review_id="reason-check",
                      expected_revision=view["revision"], **self.recommendation(view))
        other = self.configure(Core(self.vault))
        other_sid = other.resume_learning()["session_id"]
        barrier, results = threading.Barrier(2), []
        def run(core, sid, request_id):
            barrier.wait(timeout=5)
            try:
                results.append(core.save_coaching_note(session_id=sid, request_id=request_id, **params))
            except CoreError as error:
                results.append(error.result())
        threads = [threading.Thread(target=run, args=(core, sid, request_id)) for core, sid, request_id in
                   ((self.core, self.sid, "note-one"), (other, other_sid, "note-two"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual([result["error"]["code"] for result in results if not result["ok"]], ["revision_conflict"])
        self.assertEqual(sum(row["type"] == "coaching_note" for row in self.snapshot()["events"]), 1)

    def test_legacy_digest_and_null_additions_remain_identical_and_replay_first(self):
        view = self.prepared()
        first = self.note()
        payload = dict(attempt_id="attempt", summary="本次辅导说明", next_step="重新说明依据", open_question="",
                       hypothesis="", base_note_id=None, basis_reflection_id=None, basis_reason_review_id="reason-check")
        expected = sha(canonical({"operation": "save_coaching_note", "payload": payload}).encode())
        with self.core.connection() as db:
            self.assertEqual(db.execute("SELECT digest FROM requests WHERE id='note'").fetchone()[0], expected)
        event = self.snapshot()["events"][-1]
        self.assertNotIn("reasoning_assessment", event)
        self.assertNotIn("practice_recommendation", event)
        self.focus_path.unlink()
        self.save(dict(id="invalid", type="retract", targetId="attempt", reason="合成撤销"))
        self.core.items["shared"]["version"] = "new-version"
        replay = self.note(context=view, expected_revision=0, reasoning_assessment=None,
                           practice_focus_id=None, practice_focus_version=None)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], first["revision"])

    def test_new_replay_precedes_metadata_basis_source_changes_and_backup_roundtrip(self):
        view = self.prepared()
        params = self.recommendation(view)
        first = self.note(**params)
        self.core.backup(self.root / "backup")
        Core.restore(self.root / "backup", self.root / "restored")
        restored = self.configure(Core(self.root / "restored"))
        sid = restored.resume_learning()["session_id"]
        replay = restored.save_coaching_note(sid, 0, "note", "attempt", "本次辅导说明", "重新说明依据",
                                             basis_reason_review_id="reason-check", **params)
        self.assertTrue(replay["replayed"])
        self.focus_path.unlink()
        self.reason_review(event_id="new-review", agrees=False)
        self.save(dict(id="invalid", type="retract", targetId="attempt", reason="合成撤销"))
        self.core.items["shared"]["version"] = "new-version"
        before = self.snapshot()
        replay = self.note(context=view, expected_revision=0, **params)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], first["revision"])
        self.atomic_error("id_conflict", context=view, **{**params, "practice_focus_version": "0" * 64})
        self.assertEqual(self.snapshot(), before)

    def test_previous_note_is_historical_and_recommendation_cannot_be_adopted(self):
        self.prepared()
        self.note(**self.recommendation())
        view = self.context()
        result = self.core.start_review_practice(self.sid, view["revision"], "redo", "attempt", "v1-shared",
                                                base_note_id="note", basis_reason_review_id="reason-check")
        self.rev = result["revision"]
        self.attempt("redo-attempt", "shared", task_id="redo")
        context = self.context("redo-attempt")["context"]
        self.assertIsNone(context["coaching_note"])
        previous = context["previous_coaching_note"]
        self.assertTrue(previous["historical"])
        self.assertFalse(previous["practice_recommendation"]["available"])
        self.assertEqual(previous["reasoning_assessment"]["reason_quote"], "本人作答依据")

    def test_lists_and_resume_are_bounded_without_duplicate_reason_quotes(self):
        for index, item_id in enumerate(("shared", "extra", "coarse")):
            attempt_id = "bounded-" + str(index)
            quote = "QUOTE_ONLY_" + str(index) + "中" * 480
            self.attempt(attempt_id, item_id, reason=quote)
            self.reason_review(attempt_id, "review-" + str(index))
            view = self.context(attempt_id)
            self.note("note-" + str(index), attempt_id, reasoning_assessment=self.assessment(quote=quote),
                      practice_focus_id=view["context"]["practice_focus_options"][0]["id"],
                      practice_focus_version=view["context"]["practice_focus_version"],
                      summary="说" * 2000, hypothesis="假" * 1000, next_step="步" * 1200, open_question="问" * 1200)
        for row in self.queue(include_checked=True)["entries"]:
            self.assertNotIn("reason_quote", row["coaching_note"]["reasoning_assessment"])
        resumed = self.core.resume_learning()
        self.assertLess(len(json.dumps(resumed, ensure_ascii=False).encode()), 20000)
        encoded = json.dumps(resumed["review_queue"], ensure_ascii=False).encode()
        self.assertLess(len(encoded), 20000)
        self.assertNotIn(b"QUOTE_ONLY_", encoded)
        self.assertNotIn(b"reason_quote", encoded)
        self.assertNotIn(b"SECRET_", encoded)
        self.assertLessEqual(len(self.context("bounded-0")["context"]["coaching_note"]["reasoning_assessment"]["reason_quote"]), 500)


if __name__ == "__main__":
    unittest.main()

"""Bounded Beta5 tests using only disposable synthetic vaults and local media."""
import json
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import test_practice_loop as fixtures
from eju_core.errors import CoreError
from eju_core.evidence import evidence_map
from eju_core.review_loop import ReviewLoopMixin
from eju_core.store import Core


if issubclass(Core, ReviewLoopMixin):
    ReviewCore = Core
else:
    class ReviewCore(ReviewLoopMixin, fixtures.LoopCore):
        pass


class ReviewLoopTests(unittest.TestCase):
    item = fixtures.PracticeLoopTests.item
    configure = fixtures.PracticeLoopTests.configure
    save = fixtures.PracticeLoopTests.save
    attempt = fixtures.PracticeLoopTests.attempt
    append = fixtures.PracticeLoopTests.append
    snapshot = fixtures.PracticeLoopTests.snapshot
    assert_error = fixtures.PracticeLoopTests.assert_error

    def setUp(self):
        fixtures.PracticeLoopTests.setUp(self)
        self.core = self.configure(ReviewCore(self.vault))

    def context(self, attempt_id="attempt"):
        return self.core.read_coaching_context(self.sid, attempt_id)

    def note(self, request_id="note", attempt_id="attempt", context=None, **overrides):
        view = context or self.context(attempt_id)
        context = view["context"]
        params = dict(session_id=self.sid, expected_revision=view["revision"], request_id=request_id,
                      attempt_id=attempt_id, summary="本次辅导说明", next_step="重新说明依据",
                      base_note_id=context["base_note_id"], basis_reflection_id=context["basis_reflection_id"],
                      basis_reason_review_id=context["basis_reason_review_id"])
        params.update(overrides)
        receipt = self.core.save_coaching_note(**params)
        self.rev = receipt.get("current_revision", receipt["revision"])
        return receipt

    def start(self, view=None, request_id="redo", **overrides):
        view = view or self.context()
        context = view["context"]
        params = dict(session_id=self.sid, expected_revision=view["revision"], request_id=request_id,
                      source_attempt_id=context["attempt"]["attempt_id"],
                      item_version=context["attempt"]["item_version"], base_task_id=context["base_task_id"],
                      base_note_id=context["base_note_id"], basis_reflection_id=context["basis_reflection_id"],
                      basis_reason_review_id=context["basis_reason_review_id"])
        params.update(overrides)
        receipt = self.core.start_review_practice(**params)
        self.rev = receipt.get("current_revision", receipt["revision"])
        return receipt

    def reason_review(self, attempt_id="attempt", event_id="reason-check", agrees=True, result="correct"):
        self.save(dict(id=event_id, type="reason_review", targetId=attempt_id, reviewer="tutor",
                       agrees=agrees, result=result, note="仅核对本次理由"))

    def reflection(self, attempt_id="attempt", event_id="reflection", cause="not_sure", **overrides):
        attempt = next(event for event in self.core.export_json()["events"] if event["id"] == attempt_id)
        self.append(dict(id=event_id, type="reflection", attemptId=attempt_id, itemId=attempt["itemId"],
                         itemVersion=attempt["itemVersion"], task_id=attempt.get("task_id"),
                         cause=cause, evidence="本人认为仍不确定", next_action="核对依据",
                         source="learner_self_report", **overrides))

    def queue(self, **kwargs):
        return self.core.read_review_queue(self.sid, **kwargs)

    def test_context_is_exact_bounded_attempt_read_only_and_validates_ids_session(self):
        self.attempt(answer="LEARNER_ANSWER", reason="LEARNER_REASON")
        self.attempt("unrelated-attempt", "extra", answer="OTHER_PRIVATE_ANSWER")
        before = self.snapshot()
        view = self.context()
        with self.core.connection() as db:
            events = self.core.events(db)
        source = next(event for event in events if event["id"] == "attempt")
        self.assertEqual(view["context"]["attempt"], self.core._study_review(events, source))
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(view["context"]["review_available"])
        self.assertIsNone(view["context"]["coaching_note"])
        encoded = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("SECRET_", encoded)
        self.assertNotIn("OTHER_PRIVATE_ANSWER", encoded)
        self.assert_error("invalid_id", self.core.read_coaching_context, self.sid, "bad id!")
        self.assert_error("attempt_missing", self.context, "missing")
        self.assert_error("resume_required", self.core.read_coaching_context, "unknown-session", "attempt")

    def test_note_is_append_only_attributed_and_does_not_change_evidence_profile(self):
        self.attempt()
        self.reason_review()
        self.reflection()
        before = self.snapshot()
        evidence = evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"])
        receipt = self.note(hypothesis="这是辅导者假设", open_question="还需核对哪一步？")
        self.assertEqual((receipt["coaching_note_id"], receipt["source_attempt_id"]), ("note", "attempt"))
        after = self.snapshot()
        self.assertEqual(after["events"][:-1], before["events"])
        self.assertEqual(after["profile"], before["profile"])
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]), evidence)
        note = after["events"][-1]
        self.assertEqual((note["type"], note["source"], note["sessionId"]), ("coaching_note", "tutor_summary", self.sid))
        self.assertEqual((note["attemptId"], note["itemId"], note["itemVersion"]), ("attempt", "source", "v1-source"))
        self.assertEqual((note["basis_reflection_id"], note["basis_reason_review_id"]), ("reflection", "reason-check"))
        with self.core.connection() as db:
            self.assertEqual(db.execute("SELECT origin FROM events WHERE id='note'").fetchone()[0], "native_codex")
        self.note("note-two", summary="更新说明")
        self.assertEqual(self.core.export_json()["events"][-1]["previous_id"], "note")
        self.assertEqual(self.core.export_json()["events"][-2], note)
        self.assertFalse(any(event["type"] in ("exposure", "task") for event in after["events"]))

    def test_note_strict_text_limits_and_atomic_failures(self):
        self.attempt()
        for field, value in (("summary", ""), ("summary", "  "), ("summary", "x" * 2001),
                             ("next_step", ""), ("next_step", "x" * 1201),
                             ("open_question", "x" * 1201), ("hypothesis", "x" * 1001),
                             ("summary", None), ("next_step", True), ("open_question", [])):
            with self.subTest(field=field, length=len(value) if isinstance(value, str) else type(value).__name__):
                before = self.snapshot()
                self.assert_error("invalid_field", self.note, **{field: value})
                self.assertEqual(self.snapshot(), before)
        self.note(summary="s" * 2000, next_step="n" * 1200, open_question="o" * 1200, hypothesis="h" * 1000)
        note = self.context()["context"]["coaching_note"]
        self.assertEqual([len(note[key]) for key in ("summary", "next_step", "open_question", "hypothesis")],
                         [2000, 1200, 1200, 1000])

    def test_exact_note_basis_conflicts_stale_and_immutable_history(self):
        self.attempt()
        empty = self.context()
        self.note()
        before = self.snapshot()
        self.assert_error("coaching_conflict", self.note, "conflict", context=empty, expected_revision=self.rev)
        self.assertEqual(self.snapshot(), before)
        view = self.context()
        self.reason_review()
        self.assertTrue(self.context()["context"]["coaching_note"]["stale"])
        self.assert_error("basis_changed", self.note, "basis-conflict", context=view, expected_revision=self.rev)
        self.assert_error("basis_changed", self.note, "null-basis", basis_reason_review_id=None)
        self.note("note-two")
        self.assertFalse(self.context()["context"]["coaching_note"]["stale"])
        self.reflection()
        self.assertTrue(self.context()["context"]["coaching_note"]["stale"])
        self.assertIn("复盘", self.context()["context"]["coaching_note"]["stale_reason"])
        self.assertEqual([event["id"] for event in self.core.export_json()["events"] if event["type"] == "coaching_note"],
                         ["note", "note-two"])

    def test_invalid_linked_reflections_and_judgments_are_not_note_basis(self):
        self.attempt()
        self.reason_review()
        self.reflection()
        self.append(dict(id="bad-reflection", type="reflection", attemptId="attempt", itemId="source",
                         itemVersion="wrong-version", cause="not_sure", evidence="invalid", source="learner_self_report"))
        self.append(dict(id="bad-review", type="reason_review", targetId="attempt", reviewer="inferred",
                         agrees=True, result="correct", note="not a valid judgment"))
        context = self.context()["context"]
        self.assertEqual((context["basis_reflection_id"], context["basis_reason_review_id"]), ("reflection", "reason-check"))
        self.assertEqual(context["attempt"]["reason_review"]["id"], "reason-check")
        self.assertTrue(any(event["id"] == "bad-review" for event in self.core.export_json()["events"]))
        self.note()
        self.assertEqual(self.queue(include_checked=True)["entries"][0]["reason_review"]["id"], "reason-check")

    def test_invalid_sources_remain_readable_and_refuse_save_or_start(self):
        for index, invalidation in enumerate(("retract", "disputed", "corrected", "version", "missing", "unreviewed")):
            with self.subTest(invalidation=invalidation):
                item_id, attempt_id = "invalid-" + str(index), "invalid-attempt-" + str(index)
                item = self.item(item_id)
                self.core.items[item_id] = item
                self.attempt(attempt_id, item_id)
                if invalidation == "retract":
                    self.save(dict(id="retract-invalid", type="retract", targetId=attempt_id, reason="合成撤销"))
                elif invalidation in ("disputed", "corrected"):
                    issue_id = "issue-" + str(index)
                    self.save(dict(id=issue_id, type="issue", itemId=item_id, reason="合成争议"))
                    if invalidation == "corrected":
                        self.save(dict(id="correct-invalid", type="resolve", itemId=item_id, issueId=issue_id,
                                       reference="合成核验", explanation="合成更正", outcome="corrected"))
                elif invalidation == "version":
                    item["version"] = "v2-invalid"
                elif invalidation == "missing":
                    del self.core.items[item_id]
                else:
                    item["status"] = "indexed"
                view = self.context(attempt_id)
                self.assertTrue(view["context"]["attempt"]["invalid_reason"])
                self.assertFalse(view["context"]["review_available"])
                before = self.snapshot()
                self.assert_error("source_invalid", self.note, "invalid-note-" + str(index), attempt_id, view)
                self.assert_error("item_version_conflict" if invalidation == "version" else "source_invalid",
                                  self.start, view, "invalid-redo-" + str(index))
                self.assertEqual(self.snapshot(), before)

    def test_queue_chooses_latest_before_cursor_and_never_resurrects_old_valid_attempt(self):
        self.attempt("old", "source")
        old_seq = self.queue()["entries"][0]["seq"]
        self.attempt("latest", "source")
        self.save(dict(id="retract-latest", type="retract", targetId="latest", reason="合成撤销"))
        queue = self.queue()
        self.assertEqual([(row["attempt_id"], row["status"]) for row in queue["entries"]], [("latest", "unavailable")])
        self.assertEqual(self.queue(before=old_seq + 1)["entries"], [])
        self.assertEqual(queue["unavailable_count"], 1)

    def test_stable_seq_pagination_after_new_inserts_and_filtered_has_more(self):
        for index, item_id in enumerate(("source", "shared", "coarse", "extra")):
            self.attempt("a" + str(index), item_id)
        first = self.queue(limit=2)
        self.assertEqual([row["attempt_id"] for row in first["entries"]], ["a3", "a2"])
        self.assertTrue(first["has_more"])
        item = self.item("new-after-page")
        self.core.items[item["id"]] = item
        self.attempt("new-insert", item["id"])
        self.attempt("replacement-for-a1", "shared")
        second = self.queue(before=first["next_before"], limit=2)
        self.assertEqual([row["attempt_id"] for row in second["entries"]], ["a0"])
        self.assertFalse(second["has_more"])
        self.assertFalse(set(row["attempt_id"] for row in first["entries"]).intersection(row["attempt_id"] for row in second["entries"]))
        self.assertEqual(self.queue(before=second["next_before"])["next_before"], 0)

    def test_subject_filters_counts_and_checked_visibility_are_independent(self):
        self.core.items["extra"]["subjects"] = ["integrated"]
        self.attempt()
        self.reason_review()
        self.attempt("pending", "shared", result="incorrect")
        self.attempt("other-subject", "extra")
        self.save(dict(id="other-issue", type="issue", itemId="extra", reason="合成问题"))
        queue = self.queue(subject="math1")
        self.assertEqual((queue["pending_count"], queue["checked_count"], queue["unavailable_count"]), (1, 1, 0))
        self.assertEqual([row["attempt_id"] for row in queue["entries"]], ["pending"])
        all_math = self.queue(subject="math1", include_checked=True)
        self.assertEqual([row["status"] for row in all_math["entries"]], ["pending", "checked"])
        other = self.queue(subject="integrated")
        self.assertEqual((other["pending_count"], other["checked_count"], other["unavailable_count"]), (0, 0, 1))
        self.assertEqual(self.queue(before=1)["checked_count"], 1)
        self.assertEqual(self.queue()["pending_count"], 1)

    def test_answer_reason_reflection_and_tutor_summary_have_separate_meanings(self):
        self.attempt(result="incorrect")
        self.reason_review(result="correct")
        self.note()
        row = self.queue()["entries"][0]
        self.assertEqual(row["result"], "incorrect")
        self.assertIn("answer_incorrect", [reason["code"] for reason in row["reasons"]])
        self.attempt("ungraded", "shared", result="ungraded", seenBefore=True, hint="H2")
        self.reason_review("ungraded", "ungraded-check", result="correct")
        row = self.queue(include_checked=True)["entries"][0]
        self.assertEqual((row["result"], row["status"]), ("ungraded", "checked"))
        self.assertIn("不证明独立迁移", row["reasons"][0]["label"])
        self.reflection("ungraded", "ungraded-reflection")
        self.assertEqual(self.queue()["entries"][0]["status"], "pending")
        self.note("ungraded-note", "ungraded")
        self.assertEqual(self.queue(include_checked=True)["entries"][0]["status"], "checked")
        self.note("open-note", "ungraded", open_question="还需解释这个问题")
        self.assertIn("open_question", [reason["code"] for reason in self.queue()["entries"][0]["reasons"]])
        self.reason_review("ungraded", "second-check", agrees=False)
        reasons = [reason["code"] for reason in self.queue()["entries"][0]["reasons"]]
        self.assertTrue({"answer_incorrect", "reason_disagrees", "coaching_stale", "reflection_open", "open_question"}.issubset(reasons))

    def test_independent_ok_reflection_needs_no_note_but_reason_review_remains_required(self):
        self.attempt(seenBefore=True, hint="H4", guessed=True)
        self.reflection(cause="independent_ok")
        self.assertEqual([reason["code"] for reason in self.queue()["entries"][0]["reasons"]], ["reason_unreviewed"])
        self.reason_review()
        self.assertEqual(self.queue()["entries"], [])
        self.assertEqual(self.queue()["checked_count"], 1)

    def test_historical_valid_note_can_save_but_cannot_replace_latest_queue_or_start(self):
        self.attempt("old", "source")
        self.note("old-note", "old")
        self.attempt("new", "source")
        old = self.context("old")
        self.assertFalse(old["context"]["review_available"])
        self.assertIn("source_changed", old["context"]["review_unavailable_reason"])
        self.note("old-note-updated", "old", summary="历史说明仍可保存")
        row = self.queue()["entries"][0]
        self.assertEqual(row["attempt_id"], "new")
        self.assertIsNone(row["coaching_note"])
        self.assert_error("source_changed", self.start, self.context("old"))

    def test_missing_media_unreviewed_and_stale_context_refuse_start_atomically(self):
        self.attempt(item_id="shared")
        view = self.context()
        (self.media / "shared.png").unlink()
        self.assertFalse(self.context()["context"]["review_available"])
        before = self.snapshot()
        self.assert_error("material_unavailable", self.start, view)
        self.assertEqual(self.snapshot(), before)
        self.item("shared")  # Restore the exact synthetic image.
        self.assert_error("item_version_conflict", self.start, view, item_version="wrong-version")
        self.reason_review()
        self.assert_error("revision_conflict", self.start, view)
        self.assert_error("basis_changed", self.start, view, expected_revision=self.rev)
        fresh = self.context()
        self.note()
        self.assert_error("coaching_conflict", self.start, fresh, expected_revision=self.rev)
        self.assertFalse(any(event["type"] == "task" for event in self.core.export_json()["events"]))

    def test_start_is_explicit_empty_old_question_task_and_exposure_is_separate(self):
        self.attempt(answer="OLD_LEARNER_ANSWER", reason="OLD_LEARNER_REASON")
        self.reason_review()
        self.note(summary="旧题辅导说明")
        before = self.snapshot()
        evidence = evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"])
        self.assertEqual(self.queue()["checked_count"], 1)
        receipt = self.start()
        self.assertEqual((receipt["practice_mode"], receipt["learner_exposure_recorded"]), ("review", False))
        after = self.snapshot()
        self.assertEqual(after["events"][:-1], before["events"])
        self.assertEqual(after["profile"], before["profile"])
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]), evidence)
        task = self.core.task_state(after["events"])
        self.assertEqual((task["answer"], task["reason"], task["exposure"], task["practice_mode"]), ("", "", "none", "review"))
        self.assertEqual((task["review_source_attempt_id"], task["base_note_id"], task["basis_reason_review_id"]),
                         ("attempt", "note", "reason-check"))
        self.assertNotIn("OLD_LEARNER", json.dumps(after["events"][-1]))
        self.assertFalse(any(event["type"] == "exposure" for event in after["events"]))
        shown = self.core.workbench_action(self.sid, self.rev, "redo-shown", "redo", "show")
        self.rev = shown["revision"]
        submitted = self.core.workbench_action(self.sid, self.rev, "redo-submitted", "redo", "submit",
                                               dict(answer="1", reason="本次重新说明"))
        self.rev = submitted["revision"]
        events = self.core.export_json()["events"]
        attempt = next(event for event in events if event["id"] == submitted["attempt_id"])
        self.assertTrue(attempt["seenBefore"])
        self.assertEqual(self.queue()["entries"][0]["attempt_id"], submitted["attempt_id"])
        current = self.context(submitted["attempt_id"])["context"]
        self.assertIsNone(current["coaching_note"])
        self.assertIsNone(current["basis_reason_review_id"])
        self.assertEqual(current["previous_attempt_id"], "attempt")
        self.assertTrue(current["previous_coaching_note"]["historical"])
        self.assertEqual(current["previous_coaching_note"]["id"], "note")
        self.assertEqual(self.queue()["entries"][0]["reasons"][0]["code"], "reason_unreviewed")

    def test_previous_explanation_requires_same_item_version_and_is_single_hop(self):
        self.attempt()
        self.note()
        self.append(dict(id="foreign-task", type="task", itemId="shared", itemVersion="v1-shared",
                         practice_mode="review", review_source_attempt_id="attempt"))
        self.attempt("foreign-attempt", "shared", task_id="foreign-task")
        context = self.context("foreign-attempt")["context"]
        self.assertIsNone(context["previous_attempt_id"])
        self.assertIsNone(context["previous_coaching_note"])
        self.append(dict(id="bad-version-task", type="task", itemId="source", itemVersion="v0-source",
                         practice_mode="review", review_source_attempt_id="attempt"))
        self.append(dict(id="bad-version-attempt", type="attempt", itemId="source", itemVersion="v0-source",
                         task_id="bad-version-task", answer="old", reason="old"))
        self.assertIsNone(self.context("bad-version-attempt")["context"]["previous_attempt_id"])
        self.append(dict(id="mismatched-task", type="task", itemId="shared", itemVersion="v1-shared",
                         practice_mode="review", review_source_attempt_id="attempt"))
        self.attempt("mismatched-attempt", "source", task_id="mismatched-task")
        self.assertIsNone(self.context("mismatched-attempt")["context"]["previous_attempt_id"])

    def test_active_paused_and_changed_task_guards_leave_no_partial_events(self):
        self.attempt()
        self.start()
        before = self.snapshot()
        self.assert_error("task_changed", self.start, request_id="changed-task", base_task_id=None)
        self.assert_error("task_active", self.start, request_id="active-task")
        self.assertEqual(self.snapshot(), before)
        paused = self.core.workbench_action(self.sid, self.rev, "pause-redo", "redo", "pause")
        self.rev = paused["revision"]
        before = self.snapshot()
        self.assert_error("task_paused", self.start, request_id="paused-task")
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_starts_have_one_winner_and_no_exposure(self):
        self.attempt()
        view = self.context()
        other = self.configure(ReviewCore(self.vault))
        other_sid = other.resume_learning()["session_id"]
        barrier, results = threading.Barrier(2), []
        def run(core, session_id, request_id):
            barrier.wait(timeout=5)
            try:
                results.append(core.start_review_practice(session_id, view["revision"], request_id, "attempt", "v1-source"))
            except CoreError as error:
                results.append(error.result())
        threads = [threading.Thread(target=run, args=(self.core, self.sid, "client-one")),
                   threading.Thread(target=run, args=(other, other_sid, "client-two"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(result["ok"] for result in results), 1)
        self.assertEqual([result["error"]["code"] for result in results if not result["ok"]], ["revision_conflict"])
        events = self.core.export_json()["events"]
        self.assertEqual(sum(event["type"] == "task" for event in events), 1)
        self.assertFalse(any(event["type"] == "exposure" for event in events))

    def test_note_and_start_replay_precede_changed_basis_source_and_later_state(self):
        self.attempt()
        empty = self.context()
        first_note = self.note(context=empty)
        noted = self.context()
        first_start = self.start(noted)
        self.reason_review()
        self.note("later-note")
        self.save(dict(id="later-retract", type="retract", targetId="attempt", reason="后续撤销"))
        self.core.items["source"]["version"] = "v2-source"
        new_sid = self.core.resume_learning()["session_id"]
        replay_note = self.note(context=empty, session_id=new_sid, expected_revision=0)
        replay_start = self.start(noted, session_id=new_sid, expected_revision=0)
        self.assertTrue(replay_note["replayed"])
        self.assertTrue(replay_start["replayed"])
        self.assertEqual(replay_note["revision"], first_note["revision"])
        self.assertEqual(replay_start["revision"], first_start["revision"])
        self.assertGreater(replay_start["current_revision"], replay_start["revision"])
        self.assert_error("id_conflict", self.note, context=empty, summary="不同内容", session_id=new_sid)
        self.assert_error("id_conflict", self.start, noted, item_version="other-version", session_id=new_sid)
        events = self.core.export_json()["events"]
        self.assertEqual(sum(event["type"] == "task" for event in events), 1)
        self.assertEqual(sum(event["id"] == "note" for event in events), 1)

    def test_backup_restore_new_core_and_session_preserve_notes_tasks_and_replays(self):
        self.attempt()
        self.note()
        noted = self.context()
        self.start(noted)
        before = self.snapshot()
        queue = self.queue()
        self.core.backup(self.root / "backup")
        ReviewCore.restore(self.root / "backup", self.root / "restored")
        restored = self.configure(ReviewCore(self.root / "restored"))
        self.assertEqual(self.snapshot(restored), before)
        sid = restored.resume_learning()["session_id"]
        self.assertEqual(restored.read_review_queue(sid)["entries"], queue["entries"])
        self.assertEqual(restored.read_coaching_context(sid, "attempt")["context"], self.context()["context"])
        note = restored.save_coaching_note(sid, 0, "note", "attempt", "本次辅导说明", "重新说明依据")
        start = restored.start_review_practice(sid, 0, "redo", "attempt", "v1-source", base_note_id="note")
        self.assertTrue(note["replayed"])
        self.assertTrue(start["replayed"])
        self.assertEqual(sum(event["type"] == "coaching_note" for event in restored.export_json()["events"]), 1)

    def test_queue_summary_and_list_are_bounded_without_prior_answers_or_material(self):
        for index, item_id in enumerate(("source", "shared", "coarse", "extra")):
            self.core.items[item_id]["displayTitle"] = "标" * 500
            attempt_id = "bounded-" + str(index)
            self.attempt(attempt_id, item_id, answer="LEARNER_SECRET_ANSWER" * 100, reason="LEARNER_SECRET_REASON" * 100)
            self.note("bounded-note-" + str(index), attempt_id, summary="s" * 2000, next_step="n" * 1200,
                      open_question="o" * 1200, hypothesis="h" * 1000)
        before = self.snapshot()
        queue = self.queue()
        summary = self.core.review_queue_summary(before["events"])
        self.assertEqual(len(summary["entries"]), 3)
        self.assertEqual(summary["pending_count"], 4)
        for row in queue["entries"]:
            self.assertNotIn("answer", row)
            self.assertNotIn("reason", row)
        for row in summary["entries"]:
            self.assertLessEqual(len(row["title"]), 200)
            self.assertLessEqual(len(row["coaching_note"]["summary"]), 350)
            for key in ("next_step", "open_question", "hypothesis"):
                self.assertLessEqual(len(row["coaching_note"][key]), 250)
        encoded = json.dumps({"queue": queue, "summary": summary}, ensure_ascii=False)
        self.assertNotIn("LEARNER_SECRET", encoded)
        self.assertNotIn("SECRET_", encoded)
        self.assertLess(len(json.dumps(summary, ensure_ascii=False).encode()), 20000)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.core.review_queue_summary(before["events"], limit=1)["pending_count"], 4)

    def test_queue_input_validation_is_strict_and_read_only(self):
        self.attempt()
        before = self.snapshot()
        for kwargs in (dict(limit=0), dict(limit=21), dict(limit=True), dict(before=-1), dict(before=True),
                       dict(include_checked=1), dict(include_checked="false")):
            self.assert_error("invalid_field", self.queue, **kwargs)
        self.assert_error("invalid_subject", self.queue, subject="unknown")
        self.assert_error("invalid_field", self.core.review_queue_summary, [], 4)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

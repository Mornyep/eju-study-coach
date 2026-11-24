"""Writing tests. Every vault, draft and media file is disposable synthetic data."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eju_core.errors import CoreError
from eju_core.evidence import evidence_map
from eju_core.store import Core, stamp, today
from test_practice_loop import png


class WritingFixtures:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        Core.initialize(self.vault)
        self.catalog = {"subjects": {"japanese_writing": "日语记述", "math1": "数学"},
                        "nodes": [{"id": "J.writing", "label": "记述"}], "items": [self.item()]}
        self.core = self.configure(Core(self.vault))
        resume = self.core.resume_learning()
        self.sid, self.rev = resume["session_id"], resume["revision"]

    @staticmethod
    def item(item_id="writing", **overrides):
        return dict(id=item_id, version="v1-" + item_id, kind="original", status="reviewed_original",
                    subjects=["japanese_writing"], nodes=["J.writing"], order=1,
                    title="合成写作任务", displayTitle="合成记述", stem="二つの方法の利点に触れ、自分の考えと理由を書いてください。",
                    answer="SECRET_ANSWER", solution="SECRET_SOLUTION", grading={"mode": "manual"}, **overrides)

    def configure(self, core):
        core.catalog = copy.deepcopy(self.catalog)
        core.items = {item["id"]: item for item in core.catalog["items"]}
        core.coaching = {"writing": {"questionFocus": "SECRET_COACH"}}
        return core

    def snapshot(self, core=None):
        data = (core or self.core).export_json()
        data.pop("exported_at")
        with (core or self.core).connection() as db:
            data["requests"] = [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY id")]
        return data

    def assert_error(self, code, method, *args, **kwargs):
        with self.assertRaises(CoreError) as caught:
            method(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def draft_params(self, request_id="draft", text="私は同じ仲間と学ぶ方がよい。安心して話せるからだ。", draft_id=None, **overrides):
        item = self.core.items["writing"]
        params = dict(session_id=self.sid, expected_revision=self.rev, request_id=request_id,
                      item_id=item["id"], item_version=item["version"], text=text,
                      source_fingerprint=self.core._writing_fingerprint(item))
        if draft_id:
            context = self.core.read_writing_context(self.sid, draft_id)["context"]
            params.update(draft_id=draft_id, base_version_id=context["base_version_id"],
                          source_fingerprint=context["source_fingerprint"], item_version=context["item_version"])
        return {**params, **overrides}

    def draft(self, request_id="draft", text="私は同じ仲間と学ぶ方がよい。安心して話せるからだ。", draft_id=None, **overrides):
        result = self.core.save_writing_draft(**self.draft_params(request_id, text, draft_id, **overrides))
        self.rev = result.get("current_revision", result["revision"])
        return result

    def feedback_params(self, request_id="feedback", draft_id="draft", **overrides):
        context = self.context(draft_id)
        source = context["latest_version"]
        old = context["current_feedback"]
        return dict(session_id=self.sid, expected_revision=self.rev, request_id=request_id, draft_id=draft_id,
                    source_version_id=source["id"], task_response="partial", task_evidence_quote=source["text"][:40],
                    task_comment="二つの方法の利点を扱う必要がある。", issues=[{
                        "category": "content", "quote": source["text"][:20], "request": "もう一方の方法の利点を一文で補ってください。"}],
                    base_feedback_id=old["id"] if old else None) | overrides

    def feedback(self, request_id="feedback", draft_id="draft", **overrides):
        result = self.core.save_writing_feedback(**self.feedback_params(request_id, draft_id, **overrides))
        self.rev = result.get("current_revision", result["revision"])
        return result

    def context(self, draft_id="draft", **kwargs):
        return self.core.read_writing_context(self.sid, draft_id, **kwargs)["context"]

    def seed(self, event):
        with self.core.connection(write=True) as db:
            self.core.insert_event(db, dict(createdAt=stamp(), date=today(), sessionId=self.sid, **event), "synthetic_fixture")
            self.rev = self.core.finish_request(db, event["id"], "fixture")["revision"]


class WritingLoopTests(WritingFixtures, unittest.TestCase):
    def test_default_catalog_writing_candidates_preserve_origin_without_answers(self):
        core = Core(self.vault)
        view = core.read_writing_drafts(self.sid)
        originals = [item for item in view["available_items"] if item["kind"] == "original"]
        self.assertEqual({item["item_id"] for item in originals},
                         {"CG-JW-F", "CG-JW-C", "CG-JW-T", "CG-JW-D"})
        self.assertTrue(all(item["practice_label"] == "原创辅助练习" for item in originals))
        self.assertEqual([item["role"] for item in originals[:2]], ["transfer", "delayed"])
        kinds = [item["kind"] for item in view["available_items"]]
        self.assertEqual(kinds, sorted(kinds, key=lambda kind: kind != "official"))
        self.assertTrue(all(item["images"] and item["images_required"] for item in view["available_items"]
                            if item["kind"] == "official"))
        self.assertNotIn("solution", json.dumps(view))

    def test_read_only_list_and_context_do_not_expose_unrelated_private_data(self):
        self.draft()
        self.draft("unrelated", text="UNRELATED_PRIVATE_TEXT")
        before = self.snapshot()
        listing = self.core.read_writing_drafts(self.sid, limit=1)
        context = self.context()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(listing["drafts"][0]["draft_id"], "unrelated")
        self.assertEqual(listing["drafts"][0]["latest_excerpt"], "UNRELATED_PRIVATE_TEXT")
        self.assertTrue(listing["has_more"])
        self.assertEqual(self.core.read_writing_drafts(self.sid, before=listing["next_before"])["drafts"][0]["draft_id"], "draft")
        self.assertNotIn("UNRELATED_PRIVATE_TEXT", json.dumps(context))
        self.assertNotIn("SECRET_", json.dumps({"list": listing, "context": context}))
        self.assertEqual(context["practice_label"], "原创辅助练习")

    def test_original_unicode_empty_whitespace_and_boundaries_are_verbatim(self):
        values = ["", " \n\t\r\n", "  私は\r\n祖母の話を聞いた。\tか\u3099・が・𠮷・👩🏽‍💻\u0000 ", "字" * 8000]
        for index, value in enumerate(values):
            self.draft(f"u{index}", value)
            self.assertEqual(self.context(f"u{index}")["original_version"]["text"], value)
            self.assertEqual(self.core.read_writing_drafts(self.sid)["drafts"][0]["latest_excerpt"], value[:80])
        for value in (None, True, 17, ["a"], "字" * 8001, "\ud800"):
            before = self.snapshot()
            self.assert_error("invalid_field", self.draft, "invalid", value)
            self.assertEqual(before, self.snapshot())

    def test_multiple_rounds_comparison_and_all_history_recover_without_mutation(self):
        self.draft(text="原稿：私はAを選ぶ。")
        self.feedback(task_evidence_quote="私はAを選ぶ。", issues=[])
        first = self.snapshot()["events"][:]
        self.draft("revision1", "改稿：私はAを選ぶ。Bにも利点がある。", "draft")
        self.feedback("feedback2", issues=[])
        self.draft("revision2", "最終稿：私はBを選ぶ。自分の考えを変えた。", "draft")
        context = self.context(limit=1)
        self.assertEqual(self.snapshot()["events"][:2], first)
        self.assertEqual(context["original_version"]["text"], "原稿：私はAを選ぶ。")
        self.assertEqual(context["comparison"]["before_version_id"], "revision1")
        self.assertEqual(context["comparison"]["after_version_id"], "revision2")
        self.assertEqual(context["base_version_id"], "revision2")
        self.assertIsNone(context["current_feedback"])
        self.assertTrue(context["feedback_history"][0]["historical"])
        old = self.context(version_id="revision1")
        self.assertEqual(old["comparison"]["before_version_id"], "draft")
        self.assertTrue(old["selected_feedback"]["stale"])
        self.assertEqual(old["latest_version"]["id"], "revision2")
        versions, feedbacks, cursor = [], [], 0
        while True:
            page = self.context(versions_before=cursor, limit=1)
            versions.extend(row["id"] for row in page["versions"])
            if not page["versions_has_more"]: break
            cursor = page["versions_next_before"]
        cursor = 0
        while True:
            page = self.context(feedback_before=cursor, limit=1)
            feedbacks.extend(row["id"] for row in page["feedback_history"])
            if not page["feedback_has_more"]: break
            cursor = page["feedback_next_before"]
        self.assertEqual(versions, ["revision2", "revision1", "draft"])
        self.assertEqual(feedbacks, ["feedback2", "feedback"])

    def test_feedback_strict_quotes_two_points_and_no_scores_or_rewrites(self):
        self.draft()
        params = self.feedback_params()
        invalid = [({"issues": params["issues"] * 3}, "invalid_field"),
                   ({"issues": [{**params["issues"][0], "rewrite": "代写"}]}, "invalid_field"),
                   ({"issues": [{**params["issues"][0], "quote": "捏造引用"}]}, "writing_quote_mismatch"),
                   ({"issues": [{**params["issues"][0], "category": "score"}]}, "invalid_field"),
                   ({"task_response": "mastered"}, "invalid_field"),
                   ({"task_evidence_quote": ""}, "writing_quote_mismatch"),
                   ({"task_evidence_quote": "捏造引用"}, "writing_quote_mismatch"),
                   ({"task_comment": ""}, "invalid_field"),
                   ({"task_comment": "\ud800"}, "invalid_field"),
                   ({"task_comment": "x" * 1001}, "invalid_field"),
                   ({"issues": [{**params["issues"][0], "request": " "}]}, "invalid_field")]
        before = self.snapshot()
        for override, code in invalid:
            self.assert_error(code, self.core.save_writing_feedback, **(params | override))
            self.assertEqual(self.snapshot(), before)
        self.feedback(issues=params["issues"] * 2)
        event = self.snapshot()["events"][-1]
        self.assertEqual(len(event["issues"]), 2)
        self.assertNotIn("score", event)
        self.assertNotIn("rewrite", event)

    def test_zero_issue_feedback_and_empty_draft_only_unclear(self):
        self.draft(text=" \r\n")
        before = self.snapshot()
        self.assert_error("writing_empty", self.feedback, task_response="answered", task_evidence_quote="", issues=[])
        self.assert_error("writing_empty", self.feedback, task_response="unclear", task_evidence_quote=" ", issues=[])
        self.assertEqual(before, self.snapshot())
        self.feedback(task_response="unclear", task_evidence_quote="", issues=[], task_comment="先用自己的话写出立场。")
        self.draft("nonempty", text="私はAがよいと思う。")
        self.feedback("positive", "nonempty", task_response="answered", issues=[], task_comment="这句话已明确表达立场。")
        self.assertEqual(self.context("nonempty")["current_feedback"]["issues"], [])

    def test_same_version_feedback_is_append_only_and_old_feedback_is_historical(self):
        self.draft()
        old_params = self.feedback_params()
        self.feedback()
        self.assert_error("writing_feedback_conflict", self.core.save_writing_feedback,
                          **(old_params | {"request_id": "stale-feedback", "expected_revision": self.rev}))
        self.feedback("corrected-feedback", task_comment="补充明确下一步。")
        context = self.context()
        self.assertEqual(context["current_feedback"]["id"], "corrected-feedback")
        self.assertTrue(context["feedback_history"][1]["historical"])
        self.assertEqual(context["feedback_history"][0]["base_feedback_id"], "feedback")

    def test_stale_revision_base_version_and_wrong_draft_links_are_atomic(self):
        self.draft()
        stale = self.draft_params("revision1", "new", "draft")
        self.draft("other", "private")
        before = self.snapshot()
        self.assert_error("revision_conflict", self.core.save_writing_draft, **stale)
        self.draft("revision2", "newer", "draft")
        before = self.snapshot()
        self.assert_error("writing_version_conflict", self.core.save_writing_draft, **(stale | {"expected_revision": self.rev}))
        self.assert_error("writing_version_missing", self.context, version_id="other")
        self.assert_error("writing_version_conflict", self.feedback, source_version_id="draft")
        self.assert_error("writing_source_conflict", self.draft, "wrong-item", "x", "draft", item_id="other")
        self.assertEqual(before, self.snapshot())

    def test_replay_survives_sessions_revisions_and_missing_source(self):
        args = self.draft_params()
        receipt = self.core.save_writing_draft(**args)
        self.rev = receipt["revision"]
        feedback_args = self.feedback_params()
        feedback_receipt = self.core.save_writing_feedback(**feedback_args)
        self.rev = feedback_receipt["revision"]
        self.draft("revision", "本人改稿", "draft")
        del self.core.items["writing"]
        resumed = self.core.resume_learning()
        for method, params, expected in ((self.core.save_writing_draft, args, receipt),
                                         (self.core.save_writing_feedback, feedback_args, feedback_receipt)):
            replay = method(**(params | {"session_id": resumed["session_id"], "expected_revision": 0}))
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["revision"], expected["revision"])
            self.assertEqual(replay["current_revision"], self.rev)
        self.assert_error("id_conflict", self.core.save_writing_draft, **(args | {"text": "不同"}))
        self.assertEqual(len(self.snapshot()["events"]), 3)

    def test_missing_changed_disputed_source_preserves_history_disables_feedback_and_writes(self):
        self.draft()
        self.feedback()
        original = copy.deepcopy(self.core.items["writing"])
        for mutation, code in ((lambda: self.core.items.pop("writing"), "source_missing"),
                               (lambda: self.core.items["writing"].update(stem="修改但未加版本"), "source_changed"),
                               (lambda: self.core.items["writing"].update(version="new"), "source_changed"),
                               (lambda: self.core.items["writing"].update(status="indexed"), "source_invalid")):
            self.core.items["writing"] = copy.deepcopy(original)
            mutation()
            context = self.context()
            self.assertFalse(context["source_available"])
            self.assertIsNone(context["current_feedback"])
            self.assertTrue(context["selected_feedback"]["stale"])
            self.assertEqual(context["prompt"], original["stem"])
            self.assert_error(code, self.core.save_writing_feedback, **(self.feedback_params("blocked-feedback") | {"base_feedback_id": "feedback"}))
            self.assert_error(code, self.core.save_writing_draft, self.sid, self.rev, "blocked", "writing",
                              original["version"], "本人新稿", "draft", "draft", context["source_fingerprint"])
        self.core.items["writing"] = original
        self.seed(dict(id="issue", type="issue", itemId="writing", note="合成疑问"))
        self.assertFalse(self.context()["source_available"])
        self.assertEqual(self.core.read_writing_drafts(self.sid)["available_items"], [])

    def test_source_fingerprint_required_on_new_and_version_changes_block(self):
        for override, code in (({"source_fingerprint": None}, "source_changed"),
                               ({"source_fingerprint": "0" * 64}, "source_changed"),
                               ({"item_version": "old"}, "item_version_conflict"),
                               ({"base_version_id": "other"}, "invalid_field"),
                               ({"draft_id": "other"}, "invalid_field")):
            before = self.snapshot()
            self.assert_error(code, self.core.save_writing_draft, **(self.draft_params() | override))
            self.assertEqual(self.snapshot(), before)

    def test_official_writing_requires_complete_verified_media_and_missing_media_blocks(self):
        media = self.root / "media"
        media.mkdir()
        image = media / "page.png"
        image.write_bytes(png())
        item = self.core.items["writing"]
        item.update(kind="official", status="reviewed_official", pages=[1], delivery={
            "format": "official_pages", "review": "complete_clean", "source_url": "https://example.invalid/synthetic.pdf",
            "source_sha256": "f" * 64, "assets": [{"path": "page.png", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "width": 1, "height": 1, "pdf_page": 1}]})
        with patch("eju_core.question_media.MEDIA_ROOT", media):
            listing = self.core.read_writing_drafts(self.sid)
            self.assertTrue(listing["available_items"][0]["images_required"])
            self.draft()
            self.feedback()
            image.unlink()
            self.assertEqual(self.core.read_writing_drafts(self.sid)["available_items"], [])
            self.assertFalse(self.context()["ready"])
            self.assert_error("material_unavailable", self.draft, "rev", "x", "draft")
            self.assert_error("material_unavailable", self.feedback, "new-feedback")
            self.assertEqual(self.context()["original_version"]["id"], "draft")

    def test_no_attempt_exposure_profile_mastery_or_fsrs_change(self):
        before = self.snapshot()
        knowledge = evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"])
        self.draft()
        self.feedback(task_response="answered", issues=[])
        self.draft("revision", "改稿", "draft")
        after = self.snapshot()
        self.assertEqual(after["profile"], before["profile"])
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]), knowledge)
        self.assertEqual({event["type"] for event in after["events"]}, {"writing_version", "writing_feedback"})
        self.assertEqual(self.core.resume_learning()["workbench_task"], None)

    def test_transaction_failure_rolls_back_event_receipt_and_revision(self):
        before = self.snapshot()
        with patch.object(self.core, "finish_request", side_effect=CoreError("synthetic_failure", "合成故障")):
            self.assert_error("synthetic_failure", self.draft)
        self.assertEqual(self.snapshot(), before)
        self.draft()
        before = self.snapshot()
        with patch.object(self.core, "finish_request", side_effect=CoreError("synthetic_failure", "合成故障")):
            self.assert_error("synthetic_failure", self.feedback)
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_sessions_only_one_writes_same_revision(self):
        self.draft()
        barrier = threading.Barrier(2)
        result = []
        args = self.draft_params("race0", "并发文本0", "draft")
        def run(index):
            core = self.configure(Core(self.vault))
            session = core.resume_learning()["session_id"]
            barrier.wait(timeout=5)
            try:
                receipt = core.save_writing_draft(**(args | {"session_id": session, "request_id": f"race{index}", "text": f"并发文本{index}"}))
                result.append(receipt["saved"])
            except CoreError as exc:
                result.append(exc.code)
        threads = [threading.Thread(target=run, args=(index,)) for index in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertCountEqual(result, [True, "revision_conflict"])
        self.assertEqual(len(self.snapshot()["events"]), 2)

    def test_snapshot_backup_restore_and_serialized_record_detail_recover_all_text(self):
        text = "か\u3099\r\n" * 1500
        self.draft(text=text)
        self.feedback(issues=[])
        self.draft("revision", "新稿", "draft")
        backup, destination = self.root / "backup", self.root / "restored"
        self.core.backup(backup)
        Core.restore(backup, destination)
        restored = self.configure(Core(destination))
        resume = restored.resume_learning()
        context = restored.read_writing_context(resume["session_id"], "draft")["context"]
        self.assertEqual(context["original_version"]["text"], text)
        self.assertEqual(context["comparison"]["before_text"], text)
        self.assertTrue(context["feedback_history"][0]["historical"])
        offset, parts = 0, []
        while True:
            page = restored.read_record_detail(resume["session_id"], "event", "draft", offset=offset, limit=1000)
            parts.append(page["json_fragment"])
            if not page["has_more"]: break
            offset = page["next_offset"]
        self.assertEqual(json.loads("".join(parts))["text"], text)
        self.assertEqual(self.snapshot(restored)["events"], self.snapshot()["events"])

    def test_read_validation_sessions_and_bounded_candidate_count(self):
        self.draft()
        self.assert_error("resume_required", self.core.read_writing_drafts, "no-session")
        self.assert_error("writing_missing", self.context, "missing")
        for value in (True, 0, 21, "2"):
            self.assert_error("invalid_field", self.core.read_writing_drafts, self.sid, limit=value)
        for value in (-1, True, "0"):
            self.assert_error("invalid_field", self.core.read_writing_context, self.sid, "draft", versions_before=value)
        self.core.items.update({f"extra{n}": self.item(f"extra{n}") for n in range(25)})
        listing = self.core.read_writing_drafts(self.sid)
        self.assertEqual(len(listing["available_items"]), 20)
        self.assertTrue(listing["available_items_truncated"])
        self.assertEqual(listing["available_item_count"], 26)


if __name__ == "__main__":
    unittest.main()

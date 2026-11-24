"""Writing continuation, snapshot and authenticated media tests on synthetic data."""
import copy
import hashlib
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from test_writing_loop import WritingFixtures
from test_practice_loop import png
import test_review_loop as reviews
from eju_core.http_server import make_server
from eju_core.store import Core, bound_resume


class WritingContinuityTests(WritingFixtures, unittest.TestCase):
    def summary(self, **kwargs):
        return self.core.read_writing_resume(self.sid, **kwargs)

    def test_empty_existing_schema_readonly_session_and_strict_bounds(self):
        before = self.snapshot()
        with self.core.connection() as db:
            schema = list(db.execute("SELECT sql FROM sqlite_master ORDER BY name"))
            session_count = db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        self.assertEqual(self.summary(), dict(ok=True, revision=0, drafts=[], draft_count=0, has_more=False))
        self.assertEqual(self.core.resume_learning()["writing_resume"], dict(drafts=[], draft_count=0, has_more=False))
        self.assert_error("resume_required", self.core.read_writing_resume, "unknown-session")
        for value in (0, 11, True, "3", None, -1):
            self.assert_error("invalid_field", self.summary, limit=value)
        self.assertEqual(self.snapshot(), before)
        with self.core.connection() as db:
            self.assertEqual(list(db.execute("SELECT sql FROM sqlite_master ORDER BY name")), schema)
            # resume's existing session registration is the only durable change.
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], session_count + 1)

    def test_recent_activity_uses_version_feedback_seq_not_creation_or_wall_clock(self):
        self.draft("old")
        self.draft("new")
        with patch("eju_core.store.stamp", return_value="2001-01-01T00:00:00+00:00"):
            self.feedback("old-feedback", "old")
        summary = self.summary()
        self.assertEqual([row["draft_id"] for row in summary["drafts"]], ["old", "new"])
        self.assertEqual(summary["drafts"][0]["last_activity_at"], "2001-01-01T00:00:00+00:00")
        self.assertEqual(summary["drafts"][0]["activity_seq"], 3)
        self.draft("old-revision", "本人の新しい稿", "old")
        self.seed(dict(id="read-exposure", type="exposure", itemId="writing", level="stem", source="codex_report"))
        self.assertEqual(self.summary()["drafts"][0]["activity_seq"], 4)
        self.assertEqual(self.summary()["drafts"][0]["latest_version_id"], "old-revision")
        # Creation-order pagination remains unchanged even when old is edited.
        page = self.core.read_writing_drafts(self.sid, limit=1)
        self.assertEqual(page["drafts"][0]["draft_id"], "new")
        self.assertEqual(self.core.read_writing_drafts(self.sid, before=page["next_before"])["drafts"][0]["draft_id"], "old")

    def test_states_never_imply_completion_and_only_latest_feedback_is_current(self):
        self.draft(text=" \r\n\t")
        self.assertEqual(self.summary()["drafts"][0]["state"], "empty_draft")
        self.feedback(task_response="unclear", task_evidence_quote="", issues=[])
        self.assertEqual(self.summary()["drafts"][0]["state"], "empty_draft")
        self.draft("v2", "私はAを選ぶ。", "draft")
        row = self.summary()["drafts"][0]
        self.assertEqual(row["state"], "awaiting_feedback")
        self.assertIsNone(row["current_feedback_id"])
        self.feedback("f2", task_response="answered", issues=[])
        row = self.summary()["drafts"][0]
        self.assertEqual((row["state"], row["current_feedback_id"]), ("feedback_available", "f2"))
        self.feedback("f3", task_response="partial", issues=[])
        self.assertEqual(self.summary()["drafts"][0]["current_feedback_id"], "f3")
        self.draft("v3", "私はBも考えた。", "draft")
        row = self.summary()["drafts"][0]
        self.assertEqual((row["state"], row["current_feedback_id"], row["version_count"]), ("awaiting_feedback", None, 3))
        before = self.snapshot()
        self.assertEqual(self.core.resume_learning()["writing_resume"], {key: self.summary()[key] for key in ("drafts", "draft_count", "has_more")})
        self.assertEqual(self.snapshot(), before)

    def test_invalid_sources_preserve_exact_history_and_no_current_feedback_or_images(self):
        text = " か\u3099・が・𠮷・👩🏽‍💻\r\n原稿。 "
        self.draft(text=text)
        self.feedback()
        original = copy.deepcopy(self.core.items["writing"])
        for change, code in ((lambda: self.core.items.pop("writing"), "source_missing"),
                             (lambda: self.core.items["writing"].update(stem="new prompt"), "source_changed"),
                             (lambda: self.core.items["writing"].update(status="indexed"), "source_invalid")):
            self.core.items["writing"] = copy.deepcopy(original)
            change()
            before = self.snapshot()
            row = self.summary()["drafts"][0]
            context = self.context()
            self.assertEqual((row["state"], row["source_unavailable_code"]), ("source_unavailable", code))
            self.assertFalse(row["source_available"])
            self.assertIsNone(row["current_feedback_id"])
            self.assertTrue(row["source_unavailable_reason"])
            self.assertEqual(context["original_version"]["text"], text)
            self.assertEqual(context["prompt"], original["stem"])
            self.assertEqual(context["images"], [])
            self.assertIsNone(context["source_url"])
            self.assertEqual(self.snapshot(), before)

    def test_many_drafts_unicode_exact_ids_and_bounded_nonleaking_summaries(self):
        text = "か\u3099👩🏽‍💻\r\n" * 700
        self.core.items["writing"]["displayTitle"] = "𠮷" * 500
        ids = [f"d{index:02}-" + "i" * 146 for index in range(12)]
        for draft_id in ids:
            self.draft(draft_id, text)
        self.feedback("feedback-private", ids[-1], task_comment="PRIVATE_FEEDBACK_QUOTE", issues=[])
        before = self.snapshot()
        result = self.summary(limit=10)
        self.assertEqual(result["draft_count"], 12)
        self.assertEqual(len(result["drafts"]), 10)
        self.assertTrue(result["has_more"])
        self.assertEqual([row["draft_id"] for row in result["drafts"]], ids[::-1][:10])
        for row in result["drafts"]:
            self.assertEqual(row["latest_excerpt"], text[:80])
            self.assertNotIn("text", row)
            self.assertNotIn("prompt", row)
            self.assertNotIn("feedback", row)
        encoded = json.dumps(result, ensure_ascii=False)
        for secret in ("SECRET_", "PRIVATE_FEEDBACK_QUOTE", text, '"score"', '"due"', '"completed"'):
            self.assertNotIn(secret, encoded)
        resumed = self.core.resume_learning()
        self.assertEqual(len(resumed["writing_resume"]["drafts"]), 3)
        self.assertTrue(resumed["writing_resume"]["has_more"])
        self.assertLess(len(json.dumps(resumed, ensure_ascii=False).encode()), 20000)
        self.assertEqual(self.context(ids[-1])["latest_version"]["text"], text)
        self.assertEqual(self.snapshot(), before)

    def test_summary_and_revision_use_one_connection_snapshot_during_concurrent_write(self):
        self.draft()
        actual = self.core._writing_resume_summary
        writer_result = []
        def concurrent_summary(rows, limit=3):
            def write():
                other = self.configure(Core(self.vault))
                writer_result.append(other.save_writing_draft(**self.draft_params("concurrent", "new")))
            thread = threading.Thread(target=write)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            return actual(rows, limit)
        with patch.object(self.core, "_writing_resume_summary", side_effect=concurrent_summary):
            result = self.summary()
        self.assertEqual(result["revision"], 1)
        self.assertEqual(result["draft_count"], 1)
        self.assertEqual(writer_result[0]["revision"], 2)
        with patch.object(self.core, "connection", wraps=self.core.connection) as connections:
            resumed = self.core.resume_learning()
            self.assertEqual(connections.call_count, 1)
        self.assertEqual(resumed["revision"], 2)
        self.assertEqual(resumed["writing_resume"]["draft_count"], 2)

    def test_backup_restore_existing_schema_preserves_history_and_summary(self):
        self.draft(text="旧原稿\r\nか\u3099")
        self.feedback()
        self.draft("v2", "本人改稿", "draft")
        before = self.snapshot()
        self.core.backup(self.root / "backup")
        Core.restore(self.root / "backup", self.root / "restored")
        restored = self.configure(Core(self.root / "restored"))
        resumed = restored.resume_learning()
        with restored.connection() as db:
            self.assertEqual(restored.meta(db, "schema_version"), 2)
        self.assertEqual(resumed["writing_resume"], {key: self.summary()[key] for key in ("drafts", "draft_count", "has_more")})
        self.assertEqual(self.snapshot(restored), before)

    def test_worst_case_review_and_writing_envelope_remains_bounded_and_truthful(self):
        fixture = reviews.ReviewLoopTests("test_queue_input_validation_is_strict_and_read_only")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        item = self.item()
        item["displayTitle"] = "𠮷" * 500
        fixture.core.items[item["id"]] = item
        fixture.core.catalog["items"].append(item)
        for index in range(3):
            saved = fixture.core.save_writing_draft(fixture.sid, fixture.rev, f"writing-{index}", item["id"], item["version"],
                "𠮷" * 8000, source_fingerprint=fixture.core._writing_fingerprint(item))
            fixture.rev = saved["revision"]
        for index, item_id in enumerate(("source", "shared", "coarse")):
            aid = f"budget-attempt-{index}"
            fixture.attempt(aid, item_id, answer="𠮷" * 1000, reason="𠮷" * 1000)
            fixture.save(dict(id=f"budget-reason-{index}", type="reason_review", targetId=aid,
                              reviewer="tutor", agrees=True, result="correct", note="𠮷" * 1000))
            fixture.note(f"budget-note-{index}", aid, summary="𠮷" * 2000, next_step="𠮷" * 1200,
                         open_question="𠮷" * 1200, hypothesis="𠮷" * 1000)
        before = fixture.snapshot()
        view = fixture.core.resume_learning()
        self.assertLess(len(json.dumps(view, ensure_ascii=False).encode()), 20000)
        self.assertTrue(view["resume_truncated"])
        self.assertEqual(view["writing_resume"]["draft_count"], 3)
        self.assertEqual(view["writing_resume"]["has_more"], len(view["writing_resume"]["drafts"]) < 3)
        self.assertEqual(fixture.snapshot(), before)
        # Force the envelope's writing-row removal branch; exact IDs survive in
        # retained rows, count stays complete, and a follow-up read recovers all.
        overfull = self.core.resume_learning()
        overfull["writing_resume"] = fixture.core.read_writing_resume(fixture.sid)
        overfull["limits"].append("𠮷" * 3500)
        limited = bound_resume(overfull)
        self.assertLess(len(json.dumps(limited, ensure_ascii=False).encode()), 20000)
        self.assertTrue(limited["writing_resume"]["has_more"])
        self.assertEqual(limited["writing_resume"]["draft_count"], 3)
        full = fixture.core.read_writing_context(fixture.sid, "writing-2")["context"]
        self.assertEqual(full["latest_version"]["text"], "𠮷" * 8000)


class WritingMediaContinuityTests(WritingFixtures, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.media = self.root / "media"
        self.media.mkdir()
        self.image = self.media / "complete.png"
        self.image.write_bytes(png())
        self.original = self.item("original")
        self.core.items[self.original["id"]] = self.original
        self.core.items["writing"].update(kind="official", status="reviewed_official", pages=[1], order=999,
            delivery={"format": "official_pages", "review": "complete_clean", "source_url": "https://example.invalid/synthetic.pdf",
                      "source_sha256": "f" * 64, "assets": [{"path": "complete.png", "sha256": hashlib.sha256(self.image.read_bytes()).hexdigest(),
                                                           "width": 1, "height": 1, "pdf_page": 1, "alt": "合成完整原页"}]})
        media_patch = patch("eju_core.question_media.MEDIA_ROOT", self.media)
        media_patch.start()
        self.addCleanup(media_patch.stop)

    def test_complete_official_candidate_first_safe_images_and_missing_or_changed_media(self):
        before = self.snapshot()
        listing = self.core.read_writing_drafts(self.sid)
        official, original = listing["available_items"]
        self.assertEqual((official["kind"], original["kind"]), ("official", "original"))
        self.assertEqual(original["practice_label"], "原创辅助练习")
        self.assertTrue(official["images_required"])
        self.assertEqual(set(official["images"][0]), {"index", "sha256", "width", "height", "pdf_page", "alt", "media_type"})
        self.assertNotIn(str(self.media), json.dumps(listing))
        self.assertEqual(self.snapshot(), before)
        self.draft(text="原稿はそのまま残る。")
        context = self.context()
        self.assertEqual(context["images"], official["images"])
        self.assertEqual(context["source_url"], official["source_url"])
        original_bytes = self.image.read_bytes()
        for mode in ("missing", "corrupt", "changed_source"):
            self.image.write_bytes(original_bytes)
            if mode == "missing": self.image.unlink()
            if mode == "corrupt": self.image.write_bytes(original_bytes + b"changed")
            if mode == "changed_source": self.core.items["writing"]["delivery"]["source_url"] = "https://example.invalid/replacement.pdf"
            before = self.snapshot()
            context = self.context()
            self.assertEqual(context["images"], [])
            self.assertIsNone(context["source_url"])
            self.assertEqual(context["original_version"]["text"], "原稿はそのまま残る。")
            row = self.core.read_writing_resume(self.sid)["drafts"][0]
            self.assertEqual(row["state"], "source_unavailable")
            code = "source_changed" if mode == "changed_source" else "material_unavailable"
            self.assertEqual(row["source_unavailable_code"], code)
            self.assert_error(code, self.draft, "blocked", "本人新稿", "draft")
            self.assertEqual(self.snapshot(), before)

    def test_http_resume_auth_and_existing_image_endpoint_are_readonly(self):
        self.draft()
        server, token = make_server(self.core)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join()))
        host = f"127.0.0.1:{server.server_port}"
        def request(method, path, payload=None, authorized=True):
            headers = {"Origin": "http://" + host, "Content-Type": "application/json"}
            if authorized: headers["Authorization"] = "Bearer " + token
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request(method, path, json.dumps(payload) if payload else None, headers)
            response = connection.getresponse()
            result = response.status, response.read()
            connection.close()
            return result
        before = self.snapshot()
        args = {"method": "read_writing_resume", "params": {"session_id": self.sid}}
        status, raw = request("POST", "/api/call", args)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["drafts"][0]["draft_id"], "draft")
        self.assertEqual(request("POST", "/api/call", args, False)[0], 401)
        path = "/api/question-image?" + urlencode(dict(item_id="writing", index=0, session_id=self.sid))
        self.assertEqual(request("GET", path), (200, self.image.read_bytes()))
        self.assertEqual(request("GET", path, authorized=False)[0], 401)
        self.image.unlink()
        self.assertEqual(request("GET", path)[0], 400)
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

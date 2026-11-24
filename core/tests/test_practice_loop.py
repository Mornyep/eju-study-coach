"""Synthetic vault/media tests; never read a learner's real study records."""
import copy
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eju_core.errors import CoreError
from eju_core.evidence import evidence_map
from eju_core.practice_loop import PracticeLoopMixin
from eju_core.store import Core, stamp, today


if not issubclass(Core, PracticeLoopMixin):
    class LoopCore(PracticeLoopMixin, Core):
        pass
else:
    LoopCore = Core


def png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b""))


class PracticeLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.media = self.root / "synthetic-media"
        self.media.mkdir()
        self.addCleanup(patch.stopall)
        patch("eju_core.question_media.MEDIA_ROOT", self.media).start()
        self.vault = self.root / "vault"
        LoopCore.initialize(self.vault)
        self.core = LoopCore(self.vault)
        self.source = self.item("source", ["n.shared"], kind="original")
        self.catalog = {
            "subjects": {"math1": "数学1", "integrated": "综合科目"},
            "nodes": [{"id": value, "label": value + " label"} for value in ("n.shared", "n.other", "n.due")],
            "items": [self.source, self.item("shared", ["n.shared"], order=20),
                      self.item("coarse", ["n.other"], order=1), self.item("extra", ["n.other"], order=30)],
        }
        self.configure(self.core)
        resumed = self.core.resume_learning()
        self.sid, self.rev = resumed["session_id"], resumed["revision"]

    def item(self, item_id, nodes=None, order=0, kind="official", **overrides):
        data = png()
        filename = item_id + ".png"
        (self.media / filename).write_bytes(data)
        item = {
            "id": item_id, "version": "v1-" + item_id, "kind": kind,
            "title": "题目 " + item_id, "displayTitle": "标题 " + item_id,
            "subjects": ["math1"], "nodes": nodes or [], "order": order, "groupId": "group-" + item_id,
            "status": "reviewed_official" if kind == "official" else "reviewed_original",
            "stem": "SECRET_STEM_" + item_id, "answer": "SECRET_ANSWER_" + item_id,
            "solution": "SECRET_SOLUTION_" + item_id, "grading": {"mode": "choice", "value": "1"},
            "pages": [1], "delivery": {
                "format": "official_pages" if kind == "official" else "authored_figure",
                "review": "complete_clean", "source_url": "https://example.invalid/synthetic.pdf",
                "source_sha256": hashlib.sha256(item_id.encode()).hexdigest(),
                "assets": [{"path": filename, "sha256": hashlib.sha256(data).hexdigest(),
                            "width": 1, "height": 1, "pdf_page": 1}],
            },
        }
        item.update(overrides)
        return item

    def configure(self, core):
        core.catalog = copy.deepcopy(self.catalog)
        core.items = {item["id"]: item for item in core.catalog["items"]}
        core.coaching = {item_id: {"item_version": item["version"], "reviewed": True,
                                   "questionFocus": "SECRET_COACHING_" + item_id}
                         for item_id, item in core.items.items()}
        return core

    def save(self, event):
        receipt = self.core.record_learning_event(self.sid, self.rev, event)
        self.rev = receipt["revision"]
        return receipt

    def attempt(self, attempt_id="attempt", item_id="source", **overrides):
        item = self.core.items[item_id]
        event = dict(id=attempt_id, type="attempt", itemId=item_id, itemVersion=item["version"], answer="1",
                     reason="本人作答依据", result="correct", hint="H0", seenBefore=False,
                     guessed=False, seconds=None, grading="external")
        event.update(overrides)
        return self.save(event)

    def append(self, event):
        """Seed synthetic historical records, including stale/invalid links."""
        event = {"createdAt": stamp(), "date": today(), "sessionId": self.sid, **event}
        with self.core.connection(write=True) as db:
            self.core.insert_event(db, event, "synthetic_fixture")
            result = self.core.finish_request(db, event["id"], "fixture-" + event["id"])
        self.rev = result["revision"]
        return event

    def snapshot(self, core=None):
        result = (core or self.core).export_json()
        result.pop("exported_at")
        return result

    def preview(self, basis=None, subject="math1"):
        return self.core.get_next_practice(self.sid, subject, basis)

    def start(self, preview, request_id="next", candidate=None, **overrides):
        candidate = candidate or preview["candidates"][0]
        params = dict(session_id=self.sid, expected_revision=preview["revision"], request_id=request_id,
                      item_id=candidate["item_id"], item_version=candidate["item_version"], subject=preview["subject"],
                      base_task_id=preview["base_task_id"], basis_attempt_id=preview["basis_attempt_id"],
                      basis_reflection_id=preview["basis_reflection_id"])
        params.update(overrides)
        result = self.core.start_next_practice(**params)
        self.rev = result["revision"]
        return result

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(CoreError) as raised:
            operation(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def complete_source_with_reflection(self):
        result = self.core.present_learning_task(self.sid, self.rev, "source-task", "source", "独立作答")
        self.rev = result["revision"]
        for request_id, action, payload in (
            ("show-source", "show", None),
            ("submit-source", "submit", {"answer": "1", "reason": "本人依据"}),
            ("reveal-source", "answer", None),
            ("reflection-one", "reflect", {"cause": "evidence", "evidence": "还需核对依据", "next_action": "独立核对", "base_reflection_id": None}),
        ):
            result = self.core.workbench_action(self.sid, self.rev, request_id, "source-task", action, payload)
            self.rev = result["revision"]
        return "submit-source.attempt"

    def test_descending_stored_seq_pagination_ignores_new_inserts(self):
        for index in range(6):
            # Deliberately opposite dates: pagination must use stored sequence.
            self.append(dict(id="a" + str(index), type="attempt", itemId="source", itemVersion="v1-source",
                             date="2020-01-" + str(9 - index).zfill(2), answer="1", reason="r"))
        first = self.core.read_study_review(self.sid, limit=2)
        self.assertEqual([row["attempt_id"] for row in first["reviews"]], ["a5", "a4"])
        self.assertTrue(first["has_more"])
        self.attempt("inserted-after-first-page")
        second = self.core.read_study_review(self.sid, before=first["next_before"], limit=2)
        third = self.core.read_study_review(self.sid, before=second["next_before"], limit=2)
        self.assertEqual([row["attempt_id"] for row in second["reviews"] + third["reviews"]], ["a3", "a2", "a1", "a0"])
        self.assertFalse(third["has_more"])
        self.assertEqual(self.core.read_study_review(self.sid, limit=1)["reviews"][0]["attempt_id"], "inserted-after-first-page")

    def test_review_bounds_allowlist_latest_claim_and_valid_reflection(self):
        self.attempt(answer="a" * 1300, reason="r" * 1500)
        self.append(dict(id="old-review", type="reason_review", targetId="attempt", agrees=False, note="old"))
        self.append(dict(id="new-review", type="reason_review", targetId="attempt", agrees=True, reviewer="self",
                         result="correct", note="n" * 1400, solution="SECRET_REVIEW_EXTRA", answer="SECRET_REVIEW_ANSWER"))
        self.append(dict(id="reflection", type="reflection", attemptId="attempt", itemId="source", itemVersion="v1-source",
                         cause="evidence", evidence="e" * 2300, next_action="n" * 1500, source="learner_self_report"))
        self.append(dict(id="bad-link", type="reflection", attemptId="attempt", itemId="shared", itemVersion="v1-shared",
                         cause="evidence", evidence="wrong item", source="learner_self_report"))
        self.append(dict(id="draft", type="task_draft", answer="SECRET_DRAFT", reason="SECRET_DRAFT_REASON"))
        before = self.snapshot()
        row = self.core.read_study_review(self.sid)["reviews"][0]
        self.assertEqual((len(row["answer"]), len(row["reason"])), (1000, 1000))
        self.assertTrue(row["text_truncated"])
        self.assertEqual(set(row["reason_review"]), {"id", "reviewer", "agrees", "result", "note", "note_truncated"})
        self.assertTrue(row["reason_review"]["note_truncated"])
        self.assertEqual(len(row["reason_review"]["note"]), 1000)
        self.assertEqual(row["reflection"]["id"], "reflection")
        self.assertEqual(row["reflection"]["cause_label"], "解题依据")
        self.assertEqual((len(row["reflection"]["evidence"]), len(row["reflection"]["next_action"])), (2000, 1200))
        raw = json.dumps(row)
        self.assertNotIn("SECRET_", raw)
        self.assertEqual(self.snapshot(), before)
        with self.core.connection() as db:
            events = self.core.events(db)
        self.assertEqual(self.core.study_review_summary(events), [{key: value for key, value in row.items() if key != "seq"}])

    def test_invalid_attempts_remain_visible_and_cannot_be_basis(self):
        self.attempt("retracted")
        self.save(dict(id="retract", type="retract", targetId="retracted", reason="本人撤销"))
        self.attempt("stale", "shared")
        self.core.items["shared"]["version"] = "v2-shared"
        self.attempt("disputed", "coarse")
        self.save(dict(id="issue", type="issue", itemId="coarse", reason="待核对"))
        self.attempt("missing", "extra")
        del self.core.items["extra"]
        rows = self.core.read_study_review(self.sid)["reviews"]
        self.assertEqual({row["attempt_id"] for row in rows}, {"retracted", "stale", "disputed", "missing"})
        self.assertTrue(all(row["invalid_reason"] for row in rows))
        for attempt_id in ("retracted", "stale", "disputed", "missing", "nonexistent"):
            with self.subTest(attempt_id=attempt_id):
                self.assert_error("basis_invalid", self.preview, attempt_id)
        self.assert_error("invalid_subject", self.preview, subject="unknown")

    def test_subject_filter_validation_and_foreign_basis(self):
        self.core.items["extra"]["subjects"] = ["integrated"]
        self.attempt("math-attempt")
        self.attempt("integrated-attempt", "extra")
        page = self.core.read_study_review(self.sid, subject="integrated")
        self.assertEqual([row["attempt_id"] for row in page["reviews"]], ["integrated-attempt"])
        self.assertEqual(page["subjects"], self.catalog["subjects"])
        self.assert_error("basis_invalid", self.preview, "integrated-attempt")
        for kwargs in ({"subject": []}, {"subject": ""}, {"before": -1}, {"before": True}, {"limit": 0}, {"limit": 21}):
            with self.subTest(kwargs=kwargs), self.assertRaises(CoreError):
                self.core.read_study_review(self.sid, **kwargs)

    def test_shared_nodes_rank_first_then_repair_due_and_stable_order(self):
        self.catalog["items"] += [self.item("repair", ["n.other"], order=50),
                                  self.item("due", ["n.due"], order=70),
                                  self.item("repair-source", ["n.other"], kind="original"),
                                  self.item("due-source", ["n.due"], kind="original")]
        self.configure(self.core)
        self.attempt(result="ungraded")
        self.attempt("repair-attempt", "repair-source", result="incorrect")
        self.append(dict(id="due-attempt", type="attempt", itemId="due-source", itemVersion="v1-due-source",
                         answer="1", reason="本人依据", result="correct", hint="H0", seenBefore=False, guessed=False,
                         date="2020-01-01"))
        preview = self.preview("attempt")
        # Coarse has a repair node and an earlier order than repair; due follows.
        self.assertEqual([row["item_id"] for row in preview["candidates"]], ["shared", "coarse", "extra"])
        self.assertEqual(preview["candidates"][0]["relation"], "shared_nodes")
        self.assertEqual(preview["candidates"][0]["shared_nodes"], [{"id": "n.shared", "label": "n.shared label"}])
        self.core.items["coarse"]["status"] = self.core.items["extra"]["status"] = "indexed"
        self.assertEqual([row["item_id"] for row in self.preview("attempt")["candidates"]], ["shared", "repair", "due"])

    def test_no_fine_grained_nodes_uses_coarse_subject_without_cause_inference(self):
        self.attempt()
        self.core.items["source"]["nodes"] = []
        self.append(dict(id="coarse-reflection", type="reflection", attemptId="attempt", itemId="source",
                         itemVersion="v1-source", cause="calculation", evidence="本人暂时判断计算", source="learner_self_report"))
        preview = self.preview("attempt")
        self.assertEqual(preview["basis"]["reflection_cause_label"], "计算")
        self.assertTrue(preview["basis"]["self_report"])
        self.assertTrue(all(row["relation"] == "same_subject" and row["shared_nodes"] == [] for row in preview["candidates"]))
        self.assertTrue(all("匹配具体错因" in row["reason"] for row in preview["candidates"]))

    def test_seen_groups_exposure_siblings_and_result_dedup(self):
        items = [self.source, self.item("seen", order=0, groupId="seen-group", exposureGroupId="seen-page"),
                 self.item("seen-group-sibling", order=1, groupId="seen-group"),
                 self.item("seen-page-sibling", order=2, exposureGroupId="seen-page"),
                 self.item("g1", order=3, groupId="duplicate-group"),
                 self.item("g2", order=4, groupId="duplicate-group"),
                 self.item("p1", order=5, exposureGroupId="duplicate-page"),
                 self.item("p2", order=6, exposureGroupId="duplicate-page"), self.item("tail", order=7)]
        self.catalog["items"] = items
        self.configure(self.core)
        self.attempt("seen-attempt", "seen")
        self.save(dict(id="seen-retraction", type="retract", targetId="seen-attempt", reason="撤销也不抹去看题事实"))
        self.assertEqual([row["item_id"] for row in self.preview()["candidates"]], ["g1", "p1", "tail"])
        self.save(dict(id="shown-g1", type="exposure", itemId="g1", level="stem", source="learner_report"))
        self.assertEqual([row["item_id"] for row in self.preview()["candidates"]], ["p1", "tail"])

    def test_missing_media_disputed_corrected_and_exhaustion_do_not_fallback(self):
        (self.media / "shared.png").unlink()
        self.save(dict(id="issue-coarse", type="issue", itemId="coarse", reason="待核对"))
        self.save(dict(id="issue-extra", type="issue", itemId="extra", reason="待纠正"))
        self.save(dict(id="correct-extra", type="resolve", issueId="issue-extra", itemId="extra", reference="合成核验",
                       explanation="已纠正", outcome="corrected"))
        preview = self.preview()
        self.assertEqual(preview["candidates"], [])
        self.assertTrue(preview["no_candidate"])
        self.assertIn("完整", preview["message"])
        self.assertIsNone(preview["basis"])
        self.assert_error("candidate_unavailable", self.start, preview, candidate={"item_id": "source", "item_version": "v1-source"})

    def test_preview_and_start_preserve_profile_evidence_and_do_not_record_exposure(self):
        self.attempt()
        before = self.snapshot()
        evidence_before = evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"])
        preview = self.preview("attempt")
        self.core.read_study_review(self.sid)
        self.assertEqual(self.snapshot(), before)
        for candidate in preview["candidates"]:
            self.assertEqual(candidate["image_count"], 1)
            self.assertFalse({"stem", "answer", "solution", "coaching", "images"}.intersection(candidate))
        receipt = self.start(preview)
        self.assertFalse(receipt["learner_exposure_recorded"])
        after = self.core.export_json()
        self.assertEqual(after["events"][:-1], before["events"])
        self.assertEqual(after["profile"], before["profile"])
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]), evidence_before)
        event = after["events"][-1]
        self.assertEqual((event["type"], event["basis_attempt_id"], event["selection_relation"]), ("task", "attempt", "shared_nodes"))
        self.assertEqual(event["sessionId"], self.sid)
        with self.core.connection() as db:
            self.assertEqual(db.execute("SELECT origin FROM events WHERE id='next'").fetchone()[0], "native_workbench")
        self.core.read_workbench(self.sid)
        self.assertFalse(any(e["type"] == "exposure" for e in self.core.export_json()["events"]))
        shown = self.core.workbench_action(self.sid, self.rev, "actually-shown", "next", "show")
        self.assertTrue(shown["saved"])
        self.assertEqual(sum(e["type"] == "exposure" for e in self.core.export_json()["events"]), 1)

    def test_start_rechecks_version_source_context_eligibility_and_membership(self):
        self.attempt()
        preview = self.preview("attempt")
        self.core.items["shared"]["version"] = "v2-shared"
        self.assert_error("item_version_conflict", self.start, preview)
        self.core.items["shared"]["version"] = "v1-shared"
        for field, value in (("status", "indexed"), ("subjects", ["integrated"])):
            with self.subTest(field=field):
                original = self.core.items["shared"][field]
                self.core.items["shared"][field] = value
                self.assert_error("candidate_unavailable", self.start, preview)
                self.core.items["shared"][field] = original
        self.catalog["items"].append(self.item("arbitrary", order=999))
        self.core.items["arbitrary"] = self.catalog["items"][-1]
        self.assert_error("candidate_unavailable", self.start, preview, candidate={"item_id": "arbitrary", "item_version": "v1-arbitrary"})
        self.save(dict(id="new-exposure", type="exposure", itemId="shared", level="stem", source="learner_report"))
        self.assert_error("revision_conflict", self.start, preview)
        self.assert_error("candidate_unavailable", self.start, preview, expected_revision=self.rev)
        self.assert_error("basis_changed", self.start, self.preview(), basis_reflection_id="unsupplied-context")
        self.assertFalse(any(event["type"] == "task" for event in self.core.export_json()["events"]))

    def test_reflection_edit_requires_new_revision_and_current_reflection_id(self):
        basis = self.complete_source_with_reflection()
        preview = self.preview(basis)
        self.assertEqual(preview["basis_reflection_id"], "reflection-one")
        result = self.core.workbench_action(self.sid, self.rev, "reflection-two", "source-task", "reflect",
                                           dict(cause="not_sure", evidence="改为还不确定", next_action="再独立核对",
                                                base_reflection_id="reflection-one"))
        self.rev = result["revision"]
        self.assert_error("revision_conflict", self.start, preview)
        self.assert_error("basis_changed", self.start, preview, expected_revision=self.rev)
        refreshed = self.preview(basis)
        self.assertEqual(refreshed["basis_reflection_id"], "reflection-two")
        self.start(refreshed)
        self.assertEqual(self.core.export_json()["events"][-1]["basis_reflection_id"], "reflection-two")

    def test_active_paused_and_changed_task_protection_are_atomic(self):
        self.start(self.preview())
        before = self.snapshot()
        self.assert_error("task_changed", self.start, self.preview(), request_id="changed", base_task_id=None)
        self.assert_error("task_active", self.start, self.preview(), request_id="active")
        self.assertEqual(self.snapshot(), before)
        result = self.core.workbench_action(self.sid, self.rev, "pause-next", "next", "pause")
        self.rev = result["revision"]
        before = self.snapshot()
        self.assert_error("task_paused", self.start, self.preview(), request_id="paused")
        self.assertEqual(self.snapshot(), before)

    def test_two_clients_start_one_winner(self):
        preview = self.preview()
        other = self.configure(LoopCore(self.vault))
        other_sid = other.resume_learning()["session_id"]
        barrier = threading.Barrier(2)
        results = []
        def write(core, session_id, request_id):
            candidate = preview["candidates"][0]
            barrier.wait()
            try:
                results.append(core.start_next_practice(session_id, 0, request_id, candidate["item_id"],
                                                        candidate["item_version"], "math1"))
            except CoreError as error:
                results.append(error.result())
        threads = [threading.Thread(target=write, args=(self.core, self.sid, "client-one")),
                   threading.Thread(target=write, args=(other, other_sid, "client-two"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(row["ok"] for row in results), 1)
        self.assertEqual([row["error"]["code"] for row in results if not row["ok"]], ["revision_conflict"])
        self.assertEqual(sum(event["type"] == "task" for event in self.core.export_json()["events"]), 1)
        self.assertFalse(any(event["type"] == "exposure" for event in self.core.export_json()["events"]))

    def test_replay_precedes_later_state_and_uses_stable_payload(self):
        preview = self.preview()
        original = self.start(preview, request_id="stable-request")
        self.core.workbench_action(self.sid, self.rev, "later-show", "stable-request", "show")
        sid = self.core.resume_learning()["session_id"]
        self.core.items[preview["candidates"][0]["item_id"]]["status"] = "indexed"
        replay = self.start(preview, request_id="stable-request", session_id=sid, expected_revision=0)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], original["revision"])
        self.assertGreater(replay["current_revision"], replay["revision"])
        self.assert_error("id_conflict", self.start, preview, request_id="stable-request", item_version="different-version")
        events = self.core.export_json()["events"]
        self.assertEqual(sum(event["type"] == "task" for event in events), 1)
        self.assertEqual(sum(event["type"] == "exposure" for event in events), 1)

    def test_backup_restore_preserves_reviews_context_and_exactly_once_receipt(self):
        basis = self.complete_source_with_reflection()
        preview = self.preview(basis)
        self.start(preview)
        before = self.snapshot()
        reviews = self.core.read_study_review(self.sid)
        self.core.backup(self.root / "backup")
        LoopCore.restore(self.root / "backup", self.root / "restored")
        restored = self.configure(LoopCore(self.root / "restored"))
        self.assertEqual(self.snapshot(restored), before)
        sid = restored.resume_learning()["session_id"]
        self.assertEqual(restored.read_study_review(sid)["reviews"], reviews["reviews"])
        candidate = preview["candidates"][0]
        receipt = restored.start_next_practice(sid, 0, "next", candidate["item_id"], candidate["item_version"], "math1",
                                               preview["base_task_id"], basis, preview["basis_reflection_id"])
        self.assertTrue(receipt["replayed"])
        self.assertEqual(sum(event["type"] == "task" for event in restored.export_json()["events"]), 2)


if __name__ == "__main__":
    unittest.main()

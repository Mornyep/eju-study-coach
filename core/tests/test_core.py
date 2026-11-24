import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from eju_core.store import Core, canonical, sha
from eju_core.errors import CoreError


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base / "vault"
        Core.initialize(self.vault)
        self.core = Core(self.vault)
        self.resume()
        self.item = self.core.items["STEM-M1-CP-F"]

    def resume(self):
        r = self.core.resume_learning()
        self.sid, self.rev = r["session_id"], r["revision"]
        return r

    def save(self, event):
        result = self.core.record_learning_event(self.sid, self.rev, event)
        self.rev = result["revision"]
        return result

    def attempt(self, **extra):
        value = dict(id="a1", type="attempt", itemId=self.item["id"], itemVersion=self.item["version"],
                     answer="2/3", reason="已知条件下有三枚札，其中两枚偶数。",
                     result="correct", hint="H0", seenBefore=False, guessed=False,
                     grading="automatic", seconds=None)
        value.update(extra)
        return value

    def review(self, target="a1", id="r1"):
        return self.save(dict(id=id, type="reason_review", targetId=target, agrees=True,
                              note="依据原题与解析核对了理由。", reviewer="tutor"))

    def profile(self):
        r = self.core.set_profile(self.sid, self.rev, "profile1",
                                  {"subjects": ["math1"], "minutes": 15, "goal": "检查条件概率"})
        self.rev = r["revision"]
        return r

    def plan(self, id="plan1", status="paused", basis=None):
        r = self.core.save_learning_plan(self.sid, self.rev, id,
                                        {"objective": "理解条件限制", "next_action": "下次先判断分母",
                                         "reason": "先核对条件集合，再复测独立变式。", "status": status,
                                         "basis_event_ids": basis or [], "item_ids": [self.item["id"]],
                                         "hypotheses": ["可能忽略已知条件，待独立验证。"]})
        self.rev = r["revision"]
        return r

    def legacy(self, events=None, **extra):
        state = {"schema": 5, "id": "legacy-user", "revision": 37,
                 "profile": {"subjects": ["math1"], "minutes": 15},
                 "events": events or [], "draft": {"currentItem": self.item["id"], "extra": "preserve"},
                 "createdDate": "2026-10-14", "unknown": {"日本語": ["保留", None]}}
        state.update(extra)
        return json.dumps({"format": "eju-workbench-backup", "version": "5.0",
                           "state": state, "wrapper_extra": "preserve too"}, ensure_ascii=False)

    def legacy_attempt(self):
        e = self.attempt()
        e.update(date="2026-10-01", createdAt="2026-10-01T10:00:00Z", nodes=self.item["nodes"],
                 extension={"keep": True})
        return e

    def legacy_review(self):
        return {"id": "r1", "type": "reason_review", "date": "2026-10-01",
                "createdAt": "2026-10-01T10:01:00Z", "targetId": "a1", "agrees": True,
                "note": "original check", "reviewer": "self"}

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(CoreError) as context:
            fn(*args, **kwargs)
        self.assertEqual(context.exception.code, code)
        return context.exception


class PersistenceTests(CoreCase):
    def test_empty_unknown_and_missing_vault_not_created(self):
        r = self.resume()
        self.assertTrue(r["profile_needs_input"])
        self.assertEqual(r["profile"]["subjects"], [])
        self.assertEqual(r["event_count"], 0)
        missing = self.base / "missing"
        self.assert_error("vault_missing", Core, missing)
        self.assertFalse(missing.exists())
        self.assert_error("destination_exists", Core.initialize, self.vault)

    def test_reopen_preserves_evidence_and_stop_plan_without_extra_attempt(self):
        self.profile()
        self.save(self.attempt())
        self.review()
        self.plan(basis=["a1", "r1"])
        c = Core(self.vault)
        result = c.resume_learning()
        self.assertEqual(result["revision"], 4)
        self.assertEqual(result["profile"]["subjects"], ["math1"])
        self.assertEqual(result["last_plan"]["status"], "paused")
        self.assertEqual(result["last_plan"]["next_action"], "下次先判断分母")
        self.assertEqual(sum(e["type"] == "attempt" for e in c.export_json()["events"]), 1)
        self.assertTrue(result["evidence"])
        self.assertTrue(all(r["stage"] == 1 for r in result["evidence"]))

    def test_duplicate_retries_and_changed_payload_conflict(self):
        a = self.attempt()
        first = self.save(a)
        again = self.core.record_learning_event(self.sid, 0, a)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["revision"], first["revision"])
        self.assertEqual(self.resume()["event_count"], 1)
        self.assert_error("id_conflict", self.core.record_learning_event, self.sid, self.rev,
                          {**a, "answer": "1/2"})
        self.assertEqual(self.resume()["event_count"], 1)

    def test_concurrent_writers_only_one_commits(self):
        other = Core(self.vault)
        r2 = other.resume_learning()
        barrier = threading.Barrier(2)
        output = []
        def write(core, sid, id):
            barrier.wait()
            try:
                output.append(core.record_learning_event(sid, 0, self.attempt(id=id)))
            except CoreError as exc:
                output.append(exc.result())
        threads = [threading.Thread(target=write, args=(self.core,self.sid,"a1")),
                   threading.Thread(target=write, args=(other,r2["session_id"],"a2"))]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(sum(r["ok"] for r in output), 1)
        self.assertEqual([r["error"]["code"] for r in output if not r["ok"]], ["revision_conflict"])
        self.assertEqual(self.resume()["event_count"], 1)

    def test_resume_required_and_expired_session(self):
        self.assert_error("resume_required", self.core.record_learning_event, "unknown", 0, self.attempt())
        with self.core.connection(write=True) as db:
            db.execute("UPDATE sessions SET created_at='2000-01-01T00:00:00+00:00'")
        self.assert_error("resume_required", self.core.record_learning_event, self.sid, 0, self.attempt())
        self.assertEqual(self.resume()["event_count"], 0)

    def test_storage_failure_never_returns_receipt_or_recreates_db(self):
        renamed = self.vault / "kept.sqlite"
        self.core.db_path.rename(renamed)
        self.assert_error("storage_error", self.core.record_learning_event, self.sid, 0, self.attempt())
        self.assertFalse(self.core.db_path.exists())
        renamed.rename(self.core.db_path)
        self.assertEqual(self.resume()["event_count"], 0)

    def test_invalid_event_and_reference_are_atomic(self):
        for change in [{"itemVersion": "wrong"}, {"seconds": float("nan")}, {"createdAt": "fake"},
                       {"hint": "H9"}, {"seconds": 30}, {"seenBefore": 0}]:
            with self.subTest(change=change), self.assertRaises(CoreError):
                self.save(self.attempt(**change))
        self.assert_error("reference_missing", self.core.record_learning_event, self.sid, 0,
                          dict(id="r1", type="reason_review", targetId="missing", agrees=True,
                               reviewer="tutor", note="not enough"))
        self.assertEqual(self.resume()["event_count"], 0)

    def test_profile_validation_and_plan_references(self):
        for profile in [{"subjects": [{}]}, {"subjects": ["math1","math1"]},
                        {"subjects": ["math1"], "minutes": True}, {"subjects": ["math1"], "secret": "bad"}]:
            with self.subTest(profile=profile), self.assertRaises(CoreError):
                self.core.set_profile(self.sid, 0, "p", profile)
        self.assert_error("reference_missing", self.plan, basis=["never"])
        self.assertEqual(self.resume()["revision"], 0)

    def test_history_paginates_without_dropping_originals(self):
        for n in range(16):
            self.plan(id=f"p{n}")
        r = self.resume()
        self.assertTrue(r["has_more_history"])
        self.assertEqual(len(r["recent_events"]), 6)
        all_ids, cursor = [], 0
        while True:
            page = self.core.read_learning_records(self.sid, after=cursor, limit=3)
            all_ids.extend(x["event"]["id"] for x in page["records"])
            cursor = page["next_cursor"]
            if not page["has_more"]: break
        self.assertEqual(all_ids, [f"p{n}" for n in range(16)])
        self.assertEqual(self.core.read_learning_records(self.sid, event_id="p5")["records"][0]["event"]["id"], "p5")

    def test_resume_bounds_references_but_keeps_full_event_history(self):
        for n in range(40):
            self.save(self.attempt(id=f"a{n}", seenBefore=True))
        r = self.resume()
        self.assertEqual(r["event_count"], 40)
        self.assertTrue(all(row["evidence_total"] == 40 for row in r["evidence"]))
        self.assertTrue(all(len(row["evidence"]) == 5 and row["details_truncated"] for row in r["evidence"]))
        self.assertLess(len(json.dumps(r,ensure_ascii=False)), 20000)

    def test_large_record_is_chunked_without_loss(self):
        content = "保留长记录" * 4000
        self.save({"id":"archive1", "type":"archive", "content":content})
        page = self.core.read_learning_records(self.sid)
        self.assertTrue(page["records"][0]["record_truncated"])
        self.assertLess(len(json.dumps(page,ensure_ascii=False)), 2000)
        fragments, offset = [], 0
        while True:
            part = self.core.read_record_detail(self.sid,"event","archive1",offset,2000)
            self.assertLessEqual(len(part["json_fragment"]),2000)
            fragments.append(part["json_fragment"]);offset=part["next_offset"]
            if not part["has_more"]:break
        self.assertEqual(json.loads("".join(fragments))["content"],content)


class EvidenceTests(CoreCase):
    def test_hint_floor_cannot_be_lowered_and_does_not_upgrade(self):
        self.save(dict(id="hint", type="exposure", itemId=self.item["id"], level="hint",
                       hint="H2", source="codex_report"))
        r = self.save(self.attempt(hint="H0"))
        self.assertEqual(r["applied_hint"], "H2")
        self.review()
        evidence = self.resume()["evidence"]
        self.assertTrue(all(e["stage"] == 0 for e in evidence))
        self.assertTrue(all(e["status"] == "needs_repair" for e in evidence))
        stored = self.core.read_learning_records(self.sid, event_id="a1")["records"][0]["event"]
        self.assertEqual(stored["reportedHint"], "H0")
        self.assertEqual(stored["hint"], "H2")

    def test_stem_in_same_session_vs_stopped_reopened(self):
        self.save(dict(id="stem", type="exposure", itemId=self.item["id"], level="stem", source="codex_report"))
        self.save(self.attempt())
        self.review()
        self.assertTrue(all(e["stage"] == 1 for e in self.resume()["evidence"]))
        other = self.core.items["STEM-M1-CP-C"]
        self.save(dict(id="stem2", type="exposure", itemId=other["id"], level="stem", source="codex_report"))
        self.plan()
        self.resume()  # A new session cannot call the old displayed question unseen.
        self.save(self.attempt(id="a2", itemId=other["id"], itemVersion=other["version"],
                               answer=str(other["grading"]["value"])))
        self.review("a2", "r2")
        self.assertTrue(all(len(e["independent"]) == 1 for e in self.resume()["evidence"]))

    def test_auto_numeric_result_uses_actual_answer_not_model_claim(self):
        self.save(self.attempt(answer="1/2", result="correct"))
        self.review()
        result = self.core.read_learning_records(self.sid, event_id="a1")["records"][0]["event"]
        self.assertEqual(result["result"], "incorrect")
        self.assertEqual(result["reportedResult"], "correct")
        self.assertTrue(all(e["stage"] == 0 for e in self.resume()["evidence"]))

    def test_reason_review_required_and_timer_null_is_not_zero(self):
        self.save(self.attempt())
        r = self.resume()
        self.assertTrue(all(e["stage"] == 0 and e["status"] == "supported" for e in r["evidence"]))
        record = self.core.read_learning_records(self.sid, event_id="a1")["records"][0]["event"]
        self.assertIsNone(record["seconds"])
        self.assertEqual(record["timingSource"], "unmeasured")
        self.review()
        self.assertTrue(all(e["stage"] == 1 for e in self.resume()["evidence"]))

    def test_dispute_freezes_and_resolution_restores_or_invalidates(self):
        self.save(self.attempt())
        self.review()
        self.save(dict(id="i1", type="issue", itemId=self.item["id"], reason="需要复核条件"))
        r = self.resume()
        self.assertTrue(all(e["stage"] == 0 and e["invalid"] for e in r["evidence"]))
        self.save(dict(id="fix", type="resolve", issueId="i1", itemId=self.item["id"],
                       outcome="confirmed_valid", reference="原题和已核对解析", explanation="条件无误"))
        self.assertTrue(all(e["stage"] == 1 for e in self.resume()["evidence"]))
        self.save(dict(id="i2", type="issue", itemId=self.item["id"], reason="另一个问题"))
        self.save(dict(id="fix2", type="resolve", issueId="i2", itemId=self.item["id"],
                       outcome="corrected", reference="校正记录", explanation="旧证据须重测"))
        self.assertTrue(all(e["stage"] == 0 and e["invalid"] for e in self.resume()["evidence"]))

    def test_candidate_scope_and_answer_separation(self):
        self.assertTrue(self.core.get_task_candidates(self.sid)["needs_subject"])
        self.profile()
        self.assertTrue(self.core.get_task_candidates(self.sid, minutes=5)["no_candidate"])
        c = self.core.get_task_candidates(self.sid, minutes=5, purpose="prerequisite")["candidates"]
        self.assertTrue(c)
        self.assertTrue(all("math1" in item["subjects"] and item["role"] in ("foundation","contrast") for item in c))
        stem = self.core.read_material(self.sid, c[0]["item_id"])
        self.assertTrue(stem["available"])
        self.assertNotIn("answer", stem)
        self.assertNotIn("prerequisites", stem)
        self.core.read_material(self.sid, c[0]["item_id"], section="coach")
        self.assertEqual(self.resume()["event_count"], 1)  # only profile, no learner exposure
        # Public beta excludes private corpus indexes. A synthetic legacy index
        # still must never become a complete question or leak an answer.
        self.assertFalse(any(i["status"] == "indexed" for i in self.core.items.values()))
        indexed = dict(self.item, id="test-legacy-index", kind="indexed", status="indexed", stem="")
        self.core.items[indexed["id"]] = indexed
        self.assertFalse(self.core.read_material(self.sid,indexed["id"])["available"])

    def test_feedback_is_only_selected_text_draft(self):
        self.save(self.attempt())
        result = self.core.prepare_feedback(self.sid, self.item["id"], "question", "请核对条件")
        self.assertFalse(result["uploaded"])
        self.assertTrue(result["draft_only"])
        self.assertEqual(result["draft"]["attachments"], [])
        self.assertNotIn("vault_id", result["draft"])
        self.assertNotIn("reason", result["draft"])
        self.assertEqual(self.resume()["event_count"], 1)


class MigrationTests(CoreCase):
    def test_preview_apply_preserves_every_source_field_and_legacy_due(self):
        raw = self.legacy([self.legacy_attempt(), self.legacy_review()])
        preview = self.core.import_v5(raw, 0, "import1")
        self.assertFalse(preview["applied"])
        self.assertEqual(self.resume()["event_count"], 0)
        self.core.import_v5(raw, 0, "import1", True)
        r = self.resume()
        self.assertEqual(r["event_count"], 2)
        self.assertTrue(r["legacy_draft_available"])
        self.assertTrue(all(e["due"] == "2026-10-02" for e in r["evidence"]))
        exported = self.core.export_json()
        self.assertEqual(exported["events"], json.loads(raw)["state"]["events"])
        self.assertEqual(exported["legacy_sources"][0]["raw"], raw)
        self.assertEqual(json.loads(exported["legacy_sources"][0]["state"]), json.loads(raw)["state"])
        self.assertEqual(exported["legacy_draft"], json.loads(raw)["state"]["draft"])

    def test_duplicate_imports_and_conflict_are_atomic(self):
        raw = self.legacy([self.legacy_attempt()])
        self.core.import_v5(raw, 0, "import1", True)
        replay = self.core.import_v5(raw, 0, "import1", True)
        self.assertTrue(replay["replayed"])
        r = self.resume()
        again = self.core.import_v5(raw, r["revision"], "import2", True)
        self.assertEqual(again["events_added"], 0)
        self.assertEqual(self.resume()["revision"], r["revision"])
        changed = json.loads(raw)
        changed["state"]["events"][0]["answer"] = "bad"
        before = self.core.export_json()["events"]
        self.assert_error("event_conflict", self.core.import_v5, json.dumps(changed), self.rev, "import3", True)
        self.assertEqual(self.core.export_json()["events"], before)

    def test_different_learner_identity_rejected_even_same_profile(self):
        self.core.import_v5(self.legacy(), 0, "i1", True)
        self.resume()
        self.assert_error("source_identity_conflict", self.core.import_v5,
                          self.legacy(id="someone-else"), self.rev, "i2", True)

    def test_bad_import_no_partial_event_write(self):
        e = self.legacy_attempt()
        raw = self.legacy([e, {**e, "id": "a2", "seconds": -1}])
        self.assert_error("legacy_invalid", self.core.import_v5, raw, 0, "i1", True)
        self.assertEqual(self.resume()["event_count"], 0)

    def test_large_unknown_profile_and_draft_retained_but_not_loaded_whole(self):
        raw = self.legacy(profile={"subjects":["math1"],"minutes":15,"future_notes":"資料"*12000},
                          draft={"future_notes":"草稿"*10000})
        self.core.import_v5(raw,0,"large-import",True)
        result = self.resume()
        self.assertTrue(result["profile"]["details_truncated"])
        self.assertLess(len(json.dumps(result,ensure_ascii=False)),3000)
        page = self.core.read_learning_records(self.sid,include_legacy_draft=True)
        self.assertTrue(page["legacy_draft_truncated"])
        for kind,key in [("profile","profile"),("legacy_draft","draft")]:
            fragments,offset=[],0
            while True:
                part=self.core.read_record_detail(self.sid,kind,offset=offset,limit=4000)
                fragments.append(part["json_fragment"]);offset=part["next_offset"]
                if not part["has_more"]:break
            self.assertEqual(json.loads("".join(fragments)),json.loads(raw)["state"][key])

    def test_profile_update_preserves_legacy_unknowns_and_unmentioned_goal(self):
        profile={"subjects":["math1"],"minutes":15,"goal":"已有目标","future_notes":{"preferred":"慢一点"}}
        self.core.import_v5(self.legacy(profile=profile),0,"import",True)
        self.resume()
        self.core.set_profile(self.sid,self.rev,"update",{"subjects":["math2"],"minutes":5})
        saved=self.core.export_json()["profile"]
        self.assertEqual(saved,{**profile,"subjects":["math2"],"minutes":5})
        event=self.core.export_json()["events"][-1]
        self.assertEqual(event["profile_patch"],{"subjects":["math2"],"minutes":5})
        self.assertEqual(event["operation"],"merge_preserving_unknown")


class MediaBackupTests(CoreCase):
    def import_text(self):
        (self.vault/"inbox"/"note.txt").write_text("原件保持；こんにちは", encoding="utf-8")
        receipt = self.core.import_material(self.sid, self.rev, "media1", "note.txt")
        self.rev = receipt["revision"]
        return receipt["resource_id"]

    def test_copy_hash_and_bounded_read(self):
        resource = self.import_text()
        (self.vault/"inbox"/"note.txt").write_text("changed")
        read = self.core.read_material(self.sid, resource, limit=3)
        self.assertEqual(read["text"], "原件保")
        self.assertTrue(read["has_more"])
        rest = self.core.read_material(self.sid, resource, offset=3)
        self.assertEqual(read["text"] + rest["text"], "原件保持；こんにちは")
        self.assertEqual(self.resume()["event_count"], 1)

    def test_path_traversal_and_symlink_refused(self):
        private = self.base/"private.txt";private.write_text("secret")
        self.assert_error("unsafe_path", self.core.import_material, self.sid, 0, "x", "../private.txt")
        (self.vault/"inbox"/"linked.txt").symlink_to(private)
        self.assert_error("material_missing", self.core.import_material, self.sid, 0, "x", "linked.txt")
        self.assertEqual(self.resume()["event_count"], 0)

    def test_missing_or_corrupt_registered_asset(self):
        resource = self.import_text()
        path = next((self.vault/"originals").glob("*.txt"))
        path.write_text("tampered")
        self.assert_error("material_corrupt", self.core.read_material, self.sid, resource)
        self.assert_error("material_corrupt", self.core.backup, self.base/"bad-backup")
        self.assertFalse((self.base/"bad-backup").exists())
        path.unlink()
        self.assertIn(resource, self.resume()["missing_artifacts"])
        self.assert_error("material_missing", self.core.read_material, self.sid, resource)

    def test_interrupted_registration_preserves_source_without_false_receipt(self):
        (self.vault/"inbox"/"note.txt").write_text("original")
        with patch.object(self.core, "insert_event", side_effect=CoreError("simulated_failure","stop")):
            self.assert_error("simulated_failure", self.core.import_material,
                              self.sid,0,"media1","note.txt")
        self.assertEqual(self.resume()["event_count"], 0)
        with self.core.connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0],0)
        self.assertEqual((self.vault/"inbox"/"note.txt").read_text(),"original")
        recovered = self.core.import_material(self.sid,0,"media1","note.txt")
        self.assertTrue(recovered["saved"])
        self.assertEqual(self.resume()["event_count"],1)

    def test_backup_restore_equivalence_and_new_session(self):
        resource = self.import_text()
        self.save(self.attempt())
        self.review()
        self.plan(basis=["a1"])
        before = self.core.export_json()
        destination = self.base/"backup"
        receipt = self.core.backup(destination)
        self.assertTrue(receipt["backup_complete"])
        self.assertFalse(receipt["encrypted"])
        restored = self.base/"restored"
        result = Core.restore(destination,restored)
        self.assertTrue(result["restored"])
        c = Core(restored)
        after = c.export_json()
        for key in ("events","revision","profile","artifacts","legacy_sources"):
            self.assertEqual(before[key],after[key])
        self.assert_error("resume_required",c.read_material,self.sid,resource)
        new = c.resume_learning()
        self.assertEqual(c.read_material(new["session_id"],resource)["text"],"原件保持；こんにちは")

    def test_restore_corruption_traversal_missing_and_existing_destination(self):
        self.import_text()
        destination = self.base/"backup"
        self.core.backup(destination)
        self.assert_error("destination_exists",Core.restore,destination,self.vault)
        manifest = json.loads((destination/"manifest.json").read_text())
        broken = copy.deepcopy(manifest);broken["files"][1]["path"]="../outside"
        (destination/"manifest.json").write_text(json.dumps(broken))
        self.assert_error("unsafe_path",Core.restore,destination,self.base/"restore1")
        self.assertFalse((self.base/"restore1").exists())
        (destination/"manifest.json").write_text(json.dumps(manifest))
        (destination/manifest["files"][1]["path"]).write_text("bad")
        self.assert_error("backup_corrupt",Core.restore,destination,self.base/"restore2")
        self.assertFalse((self.base/"restore2").exists())

    def test_pause_closes_same_session_first_exposure_exception(self):
        self.save(dict(id="stem",type="exposure",itemId=self.item["id"],level="stem",source="codex_report"))
        self.plan()
        self.save(self.attempt())
        self.review()
        rows=self.resume()["evidence"]
        self.assertTrue(rows)
        self.assertTrue(all(row["stage"]==0 and row["independent_total"]==0 for row in rows))

    def test_all_legacy_snapshots_readable_without_overwriting_active_draft(self):
        first=self.legacy(draft={"work":"OLD"})
        second=self.legacy(draft={"work":"NEW"},revision=38)
        a=self.core.import_v5(first,0,"import1",True)
        b=self.core.import_v5(second,a["revision"],"import2",True)
        r=self.resume()
        self.assertEqual(r["legacy_source_count"],2)
        records=self.core.read_learning_records(self.sid,include_legacy_draft=True)
        self.assertEqual(records["legacy_draft"],{"work":"OLD"})
        self.assertEqual(records["legacy_sources"][0]["source_sha256"],b["source_sha256"])
        index=json.loads(self.core.read_record_detail(self.sid,"legacy_sources")["json_fragment"])
        self.assertEqual(len(index),2)
        raw="";offset=0
        while True:
            part=self.core.read_record_detail(self.sid,"legacy_source",offset=offset,limit=17,source_sha256=b["source_sha256"])
            raw+=part["json_fragment"]
            if not part["has_more"]:break
            offset=part["next_offset"]
        self.assertEqual(raw,second)
        self.assert_error("invalid_source",self.core.read_record_detail,self.sid,"legacy_source",source_sha256="../private")

    def test_material_reports_live_dispute_and_correction_status(self):
        self.save(dict(id="issue1",type="issue",itemId=self.item["id"],reason="question conflict"))
        for section in ("stem","coach"):
            read=self.core.read_material(self.sid,self.item["id"],section)
            self.assertEqual(read["status"],"disputed")
            self.assertEqual(read["catalog_status"],"reviewed_original")
            self.assertFalse(read["usable_for_assessment"])
        self.save(dict(id="resolve1",type="resolve",issueId="issue1",itemId=self.item["id"],reference="synthetic reference",explanation="synthetic correction",outcome="corrected"))
        read=self.core.read_material(self.sid,self.item["id"],"coach")
        self.assertEqual(read["status"],"corrected_local")
        self.assertFalse(read["usable_for_assessment"])


if __name__ == "__main__":
    unittest.main()


"""Focus selection uses synthetic catalogs/media and temporary learning vaults."""
import copy
import hashlib
import json
import threading
import unittest
from unittest.mock import patch

import test_practice_loop as fixtures
from eju_core.evidence import evidence_map
from eju_core.errors import CoreError
from eju_core.practice_focus import guidance_sha256
from eju_core.store import Core, canonical, sha


class FocusFixtures:
    item = fixtures.PracticeLoopTests.item
    configure = fixtures.PracticeLoopTests.configure
    save = fixtures.PracticeLoopTests.save
    attempt = fixtures.PracticeLoopTests.attempt
    append = fixtures.PracticeLoopTests.append
    snapshot = fixtures.PracticeLoopTests.snapshot
    assert_error = fixtures.PracticeLoopTests.assert_error
    complete_source_with_reflection = fixtures.PracticeLoopTests.complete_source_with_reflection

    def setUp(self):
        fixtures.PracticeLoopTests.setUp(self)
        self.catalog["subjects"] = {"japanese_reading": "日语", "integrated": "综合", "math1": "数学1"}
        for item in self.catalog["items"]:
            item["subjects"] = ["japanese_reading"]
        self.catalog["items"].append(self.item("social", ["n.other"], subjects=["integrated"]))
        self.configure(self.core)
        self.focus_path = self.root / "practice_focus.json"
        patch("eju_core.practice_focus.FOCUS_PATH", self.focus_path).start()
        self.focus_map = {
            "schema_version": 1,
            "focuses": [
                {"id": "jr.evidence", "subject": "japanese_reading", "label": "明确依据", "description": "练习查找原文依据。"},
                {"id": "jr.main", "subject": "japanese_reading", "label": "文章主旨", "description": "练习辨认文章主旨。"},
                {"id": "in.graph", "subject": "integrated", "label": "图表资料", "description": "练习读懂图表资料。"}],
            "items": {item_id: self.binding(item_id, focus_id) for item_id, focus_id in (
                ("shared", "jr.evidence"), ("extra", "jr.evidence"), ("coarse", "jr.main"), ("social", "in.graph"))},
        }
        self.write_focus()

    def binding(self, item_id, focus_id="jr.evidence"):
        return {"item_version": self.core.items[item_id]["version"], "reviewed": True,
                "focus_ids": [focus_id], "rationale": "SECRET_RATIONALE_" + item_id,
                "basis": {"guidance_sha256": guidance_sha256(self.core.coaching[item_id]),
                          "private_note": "SECRET_BASIS_" + item_id}}

    def write_focus(self):
        self.focus_path.write_text(json.dumps(self.focus_map, ensure_ascii=False), encoding="utf-8")

    def preview(self, focus_id=None, basis=None, subject="japanese_reading"):
        return self.core.get_next_practice(self.sid, subject, basis, focus_id)

    def params(self, preview, request_id="focused-next", **overrides):
        candidate = preview["candidates"][0]
        params = dict(session_id=self.sid, expected_revision=preview["revision"], request_id=request_id,
                      item_id=candidate["item_id"], item_version=candidate["item_version"], subject=preview["subject"],
                      base_task_id=preview["base_task_id"], basis_attempt_id=preview["basis_attempt_id"],
                      basis_reflection_id=preview["basis_reflection_id"],
                      focus_id=(preview["selected_focus"] or {}).get("id"),
                      expected_focus_version=preview["focus_version"] if preview["selected_focus"] else None)
        params.update(overrides)
        return params

    def start(self, preview, **overrides):
        result = self.core.start_next_practice(**self.params(preview, **overrides))
        self.rev = result["revision"]
        return result


class PracticeFocusTests(FocusFixtures, unittest.TestCase):
    def test_explicit_filter_public_allowlist_and_query_has_no_writes(self):
        before = self.snapshot()
        ordinary = self.preview()
        focused = self.preview("jr.evidence")
        self.assertEqual([row["item_id"] for row in ordinary["candidates"]], ["coarse", "shared", "extra"])
        self.assertEqual([row["item_id"] for row in focused["candidates"]], ["shared", "extra"])
        self.assertEqual(focused["focus_version"], hashlib.sha256(self.focus_path.read_bytes()).hexdigest())
        self.assertIsNone(ordinary["selected_focus"])
        self.assertEqual(focused["selected_focus"], focused["focus_options"][0])
        self.assertEqual({row["id"] for row in focused["focus_options"]}, {"jr.evidence", "jr.main"})
        for row in focused["focus_options"] + [focused["selected_focus"]]:
            self.assertEqual(set(row), {"id", "label", "description"})
        self.assertNotIn("SECRET_", json.dumps(focused))
        self.assertNotIn("guidance_sha256", json.dumps(focused))
        self.assertEqual(self.snapshot(), before)
        with self.core.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM requests").fetchone()[0], 0)

    def test_no_auto_focus_from_saved_reflection_or_free_text(self):
        self.attempt()
        self.append(dict(id="self-report", type="reflection", attemptId="attempt", itemId="source",
                         itemVersion="v1-source", cause="evidence", evidence="jr.evidence 弱项？",
                         next_action="jr.main", source="learner_self_report"))
        result = self.preview(basis="attempt")
        self.assertIsNone(result["selected_focus"])
        self.assertEqual(result["basis_reflection_id"], "self-report")
        self.assertTrue(result["basis"]["self_report"])
        self.assertEqual(len(result["candidates"]), 3)

    def test_subject_options_and_unknown_wrong_subject_or_invalid_focus_fail(self):
        result = self.preview("in.graph", subject="integrated")
        self.assertEqual([row["item_id"] for row in result["candidates"]], ["social"])
        self.assertEqual([row["id"] for row in result["focus_options"]], ["in.graph"])
        self.assertEqual(self.preview(subject="math1")["focus_options"], [])
        for value in ("in.graph", "missing", "", [], {}, True):
            with self.subTest(value=value):
                self.assert_error("focus_unavailable", self.preview, value)
        self.assert_error("focus_unavailable", self.preview, "jr.evidence", subject="math1")

    def test_missing_invalid_or_duplicate_schema_does_not_broaden_focused_request(self):
        expected = self.preview()["candidates"]
        invalid = [b"broken", b"[]", b"{}", b'{"schema_version":1,"schema_version":1,"focuses":[],"items":{}}']
        variants = []
        for field, value in (("schema_version", True), ("schema_version", 2), ("items", []), ("focuses", {})):
            bad = copy.deepcopy(self.focus_map); bad[field] = value; variants.append(bad)
        for field, value in (("subject", "math1"), ("subject", []), ("label", ""), ("id", [])):
            bad = copy.deepcopy(self.focus_map); bad["focuses"][0][field] = value; variants.append(bad)
        duplicate = copy.deepcopy(self.focus_map); duplicate["focuses"].append(duplicate["focuses"][0]); variants.append(duplicate)
        invalid += [json.dumps(value).encode() for value in variants]
        before = self.snapshot()
        for raw in [None, *invalid]:
            with self.subTest(raw=raw):
                if raw is None:
                    self.focus_path.unlink()
                else:
                    self.focus_path.write_bytes(raw)
                ordinary = self.preview()
                self.assertEqual(ordinary["candidates"], expected)
                self.assertEqual(ordinary["focus_options"], [])
                self.assertIsNone(ordinary["focus_version"])
                self.assert_error("focus_unavailable", self.preview, "jr.evidence")
        self.assertEqual(self.snapshot(), before)

    def test_stale_unreviewed_and_malformed_item_bindings_never_match(self):
        initial = copy.deepcopy(self.focus_map)
        mutations = [
            lambda row: row.update(item_version="old-version"), lambda row: row.update(reviewed=False),
            lambda row: row.update(reviewed=1), lambda row: row.update(rationale=""),
            lambda row: row.update(focus_ids=["jr.evidence", "missing"]),
            lambda row: row.update(focus_ids=["jr.evidence", "in.graph"]),
            lambda row: row.update(focus_ids=["jr.evidence", "jr.evidence"]),
            lambda row: row.update(focus_ids=[{}]), lambda row: row.update(focus_ids="jr.evidence"),
            lambda row: row.update(basis={"guidance_sha256": "f" * 64}), lambda row: row.update(basis=None),
        ]
        for mutation in mutations:
            self.focus_map = copy.deepcopy(initial)
            mutation(self.focus_map["items"]["shared"])
            self.write_focus()
            self.assertEqual([row["item_id"] for row in self.preview("jr.evidence")["candidates"]], ["extra"])
        self.focus_map = initial
        self.focus_map["items"]["shared"] = None
        del self.focus_map["items"]["extra"]
        self.write_focus()
        result = self.preview("jr.evidence")
        self.assertEqual(result["candidates"], [])
        self.assertTrue(result["no_candidate"])
        self.assertIn("符合所选重点", result["message"])
        self.assertIn("取消重点", result["message"])
        self.assertTrue(self.preview()["candidates"])

    def test_current_guidance_and_item_identity_are_required(self):
        for mutate in (
            lambda: self.core.items["shared"].update(version="v2"),
            lambda: self.core.coaching["shared"].update(item_version="v2"),
            lambda: self.core.coaching["shared"].update(reviewed=False),
            lambda: self.core.coaching["shared"].update(questionFocus="changed"),
            lambda: self.core.coaching.pop("shared"),
        ):
            self.configure(self.core)
            mutate()
            self.assertEqual([row["item_id"] for row in self.preview("jr.evidence")["candidates"]], ["extra"])

    def test_complete_reviewed_unseen_and_shared_page_guards_still_apply(self):
        self.focus_map["items"]["coarse"] = self.binding("coarse")
        self.write_focus()
        shared, coarse = self.core.items["shared"], self.core.items["coarse"]
        coarse["delivery"]["source_sha256"] = shared["delivery"]["source_sha256"]
        self.assertEqual([row["item_id"] for row in self.preview("jr.evidence")["candidates"]], ["coarse", "extra"])
        self.save(dict(id="seen", type="exposure", itemId="shared", level="stem", source="learner_report"))
        self.assertEqual([row["item_id"] for row in self.preview("jr.evidence")["candidates"]], ["extra"])
        (self.media / "extra.png").unlink()
        self.assertEqual(self.preview("jr.evidence")["candidates"], [])

    def test_ranking_and_three_candidate_cap_within_matches(self):
        self.catalog["items"] += [self.item("fourth", ["n.other"], order=1, subjects=["japanese_reading"]),
                                  self.item("fifth", ["n.other"], order=2, subjects=["japanese_reading"])]
        self.configure(self.core)
        self.focus_map["items"].update({key: self.binding(key) for key in ("fourth", "fifth")})
        self.write_focus()
        self.attempt()
        result = self.preview("jr.evidence", basis="attempt")
        self.assertEqual([row["item_id"] for row in result["candidates"]], ["shared", "fourth", "fifth"])
        self.assertEqual(result["candidates"][0]["relation"], "shared_nodes")

    def test_start_persists_focus_with_no_exposure_or_profile_evidence_changes(self):
        result = self.preview("jr.evidence")
        before = self.snapshot()
        receipt = self.start(result)
        after = self.snapshot()
        self.assertFalse(receipt["learner_exposure_recorded"])
        self.assertEqual(after["profile"], before["profile"])
        event = after["events"][-1]
        self.assertEqual((event["focus_id"], event["focus_version"]), ("jr.evidence", result["focus_version"]))
        self.assertNotIn("SECRET_", json.dumps(event))
        self.assertFalse(any(row["type"] == "exposure" for row in after["events"]))
        self.assertEqual(evidence_map(after["events"], self.core.catalog["items"], self.core.catalog["nodes"]),
                         evidence_map(before["events"], self.core.catalog["items"], self.core.catalog["nodes"]))

    def test_start_requires_exact_preview_version_and_candidate_within_focus(self):
        result = self.preview("jr.evidence")
        before = self.snapshot()
        for version in (None, "", "f" * 64, [], True):
            self.assert_error("focus_changed", self.start, result, expected_focus_version=version)
        self.assert_error("focus_changed", self.start, result, focus_id=None)
        self.assert_error("candidate_unavailable", self.start, result, item_id="coarse", item_version="v1-coarse")
        self.assertEqual(self.snapshot(), before)

    def test_map_change_deletion_or_removed_definition_blocks_uncommitted_start(self):
        result = self.preview("jr.evidence")
        before = self.snapshot()
        self.focus_path.write_bytes(self.focus_path.read_bytes() + b"\n")
        self.assert_error("focus_changed", self.start, result)
        self.focus_path.unlink()
        self.assert_error("focus_unavailable", self.start, result)
        self.focus_map["focuses"] = self.focus_map["focuses"][1:]
        self.write_focus()
        self.assert_error("focus_unavailable", self.start, result)
        self.assertEqual(self.snapshot(), before)

    def test_start_revalidates_guidance_media_item_status_and_seen(self):
        result = self.preview("jr.evidence")
        self.core.coaching["shared"]["reviewed"] = False
        self.assert_error("candidate_unavailable", self.start, result)
        self.configure(self.core)
        self.core.items["shared"]["status"] = "indexed"
        self.assert_error("candidate_unavailable", self.start, result)
        self.configure(self.core)
        self.core.items["shared"]["version"] = "v2"
        self.assert_error("item_version_conflict", self.start, result)
        self.configure(self.core)
        self.save(dict(id="seen", type="exposure", itemId="shared", level="stem", source="learner_report"))
        self.assert_error("revision_conflict", self.start, result)
        self.assert_error("candidate_unavailable", self.start, result, expected_revision=self.rev)
        self.assertFalse(any(row["type"] == "task" for row in self.snapshot()["events"]))

    def test_task_and_reflection_conflicts_remain_atomic(self):
        basis = self.complete_source_with_reflection()
        result = self.preview("jr.evidence", basis=basis)
        changed = self.core.workbench_action(self.sid, self.rev, "reflection-two", "source-task", "reflect",
                                            dict(cause="not_sure", evidence="新复盘", base_reflection_id="reflection-one"))
        self.rev = changed["revision"]
        self.assert_error("revision_conflict", self.start, result)
        self.assert_error("basis_changed", self.start, result, expected_revision=self.rev)
        self.start(self.preview("jr.evidence", basis=basis))
        result = self.preview("jr.evidence", basis=basis)
        before = self.snapshot()
        self.assert_error("task_changed", self.start, result, request_id="changed", base_task_id=None)
        self.assert_error("task_active", self.start, result, request_id="active")
        self.assertEqual(self.snapshot(), before)
        paused = self.core.workbench_action(self.sid, self.rev, "pause", "focused-next", "pause")
        self.rev = paused["revision"]
        self.assert_error("task_paused", self.start, self.preview("jr.evidence"), request_id="paused")

    def test_committed_replay_precedes_changed_deleted_map_and_dynamic_state(self):
        result = self.preview("jr.evidence")
        saved = self.start(result)
        self.core.workbench_action(self.sid, self.rev, "later-show", "focused-next", "show")
        self.core.items["shared"]["version"] = "v2"
        self.focus_path.write_bytes(b"broken")
        replay = self.start(result)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["revision"], saved["revision"])
        self.assertGreater(replay["current_revision"], saved["revision"])
        self.focus_path.unlink()
        self.assertTrue(self.start(result)["replayed"])
        for overrides in ({"focus_id": "jr.main"}, {"expected_focus_version": "f" * 64},
                          {"focus_id": None, "expected_focus_version": None}):
            self.assert_error("id_conflict", self.start, result, **overrides)
        self.assertEqual(sum(row["type"] == "task" for row in self.snapshot()["events"]), 1)

    def test_beta6_unfiltered_receipt_digest_replays_after_upgrade(self):
        result = self.preview()
        params = self.params(result, request_id="beta6-start")
        # Construct an actual committed Beta6 request shape, not a new Beta7 call.
        payload = {key: params[key] for key in ("item_id", "item_version", "subject", "base_task_id",
                                               "basis_attempt_id", "basis_reflection_id")}
        digest = sha(canonical({"operation": "start_next_practice", "payload": payload}).encode())
        with self.core.connection(write=True) as db:
            self.core.insert_event(db, dict(id="beta6-start", type="task", itemId=params["item_id"],
                                           itemVersion=params["item_version"], instruction="旧版任务"), "native_workbench")
            self.core.finish_request(db, "beta6-start", digest, task_id="beta6-start", learner_exposure_recorded=False)
        self.focus_path.unlink()
        replay = self.core.start_next_practice(**params)
        self.assertTrue(replay["replayed"])
        self.assertEqual(len(self.snapshot()["events"]), 1)
        self.assertNotIn("focus_id", self.snapshot()["events"][0])

    def test_two_focused_clients_have_one_atomic_winner(self):
        preview = self.preview("jr.evidence")
        other = self.configure(Core(self.vault))
        other_sid = other.resume_learning()["session_id"]
        barrier = threading.Barrier(2)
        results = []
        def start(core, session_id, request_id):
            barrier.wait(timeout=10)
            try:
                results.append(core.start_next_practice(**self.params(preview, session_id=session_id, request_id=request_id)))
            except CoreError as exc:
                results.append(exc.result())
        threads = [threading.Thread(target=start, args=(self.core, self.sid, "focus-one")),
                   threading.Thread(target=start, args=(other, other_sid, "focus-two"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(row["ok"] for row in results), 1)
        self.assertEqual([row["error"]["code"] for row in results if not row["ok"]], ["revision_conflict"])
        events = self.snapshot()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["focus_id"], "jr.evidence")


if __name__ == "__main__":
    unittest.main()

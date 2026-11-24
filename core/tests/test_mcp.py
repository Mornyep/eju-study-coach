import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE/"src"))
from eju_core.store import Core
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.vault = self.base/"vault"
        Core.initialize(self.vault)

    @asynccontextmanager
    async def connect(self):
        params = StdioServerParameters(command=sys.executable,
                                      args=[str(CORE/"run.py"),"--vault",str(self.vault),"serve"])
        with (self.base/"server-stderr.log").open("a", encoding="utf-8") as log:
            async with stdio_client(params,errlog=log) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=15) as session:
                    result = await session.initialize()
                    self.assertEqual(result.server_info.name,"eju-learning-core")
                    yield session

    async def tool(self, session, name, args=None):
        result = await session.call_tool(name,args or {})
        raw = result.model_dump(by_alias=True)
        self.assertFalse(raw.get("isError",False),raw)
        if raw.get("structuredContent"):
            payload=raw["structuredContent"]
        else:
            payload=json.loads(next(x["text"] for x in raw["content"] if x["type"]=="text"))
        # SDKs may wrap a generic dict annotation under result.
        return payload.get("result",payload)

    async def test_actual_stdio_list_save_reopen_and_idempotency(self):
        async with self.connect() as s:
            listing = await s.list_tools()
            names = {t.name for t in listing.tools}
            self.assertEqual(names, {"resume_learning","set_profile","get_task_candidates","read_material",
                                     "read_learning_records","record_learning_event","save_learning_plan",
                                     "import_material","prepare_feedback","read_record_detail",
                                     "present_learning_task","read_workbench","workbench_action","extract_document",
                                     "read_documents","review_document_segment","read_document_page",
                                     "create_recall_card","read_recall_cards","start_recall","record_recall_event","review_recall_attempt","read_question_images",
                                     "read_writing_drafts","read_writing_resume","read_writing_context","save_writing_draft","save_writing_feedback",
                                     "read_study_review","get_next_practice","read_review_queue","read_coaching_context","save_coaching_note"})
            r = await self.tool(s,"resume_learning")
            self.assertTrue(r["ok"])
            sid = r["session_id"]
            p = await self.tool(s,"set_profile",{"session_id":sid,"expected_revision":0,"request_id":"p1",
                        "profile":{"subjects":["math1"],"minutes":5,"goal":"条件概率"}})
            self.assertTrue(p["saved"])
            candidates=await self.tool(s,"get_task_candidates",{"session_id":sid,"minutes":5,"purpose":"prerequisite"})
            item=candidates["candidates"][0]
            stem=await self.tool(s,"read_material",{"session_id":sid,"resource_id":item["item_id"]})
            self.assertTrue(stem["available"])
            self.assertNotIn("answer",stem)
            saved=await self.tool(s,"save_learning_plan",{"session_id":sid,"expected_revision":1,
                        "request_id":"plan1","plan":{"objective":"条件集合","next_action":"先独立写出条件集合",
                        "reason":"只有5分钟，先做前置检查。","status":"paused","basis_event_ids":["p1"],
                        "item_ids":[item["item_id"]]}})
            self.assertEqual(saved["revision"],2)
        # A completely new process/session must read the same durable state.
        async with self.connect() as s:
            r=await self.tool(s,"resume_learning")
            self.assertEqual(r["revision"],2)
            self.assertEqual(r["last_plan"]["status"],"paused")
            self.assertEqual(r["last_plan"]["next_action"],"先独立写出条件集合")
            self.assertEqual(r["event_count"],2)
            again=await self.tool(s,"set_profile",{"session_id":r["session_id"],"expected_revision":0,
                        "request_id":"p1","profile":{"subjects":["math1"],"minutes":5,"goal":"条件概率"}})
            self.assertTrue(again["replayed"])
            self.assertEqual(again["current_revision"],2)

    async def test_integrated_supplement_and_explicit_prerequisite_over_actual_stdio(self):
        async with self.connect() as session:
            listing = await session.list_tools()
            selection = next(t for t in listing.tools if t.name == "get_task_candidates")
            self.assertEqual(selection.model_dump(by_alias=True)["inputSchema"]["properties"]["purpose"]["default"], "study")
            resume = await self.tool(session, "resume_learning")
            sid = resume["session_id"]
            candidates = await self.tool(session, "get_task_candidates", {
                "session_id": sid, "subject": "integrated", "minutes": 5, "purpose": "supplement"})
            item = candidates["candidates"][0]
            self.assertEqual(item["practice_scope"], "exam_style_original")
            self.assertFalse(item["item_id"].startswith("CG-I-"))
            stem = await self.tool(session, "read_material", {
                "session_id": sid, "resource_id": item["item_id"]})
            self.assertTrue(stem["available"])
            self.assertNotIn("answer", stem)
            self.assertNotIn("exam_alignment_review", stem)
            coach = await self.tool(session, "read_material", {
                "session_id": sid, "resource_id": item["item_id"], "section": "coach"})
            self.assertEqual(coach["exam_alignment_review"]["status"], "reviewed")
            foundation = await self.tool(session, "get_task_candidates", {
                "session_id": sid, "subject": "integrated", "purpose": "prerequisite"})
            self.assertEqual(foundation["candidates"][0]["item_id"], "CG-I-F")
            self.assertEqual((await self.tool(session, "resume_learning"))["revision"], 0)

    async def test_coaching_durable_across_processes_and_stale_basis(self):
        core=Core(self.vault);item=core.items["STEM-M1-CP-F"]
        async with self.connect() as session:
            resume=await self.tool(session,"resume_learning");sid=resume["session_id"]
            await self.tool(session,"record_learning_event",{"session_id":sid,"expected_revision":0,
                "event":{"id":"note-attempt","type":"attempt","itemId":item["id"],"itemVersion":item["version"],
                         "answer":"2/3","reason":"限定条件中的3张，再数2张偶数。","result":"correct","hint":"H0",
                         "seenBefore":False,"guessed":False,"seconds":None,"grading":"automatic"}})
            await self.tool(session,"record_learning_event",{"session_id":sid,"expected_revision":1,
                "event":{"id":"note-reason","type":"reason_review","targetId":"note-attempt","agrees":True,
                         "reviewer":"tutor","note":"已核对条件集合与分子。"}})
            context=await self.tool(session,"read_coaching_context",{"session_id":sid,"attempt_id":"note-attempt"})
            self.assertEqual(context["context"]["basis_reason_review_id"],"note-reason")
            args={"session_id":sid,"expected_revision":2,"request_id":"durable-note","attempt_id":"note-attempt",
                  "summary":"已解释条件集合的分母只数满足条件的卡片。","next_step":"先口头说明分母，再找未见题。",
                  "hypothesis":"是否把原样本空间与条件集合混用仍需验证。","open_question":"", "basis_reason_review_id":"note-reason"}
            saved=await self.tool(session,"save_coaching_note",args);self.assertTrue(saved["saved"])
            self.assertEqual(saved["revision"],3)
        async with self.connect() as session:
            resume=await self.tool(session,"resume_learning");sid=resume["session_id"]
            self.assertEqual(resume["review_queue"]["checked_count"],1)
            self.assertEqual(resume["review_queue"]["entries"][0]["coaching_note"]["summary"],args["summary"])
            context=await self.tool(session,"read_coaching_context",{"session_id":sid,"attempt_id":"note-attempt"})
            self.assertEqual(context["context"]["coaching_note"]["hypothesis"],args["hypothesis"])
            self.assertNotIn("solution",context["context"])
            await self.tool(session,"record_learning_event",{"session_id":sid,"expected_revision":3,
                "event":{"id":"note-reason-2","type":"reason_review","targetId":"note-attempt","agrees":False,
                         "reviewer":"tutor","note":"新的核对发现仍有遗漏。"}})
            replay=await self.tool(session,"save_coaching_note",{**args,"session_id":sid})
            self.assertTrue(replay["replayed"]);self.assertEqual(replay["current_revision"],4)
            queue=await self.tool(session,"read_review_queue",{"session_id":sid})
            self.assertEqual(queue["pending_count"],1);self.assertTrue(queue["entries"][0]["coaching_note"]["stale"])
            conflict=await self.tool(session,"save_coaching_note",{**args,"session_id":sid,"expected_revision":4,
                "request_id":"conflicting-note","base_note_id":"durable-note"})
            self.assertFalse(conflict["ok"]);self.assertEqual(conflict["error"]["code"],"basis_changed")
            self.assertEqual((await self.tool(session,"resume_learning"))["revision"],4)

    async def test_conflict_invalid_session_and_material_escape_are_explicit(self):
        async with self.connect() as s:
            r=await self.tool(s,"resume_learning")
            invalid=await self.tool(s,"get_task_candidates",{"session_id":"not-resumed"})
            self.assertFalse(invalid["ok"])
            self.assertEqual(invalid["error"]["code"],"resume_required")
            bad=await self.tool(s,"import_material",{"session_id":r["session_id"],
                             "expected_revision":0,"request_id":"bad","inbox_name":"../private.txt"})
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["error"]["code"],"unsafe_path")
            conflict=await self.tool(s,"set_profile",{"session_id":r["session_id"],
                          "expected_revision":9,"request_id":"p1","profile":{"subjects":["math1"],"minutes":10}})
            self.assertFalse(conflict["ok"])
            self.assertEqual(conflict["error"]["code"],"revision_conflict")
            again=await self.tool(s,"resume_learning")
            self.assertEqual(again["revision"],0)

    async def test_actual_event_reason_and_hint_floor(self):
        async with self.connect() as s:
            r=await self.tool(s,"resume_learning");sid=r["session_id"]
            core=Core(self.vault);item=core.items["STEM-M1-CP-F"]
            h=await self.tool(s,"record_learning_event",{"session_id":sid,"expected_revision":0,
                         "event":{"id":"hint1","type":"exposure","itemId":item["id"],"level":"hint",
                                  "hint":"H1","source":"codex_report"}})
            self.assertTrue(h["saved"])
            a=await self.tool(s,"record_learning_event",{"session_id":sid,"expected_revision":1,
                         "event":{"id":"a1","type":"attempt","itemId":item["id"],"itemVersion":item["version"],
                                  "answer":"2/3","reason":"条件下3枚，偶数2枚。","result":"correct","hint":"H0",
                                  "seenBefore":False,"guessed":False,"seconds":None,"grading":"automatic"}})
            self.assertTrue(a["saved"]);self.assertEqual(a["applied_hint"],"H1")
            check=await self.tool(s,"record_learning_event",{"session_id":sid,"expected_revision":2,
                         "event":{"id":"r1","type":"reason_review","targetId":"a1","agrees":True,
                                  "reviewer":"tutor","note":"对照完整题面与解答检查理由"}})
            self.assertTrue(check["saved"])
            current=await self.tool(s,"resume_learning")
            self.assertTrue(all(e["stage"]==0 for e in current["evidence"]))


if __name__ == "__main__":
    unittest.main()


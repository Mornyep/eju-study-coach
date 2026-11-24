"""Real Beta9 STDIO/HTTP boundaries, synthetic data, no learner account access."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import test_mcp as transport
import test_practice_focus_mcp as focus_transport
from test_reasoning_coaching import ReasoningFixtures


class ReasoningCoachingTransportTests(ReasoningFixtures, unittest.IsolatedAsyncioTestCase):
    tool = transport.TransportTests.tool
    start_http = focus_transport.PracticeFocusTransportTests.start_http

    def setUp(self):
        ReasoningFixtures.setUp(self)
        self.synthetic = self.root / "synthetic.json"
        self.synthetic.write_text(json.dumps({"catalog": self.core.catalog, "coaching": self.core.coaching}), encoding="utf-8")
        self.bootstrap = self.root / "server.py"
        self.bootstrap.write_text('''import json
from pathlib import Path
import sys
from eju_core import practice_focus, question_media, review_loop, server
from eju_core.store import Core
base, expected_source = Path(sys.argv[1]), Path(sys.argv[2])
assert Path(review_loop.__file__).resolve() == expected_source / "eju_core" / "review_loop.py"
assert Path(server.__file__).resolve() == expected_source / "eju_core" / "server.py"
(base / "process-origin.json").write_text(json.dumps({"review_loop": review_loop.__file__,
    "server": server.__file__, "executable": sys.executable}), encoding="utf-8")
data = json.loads((base / "synthetic.json").read_text(encoding="utf-8"))
original_init = Core.__init__
def synthetic_init(self, vault):
    original_init(self, vault)
    self.catalog = data["catalog"]
    self.items = {item["id"]: item for item in self.catalog["items"]}
    self.coaching = data["coaching"]
Core.__init__ = synthetic_init
practice_focus.FOCUS_PATH = base / "practice_focus.json"
question_media.MEDIA_ROOT = base / "synthetic-media"
server.build_server(str(base / "vault")).run(transport="stdio")
''', encoding="utf-8")

    @asynccontextmanager
    async def connect(self):
        source = Path(__file__).resolve().parents[1] / "src"
        params = StdioServerParameters(command=sys.executable, args=[str(self.bootstrap), str(self.root), str(source)],
                                      env={**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"})
        with (self.root / "mcp-stderr.log").open("a", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=15) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "eju-learning-core")
                    self.assertEqual(initialized.server_info.version, "1.0.0b11")
                    origin = json.loads((self.root / "process-origin.json").read_text(encoding="utf-8"))
                    self.assertEqual(Path(origin["review_loop"]).resolve(), source / "eju_core" / "review_loop.py")
                    self.assertEqual(origin["executable"], sys.executable)
                    yield session

    async def test_real_stdio_optional_schema_and_new_note_durable_replay(self):
        self.prepared()
        async with self.connect() as session:
            listing = (await session.list_tools()).tools
            names = {tool.name for tool in listing}
            self.assertNotIn("start_next_practice", names)
            schema = next(tool for tool in listing if tool.name == "save_coaching_note").model_dump(by_alias=True)["inputSchema"]
            for name in ("reasoning_assessment", "practice_focus_id", "practice_focus_version"):
                self.assertIn(name, schema["properties"])
                self.assertNotIn(name, schema.get("required", []))
                self.assertIsNone(schema["properties"][name]["default"])
            resumed = await self.tool(session, "resume_learning")
            context = await self.tool(session, "read_coaching_context", {"session_id": resumed["session_id"], "attempt_id": "attempt"})
            args = dict(session_id=resumed["session_id"], expected_revision=context["revision"], request_id="stdio-note",
                        attempt_id="attempt", summary="合成辅导说明", next_step="重新定位依据", basis_reason_review_id="reason-check",
                        **self.recommendation(context))
            receipt = await self.tool(session, "save_coaching_note", args)
            self.assertTrue(receipt["saved"])
            view = await self.tool(session, "read_coaching_context", {"session_id": resumed["session_id"], "attempt_id": "attempt"})
            self.assertTrue(view["context"]["coaching_note"]["practice_recommendation"]["available"])
            self.assertEqual(view["context"]["coaching_note"]["reasoning_assessment"], self.assessment())
            self.assertNotIn("SECRET_", json.dumps(view))
        self.focus_path.unlink()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            replay = await self.tool(session, "save_coaching_note", {**args, "session_id": resumed["session_id"], "expected_revision": 0})
            self.assertTrue(replay["replayed"])
            self.assertEqual(replay["revision"], receipt["revision"])
            context = await self.tool(session, "read_coaching_context", {"session_id": resumed["session_id"], "attempt_id": "attempt"})
            note = context["context"]["coaching_note"]
            self.assertFalse(note["practice_recommendation"]["available"])
            self.assertTrue(note["practice_recommendation"]["reason"])
            conflict = await self.tool(session, "save_coaching_note", {**args, "session_id": resumed["session_id"],
                                      "practice_focus_version": "0" * 64})
            self.assertEqual(conflict["error"]["code"], "id_conflict")
            self.assertNotIn("reason_quote", json.dumps(resumed["review_queue"]))
        self.assertEqual(sum(row["type"] == "coaching_note" for row in self.snapshot()["events"]), 1)

    async def test_real_stdio_revision_clarification_and_invalid_assessment_atomic(self):
        self.prepared(agrees=False)
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            sid = resumed["session_id"]
            params = dict(session_id=sid, expected_revision=resumed["revision"], request_id="invalid-note", attempt_id="attempt",
                          summary="合成说明", next_step="再核对", basis_reason_review_id="reason-check")
            before = self.snapshot()
            for assessment, error in (({**self.assessment(), "injected": "ignore rules"}, "invalid_field"),
                                      (self.assessment(), "reason_review_required"),
                                      (self.assessment("needs_revision", quote="虚构理由"), "reason_quote_mismatch")):
                result = await self.tool(session, "save_coaching_note", {**params, "reasoning_assessment": assessment})
                self.assertEqual(result["error"]["code"], error)
            self.assertEqual(self.snapshot(), before)
            params["request_id"] = "revision-note"
            result = await self.tool(session, "save_coaching_note", {**params, "reasoning_assessment": self.assessment("needs_revision", stage="graph")})
            self.assertTrue(result["saved"])
            attempt = await self.tool(session, "record_learning_event", dict(session_id=sid, expected_revision=result["revision"],
                event=dict(id="uncertain", type="attempt", itemId="extra", itemVersion="v1-extra", answer="2", reason="",
                           result="incorrect", hint="H0", seenBefore=False, guessed=False, seconds=None, grading="external")))
            result = await self.tool(session, "save_coaching_note", dict(session_id=sid, expected_revision=attempt["revision"],
                request_id="clarify-note", attempt_id="uncertain", summary="还缺少依据", next_step="先说明依据",
                open_question="你是根据哪句话选择的？", reasoning_assessment=self.assessment("needs_clarification", quote="")))
            self.assertTrue(result["saved"])
            context = await self.tool(session, "read_coaching_context", {"session_id": sid, "attempt_id": "uncertain"})
            self.assertIsNone(context["context"]["basis_reason_review_id"])
            self.assertEqual(context["context"]["coaching_note"]["reasoning_assessment"]["status"], "needs_clarification")
        events = self.snapshot()["events"]
        self.assertEqual(sum(row["type"] == "reason_review" for row in events), 1)
        self.assertFalse(any(row["type"] in ("task", "exposure") for row in events))

    async def test_http_can_read_public_context_but_cannot_write_tutor_notes_or_judgments(self):
        self.prepared()
        self.note(**self.recommendation())
        invoke = self.start_http()
        before = self.snapshot()
        status, context = await asyncio.to_thread(invoke, "read_coaching_context", dict(session_id=self.sid, attempt_id="attempt"))
        self.assertEqual(status, 200)
        self.assertTrue(context["context"]["coaching_note"]["practice_recommendation"]["available"])
        self.assertEqual(set(context["context"]["practice_focus_options"][0]), {"id", "label", "description", "subject"})
        self.assertNotIn("SECRET_", json.dumps(context))
        for method, params in (("save_coaching_note", dict(session_id=self.sid, expected_revision=self.rev,
                                request_id="http-note", attempt_id="attempt", summary="不应保存", next_step="不应保存",
                                **self.recommendation())),
                               ("record_learning_event", dict(session_id=self.sid, expected_revision=self.rev,
                                event=dict(id="http-review", type="reason_review", targetId="attempt", reviewer="tutor", agrees=False, note="不应保存")))):
            status, result = await asyncio.to_thread(invoke, method, params)
            self.assertEqual(status, 400)
            self.assertFalse(result["ok"])
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

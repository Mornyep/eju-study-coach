"""Actual source-pinned MCP sessions recover bounded writing and exact details."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import sys
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import test_mcp as transport
import test_writing_loop_mcp as writing_transport
from test_writing_loop import WritingFixtures


class WritingContinuityMCPTests(WritingFixtures, unittest.IsolatedAsyncioTestCase):
    setUp = writing_transport.WritingMCPTests.setUp
    tool = transport.TransportTests.tool

    @asynccontextmanager
    async def connect(self):
        source = Path(__file__).resolve().parents[1] / "src"
        params = StdioServerParameters(command=sys.executable, args=[str(self.bootstrap), str(self.root), str(source)],
            env={**os.environ, "PYTHONPATH": str(source), "PYTHONDONTWRITEBYTECODE": "1"})
        with (self.root / "mcp-stderr.log").open("a", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=15) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.version, "1.0.0b11")
                    origin = json.loads((self.root / "process-origin.json").read_text(encoding="utf-8"))
                    self.assertEqual(Path(origin["writing_loop"]).resolve(), source / "eju_core" / "writing_loop.py")
                    self.assertEqual(origin["executable"], sys.executable)
                    yield session

    async def test_actual_mcp_new_process_resume_exact_draft_context_and_feedback_state(self):
        text = " \r\nか\u3099・が・𠮷 本人が書いた原稿。" * 100
        async with self.connect() as session:
            tools = {tool.name: tool.model_dump(by_alias=True) for tool in (await session.list_tools()).tools}
            schema = tools["read_writing_resume"]
            self.assertTrue(schema["annotations"]["readOnlyHint"])
            self.assertEqual(schema["inputSchema"]["properties"]["limit"]["default"], 3)
            self.assertEqual(schema["inputSchema"]["required"], ["session_id"])
            resumed = await self.tool(session, "resume_learning")
            self.assertEqual(resumed["writing_resume"]["drafts"], [])
            listing = await self.tool(session, "read_writing_drafts", {"session_id": resumed["session_id"]})
            item = listing["available_items"][0]
            args = dict(session_id=resumed["session_id"], expected_revision=listing["revision"], request_id="mcp-original",
                item_id=item["item_id"], item_version=item["item_version"], text=text, source_fingerprint=item["source_fingerprint"])
            saved = await self.tool(session, "save_writing_draft", args)
            self.assertTrue(saved["saved"])
            feedback = await self.tool(session, "save_writing_feedback", dict(session_id=resumed["session_id"],
                expected_revision=saved["revision"], request_id="mcp-feedback", draft_id=saved["draft_id"],
                source_version_id=saved["version_id"], task_response="unclear", task_evidence_quote="本人が書いた原稿。",
                task_comment="题目的两种方法尚未说明。", issues=[]))
            self.assertTrue(feedback["saved"])
        before = self.snapshot()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            self.assertNotEqual(resumed["session_id"], args["session_id"])
            self.assertEqual(resumed["revision"], 2)
            row = resumed["writing_resume"]["drafts"][0]
            self.assertEqual((row["draft_id"], row["state"], row["current_feedback_id"]),
                             ("mcp-original", "feedback_available", "mcp-feedback"))
            self.assertNotIn(text, json.dumps(resumed, ensure_ascii=False))
            reading = await self.tool(session, "read_writing_resume", {"session_id": resumed["session_id"], "limit": 10})
            self.assertEqual(reading["drafts"], resumed["writing_resume"]["drafts"])
            detail = await self.tool(session, "read_writing_context", {"session_id": resumed["session_id"], "draft_id": row["draft_id"]})
            self.assertEqual(detail["context"]["original_version"]["text"], text)
            self.assertEqual(self.snapshot(), before)
            revised = await self.tool(session, "save_writing_draft", args | {"session_id": resumed["session_id"],
                "expected_revision": detail["revision"], "request_id": "mcp-revision", "draft_id": row["draft_id"],
                "base_version_id": row["latest_version_id"], "text": "本人の改稿。"})
            self.assertTrue(revised["saved"])
        before = self.snapshot()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            row = resumed["writing_resume"]["drafts"][0]
            self.assertEqual(row["latest_version_id"], "mcp-revision")
            self.assertEqual(row["state"], "awaiting_feedback")
            self.assertIsNone(row["current_feedback_id"])
            detail = await self.tool(session, "read_writing_context", {"session_id": resumed["session_id"], "draft_id": row["draft_id"]})
            self.assertTrue(detail["context"]["feedback_history"][0]["historical"])
            self.assertEqual(detail["context"]["comparison"]["before_text"], text)
            self.assertEqual(self.snapshot(), before)

    async def test_actual_mcp_bounds_invalid_session_and_changed_source_keep_history_readonly(self):
        self.draft(text="元の文。\r\nか\u3099")
        self.feedback()
        self.catalog["items"][0]["stem"] = "replacement instruction"
        self.synthetic.write_text(json.dumps(self.catalog, ensure_ascii=False), encoding="utf-8")
        before = self.snapshot()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            row = resumed["writing_resume"]["drafts"][0]
            self.assertEqual((row["state"], row["source_unavailable_code"]), ("source_unavailable", "source_changed"))
            self.assertIsNone(row["current_feedback_id"])
            context = await self.tool(session, "read_writing_context", {"session_id": resumed["session_id"], "draft_id": row["draft_id"]})
            self.assertEqual(context["context"]["original_version"]["text"], "元の文。\r\nか\u3099")
            self.assertEqual(context["context"]["images"], [])
            self.assertIsNone(context["context"]["source_url"])
            for params, code in ((dict(session_id=resumed["session_id"], limit=11), "invalid_field"),
                                 (dict(session_id="unknown"), "resume_required")):
                result = await self.tool(session, "read_writing_resume", params)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error"]["code"], code)
            self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

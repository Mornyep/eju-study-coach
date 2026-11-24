"""Real STDIO/HTTP transports with synthetic metadata, media and temporary vaults."""
import asyncio
from contextlib import asynccontextmanager
import http.client
import json
import os
from pathlib import Path
import sys
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import test_mcp as transport
from test_practice_focus import FocusFixtures
from eju_core.http_server import make_server


class PracticeFocusTransportTests(FocusFixtures, unittest.IsolatedAsyncioTestCase):
    tool = transport.TransportTests.tool

    def setUp(self):
        FocusFixtures.setUp(self)
        self.synthetic = self.root / "synthetic.json"
        self.synthetic.write_text(json.dumps({"catalog": self.core.catalog, "coaching": self.core.coaching}), encoding="utf-8")
        self.bootstrap = self.root / "server.py"
        self.bootstrap.write_text('''import json
from pathlib import Path
import sys
from eju_core import practice_focus, question_media
from eju_core.store import Core
from eju_core.server import build_server
base = Path(sys.argv[1])
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
if len(sys.argv) == 3 and sys.argv[2] == "replay":
    params = json.loads((base / "replay.json").read_text(encoding="utf-8"))
    print(json.dumps(Core(base / "vault").start_next_practice(**params)))
else:
    build_server(str(base / "vault")).run(transport="stdio")
''', encoding="utf-8")

    @asynccontextmanager
    async def connect(self):
        source = str(Path(__file__).resolve().parents[1] / "src")
        params = StdioServerParameters(command=sys.executable, args=[str(self.bootstrap), str(self.root)],
                                      env={**os.environ, "PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1"})
        with (self.root / "mcp-stderr.log").open("a", encoding="utf-8") as log:
            async with stdio_client(params, errlog=log) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=15) as session:
                    initialized = await session.initialize()
                    self.assertEqual(initialized.server_info.name, "eju-learning-core")
                    self.assertEqual(initialized.server_info.version, "1.0.0b11")
                    yield session

    def start_http(self):
        server, token = make_server(self.core)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def invoke(method, params):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
            try:
                conn.request("POST", "/api/call", json.dumps({"method": method, "params": params}),
                             {"Content-Type": "application/json", "Authorization": "Bearer " + token,
                              "Origin": "http://127.0.0.1:" + str(server.server_port)})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()
        return invoke

    async def test_actual_stdio_optional_focus_schema_and_strict_readonly_selected_focus(self):
        before = self.snapshot()
        async with self.connect() as session:
            tools = (await session.list_tools()).tools
            self.assertNotIn("start_next_practice", {tool.name for tool in tools})
            schema = next(tool for tool in tools if tool.name == "get_next_practice").model_dump(by_alias=True)["inputSchema"]
            self.assertIn("focus_id", schema["properties"])
            self.assertNotIn("focus_id", schema.get("required", []))
            self.assertIsNone(schema["properties"]["focus_id"]["default"])
            resumed = await self.tool(session, "resume_learning")
            query = {"session_id": resumed["session_id"], "subject": "japanese_reading"}
            ordinary = await self.tool(session, "get_next_practice", query)
            result = await self.tool(session, "get_next_practice", {**query, "focus_id": "jr.evidence"})
            self.assertTrue(result["ok"])
            self.assertIsNone(ordinary["selected_focus"])
            self.assertEqual(result["selected_focus"]["id"], "jr.evidence")
            self.assertEqual([row["item_id"] for row in result["candidates"]], ["shared", "extra"])
            self.assertNotIn("SECRET_", json.dumps(result))
            self.assertNotIn("rationale", json.dumps(result))
            denied = await self.tool(session, "get_next_practice", {**query, "focus_id": "in.graph"})
            self.assertEqual(denied["error"]["code"], "focus_unavailable")
            self.focus_path.unlink()
            denied = await self.tool(session, "get_next_practice", {**query, "focus_id": "jr.evidence"})
            self.assertEqual(denied["error"]["code"], "focus_unavailable")
            ordinary = await self.tool(session, "get_next_practice", query)
            self.assertTrue(ordinary["ok"])
            self.assertEqual(ordinary["focus_options"], [])
            self.assertEqual(len(ordinary["candidates"]), 3)
        self.assertEqual(self.snapshot(), before)

    async def test_actual_http_preview_start_and_cross_process_durable_receipt(self):
        invoke = self.start_http()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            self.sid = resumed["session_id"]
            status, preview = await asyncio.to_thread(invoke, "get_next_practice",
                                                       dict(session_id=self.sid, subject="japanese_reading", focus_id="jr.evidence"))
            self.assertEqual(status, 200)
            params = self.params(preview)
            status, receipt = await asyncio.to_thread(invoke, "start_next_practice", params)
            self.assertEqual(status, 200)
            self.assertTrue(receipt["saved"])
            self.assertFalse(receipt["learner_exposure_recorded"])
            records = await self.tool(session, "read_record_detail", {"session_id": self.sid, "record_type": "event", "event_id": "focused-next"})
            self.assertTrue(records["ok"], records)
            self.assertIn("jr.evidence", json.dumps(records))
            self.assertIn(preview["focus_version"], json.dumps(records))
        # Restart the actual MCP process after removing metadata. The task and
        # its receipt remain durable; replay is not a fresh focused selection.
        self.focus_path.unlink()
        async with self.connect() as session:
            resumed = await self.tool(session, "resume_learning")
            params["session_id"] = resumed["session_id"]
            self.assertEqual(resumed["revision"], receipt["revision"])
            records = await self.tool(session, "read_record_detail", {"session_id": params["session_id"], "record_type": "event", "event_id": "focused-next"})
            self.assertIn("jr.evidence", json.dumps(records))
            # A separate interpreter must replay the committed receipt even
            # though no focus file is present when that process starts.
            (self.root / "replay.json").write_text(json.dumps(params), encoding="utf-8")
            process = await asyncio.create_subprocess_exec(sys.executable, str(self.bootstrap), str(self.root), "replay",
                         stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                         env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                              "PYTHONDONTWRITEBYTECODE": "1"})
            output, errors = await asyncio.wait_for(process.communicate(), timeout=15)
            self.assertEqual(process.returncode, 0, errors.decode())
            replay = json.loads(output)
            self.assertTrue(replay["replayed"])
            status, replay = await asyncio.to_thread(invoke, "start_next_practice", params)
            self.assertEqual(status, 200)
            self.assertTrue(replay["replayed"])
            status, conflict = await asyncio.to_thread(invoke, "start_next_practice", {**params, "focus_id": "jr.main"})
            self.assertEqual(status, 400)
            self.assertEqual(conflict["error"]["code"], "id_conflict")
        events = self.snapshot()["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["focus_id"], "jr.evidence")
        self.assertEqual(events[0]["focus_version"], preview["focus_version"])
        self.assertFalse(any(row["type"] == "exposure" for row in events))

    async def test_actual_http_stale_preview_never_writes_or_silently_broadens(self):
        invoke = self.start_http()
        preview = self.preview("jr.evidence")
        before = self.snapshot()
        self.focus_path.write_bytes(self.focus_path.read_bytes() + b"\n")
        status, result = await asyncio.to_thread(invoke, "start_next_practice", self.params(preview))
        self.assertEqual(status, 400)
        self.assertEqual(result["error"]["code"], "focus_changed")
        self.focus_path.unlink()
        status, result = await asyncio.to_thread(invoke, "start_next_practice", self.params(preview))
        self.assertEqual(status, 400)
        self.assertEqual(result["error"]["code"], "focus_unavailable")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

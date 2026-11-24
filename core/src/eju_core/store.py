"""The only durable learning state is this vault; model text is never a receipt."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import tempfile
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from .errors import CoreError
from .exposure import shares_exposure
from .safe_files import is_link, regular_file, portable_relative, private_mode

SCHEMA = 2
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_EVENTS = 25000
MAX_JSON_BYTES = 12_000_000
ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,150}$")
ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".wav", ".mp3", ".m4a", ".ogg", ".flac", ".txt", ".json"}
LEGACY_TYPES = {"attempt", "exposure", "issue", "resolve", "retract", "worksheet", "archive", "reason_review"}


def canonical(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CoreError("invalid_json", "内容不是有限、可保存的 JSON。") from exc
    if len(raw.encode("utf-8")) > MAX_JSON_BYTES:
        raise CoreError("too_large", "单次数据超过 12 MB，未写入。")
    return raw


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def identifier(value: Any, name: str = "id") -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise CoreError("invalid_id", f"{name} 格式无效。")
    return value


def short_text(value: Any, name: str, maximum: int = 2000, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value.strip()):
        raise CoreError("invalid_field", f"{name} 必须是长度不超过 {maximum} 的文字。")
    return value


def integer(value: Any, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise CoreError("invalid_field", f"{name} 必须是 {low} 到 {high} 的整数。")
    return value


def bounded_summary(event: dict) -> dict:
    result = {}
    for k in ("id", "type", "date", "itemId", "itemVersion", "result", "hint", "seenBefore", "guessed",
              "answer", "reason", "targetId", "note", "next_action", "status", "objective"):
        if k in event:
            v = event[k]
            result[k] = v[:500] if isinstance(v, str) else v
    result["full_record_available"] = True
    return result


def evidence_summary(row: dict) -> dict:
    """Bound event references as well as the number of returned skill rows."""
    result = dict(row)
    truncated = False
    for key in ("evidence", "invalid", "independent"):
        values = row.get(key, [])
        result[key + "_total"] = len(values)
        result[key] = values[-5:]
        truncated = truncated or len(values) > 5
    progress = row.get("progress", {})
    result["progress_total"] = len(progress)
    result["progress"] = dict(list(progress.items())[:5])
    result["details_truncated"] = truncated or len(progress) > 5
    return result


def plan_summary(plan: dict | None) -> dict | None:
    if plan is None:
        return None
    result = bounded_summary(plan)
    result["reason"] = plan.get("reason", "")[:500]
    result["basis_event_ids"] = plan.get("basis_event_ids", [])[:10]
    result["item_ids"] = plan.get("item_ids", [])
    result["hypotheses"] = [v[:500] for v in plan.get("hypotheses", [])[:3]]
    result["details_truncated"] = any(len(plan.get(k, "")) > 500 for k in ("objective", "next_action", "reason")) or len(plan.get("hypotheses", [])) > 3 or len(plan.get("basis_event_ids", [])) > 10
    return result


def profile_summary(profile: dict) -> dict:
    result = {"subjects": list(dict.fromkeys(profile.get("subjects", []))), "minutes": profile.get("minutes")}
    for key in ("goal", "exam", "language"):
        value = profile.get(key)
        result[key] = value[:500] if isinstance(value, str) else None
    result["details_truncated"] = bool(set(profile) - {"subjects", "minutes", "goal", "exam", "language"}) or any(
        value is not None and (not isinstance(value, str) or len(value) > 500)
        for value in (profile.get(k) for k in ("goal", "exam", "language")))
    result["full_profile_available"] = True
    return result


def bound_resume(payload: dict) -> dict:
    """Keep the full recovery envelope below 20 KB, with explicit read-more flags.

    Only returned projections are shortened. Stored records and write identities
    remain intact; the normal detail APIs retain their original limits.
    """
    payload["resume_truncated"] = False
    payload["recent_reviews_truncated"] = False
    payload["review_queue"]["entries_truncated"] = (
        sum(payload["review_queue"][name + "_count"] for name in ("pending", "checked", "unavailable"))
        > len(payload["review_queue"]["entries"]))
    def over_budget():
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) >= 20000
    if not over_budget():
        return payload
    payload["resume_truncated"] = True
    # Keep enough text to identify the next read without repeating long answers.
    for review in payload["recent_reviews"]:
        for key in ("answer", "reason"):
            if len(review[key]) > 200:
                review[key] = review[key][:200]
                review["text_truncated"] = True
        for nested, keys in (("reflection", ("evidence", "next_action")), ("reason_review", ("note",))):
            part = review.get(nested)
            if part:
                for key in keys:
                    if len(part.get(key, "")) > 200:
                        part[key] = part[key][:200]
                        part["text_truncated" if nested == "reflection" else "note_truncated"] = True
    for key in ("profile", "last_plan", "workbench_task"):
        part = payload.get(key)
        if part:
            for field in ("goal", "exam", "language", "objective", "next_action", "reason", "answer", "instruction"):
                if isinstance(part.get(field), str) and len(part[field]) > 200:
                    part[field] = part[field][:200]
                    part["details_truncated"] = True
    # Discard only redundant summary rows, oldest/least-recent rows first.
    # Every affected collection exposes truncation; queue counts stay complete.
    collections = [(payload, "recent_events", "has_more_history", True),
                   (payload, "recent_reviews", "recent_reviews_truncated", False),
                   (payload, "evidence", "evidence_truncated", False),
                   (payload, "unresolved_issues", "issues_truncated", False),
                   (payload["review_queue"], "entries", "entries_truncated", False),
                   (payload["writing_resume"], "drafts", "has_more", False),
                   (payload["recall"], "open_trials", "open_trials_truncated", False),
                   (payload, "missing_artifacts", "missing_artifacts_truncated", False)]
    for container, key, flag, oldest_first in collections:
        while container[key] and over_budget():
            container[key].pop(0 if oldest_first else -1)
            container[flag] = True
    return payload


from .workbench import WorkbenchMixin
from .documents import DocumentMixin
from .recall_store import RecallMixin
from .question_media import QuestionMediaMixin
from .practice_loop import PracticeLoopMixin


from .review_loop import ReviewLoopMixin
from .writing_loop import WritingLoopMixin


class Core(WorkbenchMixin, DocumentMixin, RecallMixin, QuestionMediaMixin, PracticeLoopMixin, ReviewLoopMixin, WritingLoopMixin):
    def __init__(self, vault: str | Path):
        self.root = Path(vault).expanduser().absolute()
        if is_link(self.root):
            raise CoreError("unsafe_path", "资料库入口不能是符号链接。")
        self.root = self.root.resolve()
        self.db_path = self.root / "learning.sqlite"
        if not self.db_path.is_file() or is_link(self.db_path):
            raise CoreError("vault_missing", "学习库不存在。请先用 init 创建或 restore 恢复；未自动建立空进度。")
        regular_file(self.db_path)
        catalog_path = files("eju_core").joinpath("data/catalog.json")
        self.catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.items = {item["id"]: item for item in self.catalog["items"]}
        self.coaching = json.loads(files("eju_core").joinpath("data/study_guidance.json").read_text(encoding="utf-8"))["items"]
        with self.connection() as db:
            try:
                version = self.meta(db, "schema_version")
                if version != SCHEMA:
                    raise CoreError("schema_unsupported", "资料库版本不受支持，原文件未修改。")
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise CoreError("vault_corrupt", "数据库完整性检查失败，请从已核对的备份恢复。")
            except sqlite3.Error as exc:
                raise CoreError("vault_corrupt", "数据库结构不可读，原文件未覆盖。") from exc

    @classmethod
    def initialize(cls, vault: str | Path) -> dict:
        root = Path(vault).expanduser().absolute()
        if is_link(root) or root.exists():
            raise CoreError("destination_exists", "初始化目标已经存在，请指定一个新的资料库目录。")
        root.mkdir(parents=True, mode=0o700)
        try:
            for directory in ("originals", "inbox", "derived"):
                (root / directory).mkdir(mode=0o700)
            db = sqlite3.connect(root / "learning.sqlite")
            try:
                db.executescript("""
                    PRAGMA journal_mode=WAL;
                    PRAGMA synchronous=FULL;
                    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                                        body TEXT NOT NULL, origin TEXT NOT NULL, revision INTEGER NOT NULL);
                    CREATE TABLE requests(id TEXT PRIMARY KEY, digest TEXT NOT NULL, receipt TEXT NOT NULL);
                    CREATE TABLE sessions(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
                    CREATE TABLE artifacts(id TEXT PRIMARY KEY, relative_path TEXT NOT NULL UNIQUE,
                                           name TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
                                           media_type TEXT NOT NULL, created_at TEXT NOT NULL);
                    CREATE TABLE imports(source_id TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL,
                                         raw TEXT NOT NULL, state TEXT NOT NULL, revision INTEGER NOT NULL);
                """)
                values = {"schema_version": SCHEMA, "vault_id": str(uuid.uuid4()), "revision": 0,
                          "created_at": stamp(), "profile": {"subjects": [], "minutes": None, "goal": None},
                          "legacy_draft": None, "legacy_source_id": None}
                db.executemany("INSERT INTO meta VALUES (?,?)", [(k, canonical(v)) for k, v in values.items()])
                db.commit()
            finally:
                db.close()
            os.chmod(root / "learning.sqlite", 0o600)
            return {"ok": True, "vault_id": values["vault_id"], "revision": 0, "path": str(root),
                    "profile_status": "unknown", "message": "空资料库已创建；没有推断你的选科或成绩。"}
        except BaseException:
            # Keep a partial initialization visible; never delete possibly useful data.
            raise

    @contextmanager
    def connection(self, write: bool = False):
        db = None
        try:
            try:
                regular_file(self.db_path)
            except CoreError as exc:
                if exc.code != "material_missing":raise
                raise CoreError("storage_error", "学习库已经缺失，未创建空库或确认保存。", retry_safe=True) from exc
            for suffix in ("-wal", "-shm", "-journal"):
                sidecar = Path(str(self.db_path) + suffix)
                if sidecar.exists() or is_link(sidecar):
                    regular_file(sidecar)
            # mode=rw refuses to silently replace a missing database with an empty one.
            db = sqlite3.connect(self.db_path.as_uri() + "?mode=rw", uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except sqlite3.Error as exc:
            if db is not None:
                db.rollback()
            raise CoreError("storage_error", "学习库读取或写入失败，不能确认保存。请保留本轮输入并检查资料库。",
                            retry_safe=True) from exc
        except BaseException:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def meta(db, key: str):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if row is None:
            raise CoreError("vault_corrupt", "学习库缺少必要元数据。")
        return json.loads(row[0])

    @staticmethod
    def set_meta(db, key: str, value: Any):
        db.execute("UPDATE meta SET value=? WHERE key=?", (canonical(value), key))

    @staticmethod
    def events(db) -> list[dict]:
        return [json.loads(row[0]) for row in db.execute("SELECT body FROM events ORDER BY seq")]

    def check_session(self, db, session_id: str):
        identifier(session_id, "session_id")
        row = db.execute("SELECT created_at FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not row or datetime.fromisoformat(row[0]) < datetime.now(timezone.utc) - timedelta(hours=24):
            raise CoreError("resume_required", "请先恢复本资料库的学习状态，再读取或写入。")

    def begin_request(self, db, request_id: str, operation: str, payload: dict, expected_revision: int,
                      session_id: str | None) -> tuple[str, dict | None]:
        identifier(request_id, "request_id")
        integer(expected_revision, "expected_revision", 0, 2**53 - 1)
        if session_id is not None:
            self.check_session(db, session_id)
        digest = sha(canonical({"operation": operation, "payload": payload}).encode())
        row = db.execute("SELECT digest,receipt FROM requests WHERE id=?", (request_id,)).fetchone()
        current = self.meta(db, "revision")
        if row:
            if row["digest"] != digest:
                raise CoreError("id_conflict", "同一请求 ID 对应不同内容，未覆盖原记录。")
            receipt = json.loads(row["receipt"])
            return digest, {**receipt, "replayed": True, "current_revision": current}
        if current != expected_revision:
            raise CoreError("revision_conflict", "另一会话已经更新记录，请先恢复最新状态。",
                            current_revision=current, supplied_revision=expected_revision)
        return digest, None

    def finish_request(self, db, request_id: str, digest: str, **extra) -> dict:
        revision = self.meta(db, "revision") + 1
        self.set_meta(db, "revision", revision)
        receipt = {"ok": True, "saved": True, "request_id": request_id, "revision": revision,
                   "vault_id": self.meta(db, "vault_id"), "replayed": False, **extra}
        db.execute("INSERT INTO requests VALUES (?,?,?)", (request_id, digest, canonical(receipt)))
        return receipt

    def insert_event(self, db, event: dict, origin: str):
        if db.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= MAX_EVENTS:
            raise CoreError("capacity_reached", "当前版本达到事件容量，请保留库并升级，不会自动删除旧记录。")
        if db.execute("SELECT 1 FROM events WHERE id=?", (event["id"],)).fetchone():
            raise CoreError("id_conflict", "事件 ID 已存在，请先读取原事件。")
        db.execute("INSERT INTO events(id,body,origin,revision) VALUES (?,?,?,?)",
                   (event["id"], canonical(event), origin, self.meta(db, "revision") + 1))

    def resume_learning(self) -> dict:
        from .evidence import evidence_map
        with self.connection(write=True) as db:
            sid = str(uuid.uuid4())
            db.execute("INSERT INTO sessions VALUES (?,?)", (sid, stamp()))
            db.execute("DELETE FROM sessions WHERE created_at<?", ((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))
            rows = self._writing_rows(db)
            events = [event for _, event in rows]
            profile = self.meta(db, "profile")
            plans = [e for e in events if e["type"] == "plan"]
            summary = evidence_map(events, self.catalog["items"], self.catalog["nodes"])
            active = [v for v in summary.values() if v.get("evidence") or v.get("invalid")]
            unresolved = [e for e in events if e["type"] == "issue"
                          and not any(r["type"] == "resolve" and r.get("issueId") == e["id"] for r in events)]
            missing = []
            for row in db.execute("SELECT id,relative_path FROM artifacts"):
                try:
                    path = self.registered_path(row["relative_path"])
                    if not path.is_file():
                        missing.append(row["id"])
                except CoreError:
                    missing.append(row["id"])
            legacy = self.meta(db, "legacy_draft")
            payload = {"ok": True, "vault_id": self.meta(db, "vault_id"), "revision": self.meta(db, "revision"),
                    "session_id": sid, "local_date": today(), "profile": profile_summary(profile),
                    "profile_needs_input": not profile["subjects"], "last_plan": plan_summary(plans[-1] if plans else None),
                    "workbench_task": self.task_summary(events), "recall": self.recall_summary(events),
                    "recent_events": [bounded_summary(e) for e in events[-6:]],
                    "recent_reviews": self.study_review_summary(events),
                    "review_queue": self.review_queue_summary(events),
                    "writing_resume": self._writing_resume_summary(rows),
                    "event_count": len(events), "has_more_history": len(events) > 6,
                    "evidence": [evidence_summary(row) for row in active[:20]], "evidence_truncated": len(active) > 20,
                    "unresolved_issues": [bounded_summary(e) for e in unresolved[:10]],
                    "issues_truncated": len(unresolved) > 10, "missing_artifacts": missing[:10],
                    "missing_artifacts_count": len(missing), "missing_artifacts_truncated": len(missing) > 10,
                    "legacy_draft_available": legacy is not None,
                    "legacy_draft_hint": self.draft_hint(legacy),
                    "legacy_source_count": db.execute("SELECT COUNT(*) FROM imports").fetchone()[0],
                    "limits": ["未出现的技能为未知；不输出 EJU 预测分数。",
                               "恢复包是有界视图，完整事件可分页读取。",
                               "模型读取答案不等于学习者看到答案；展示记录需另行保存。",
                               "本地存储不意味着 Codex 模型推理在本地。"]}
            return bound_resume(payload)

    @staticmethod
    def draft_hint(draft):
        if not isinstance(draft, dict):
            return None
        current = draft.get("currentItem")
        practice = draft.get("practices", {}).get(current, {})
        return {"current_item": current, "has_answer": bool(practice.get("answer")),
                "has_worksheet": bool(draft.get("worksheet")), "full_draft_available": True}

    def read_learning_records(self, session_id: str, after: int = 0, limit: int = 10,
                              item_id: str | None = None, event_id: str | None = None,
                              include_legacy_draft: bool = False) -> dict:
        integer(after, "after", 0, MAX_EVENTS)
        integer(limit, "limit", 1, 50)
        with self.connection() as db:
            self.check_session(db, session_id)
            rows = []
            size = 0
            for row in db.execute("SELECT seq,body,origin FROM events WHERE seq>? ORDER BY seq", (after,)):
                e = json.loads(row["body"])
                if item_id and e.get("itemId") != item_id:
                    continue
                if event_id and e["id"] != event_id:
                    continue
                body_size = len(row["body"])
                truncated = body_size > 12000
                display = bounded_summary(e) if truncated else e
                display_size = len(canonical(display))
                if rows and (size + display_size > 24000 or len(rows) >= limit):
                    break
                rows.append({"cursor": row["seq"], "event": display, "origin": row["origin"],
                             "record_truncated": truncated, "record_characters": body_size})
                size += display_size
            cursor = rows[-1]["cursor"] if rows else after
            more = False
            for row in db.execute("SELECT body FROM events WHERE seq>?", (cursor,)):
                e = json.loads(row["body"])
                if (not item_id or e.get("itemId") == item_id) and (not event_id or e["id"] == event_id):
                    more = True
                    break
            result = {"ok": True, "revision": self.meta(db, "revision"), "records": rows,
                      "next_cursor": cursor, "has_more": more}
            if include_legacy_draft:
                draft = self.meta(db, "legacy_draft")
                large = len(canonical(draft)) > 12000
                result["legacy_draft"] = None if large else draft
                result["legacy_draft_truncated"] = large
                result["legacy_draft_hint"] = self.draft_hint(draft)
                result["legacy_sources"] = [dict(r) for r in db.execute(
                    "SELECT source_sha256,revision AS imported_at_revision FROM imports ORDER BY revision DESC LIMIT 10")]
                result["legacy_sources_truncated"] = db.execute("SELECT COUNT(*) FROM imports").fetchone()[0] > 10
                result["legacy_sources_instruction"] = "其他导入快照用read_record_detail legacy_sources索引及legacy_source按source_sha256读取；当前草稿不会被后续导入自动覆盖。"
            return result

    def read_record_detail(self, session_id: str, record_type: str, event_id: str | None = None,
                           offset: int = 0, limit: int = 6000, source_sha256: str | None = None) -> dict:
        integer(offset, "offset", 0, MAX_JSON_BYTES)
        integer(limit, "limit", 1, 12000)
        with self.connection() as db:
            self.check_session(db, session_id)
            if record_type == "legacy_draft":
                raw = canonical(self.meta(db, "legacy_draft"))
            elif record_type == "legacy_sources":
                raw = canonical([dict(r) for r in db.execute("SELECT source_sha256,revision AS imported_at_revision FROM imports ORDER BY revision DESC")])
            elif record_type == "legacy_source":
                if not isinstance(source_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", source_sha256):
                    raise CoreError("invalid_source", "需要已返回的 source_sha256。")
                row = db.execute("SELECT raw FROM imports WHERE source_sha256=?", (source_sha256,)).fetchone()
                if row is None:
                    raise CoreError("record_missing", "没有这份旧来源。")
                raw = row[0]
            elif record_type == "profile":
                raw = canonical(self.meta(db, "profile"))
            elif record_type == "event":
                row = db.execute("SELECT body FROM events WHERE id=?", (event_id,)).fetchone()
                if row is None:
                    raise CoreError("record_missing", "没有该事件记录。")
                raw = row[0]
            else:
                raise CoreError("invalid_record_type", "record_type 仅支持 event、profile、legacy_draft、legacy_sources 或 legacy_source。")
            return {"ok": True, "record_type": record_type, "event_id": event_id, "source_sha256": source_sha256,
                    "json_fragment": raw[offset:offset+limit], "offset": offset,
                    "next_offset": min(len(raw), offset+limit), "has_more": offset+limit < len(raw),
                    "total_characters": len(raw), "sha256": sha(raw.encode()),
                    "instruction": "这是原始 JSON 的有界片段；需要剩余部分时沿 next_offset 读取，不把截断内容当完整记录。"}

    def get_task_candidates(self, session_id: str, minutes: int | None = None, subject: str | None = None,
                            purpose: str = "study") -> dict:
        from .evidence import evidence_map, item_status
        from .selection import integrated_eligible, practice_scope
        from .question_media import ready
        if purpose not in {"study", "supplement", "prerequisite"}:
            raise CoreError("invalid_purpose", "purpose 只支持 study、supplement 或 prerequisite。")
        if minutes is not None:
            integer(minutes, "minutes", 1, 180)
        with self.connection() as db:
            self.check_session(db, session_id)
            profile, events = self.meta(db, "profile"), self.events(db)
            subjects = [subject] if subject else profile["subjects"]
            if subject and subject not in self.catalog["subjects"]:
                raise CoreError("invalid_subject", "选科无效。")
            if not subjects:
                return {"ok": True, "candidates": [], "needs_subject": True,
                        "message": "选科尚未确认；只问当前安排需要的信息，或先处理用户给出的题。"}
            available = minutes or profile["minutes"] or 10
            emap = evidence_map(events, self.catalog["items"], self.catalog["nodes"])
            seen = {e.get("itemId") for e in events if e["type"] in ("attempt", "exposure")}
            candidates = []
            for item in self.items.values():
                wanted_kind = "official" if purpose == "study" else "original"
                wanted_status = "reviewed_official" if purpose == "study" else "reviewed_original"
                if item["kind"] != wanted_kind or not set(subjects) & set(item["subjects"]):
                    continue
                group_seen = any(self.items.get(i, {}).get("groupId", i) == item.get("groupId", item["id"]) for i in seen)
                passage_seen = any(shares_exposure(item, self.items.get(i, {})) for i in seen)
                if item["id"] in seen or group_seen or passage_seen or item_status(events, item) != wanted_status:
                    continue
                if not ready(item):
                    continue
                if purpose != "study" and "integrated" in item["subjects"] and not integrated_eligible(item, purpose):
                    continue
                if purpose == "prerequisite" and "integrated" not in item["subjects"] and item.get("role") not in ("foundation", "contrast"):
                    continue
                status = [emap[n] for n in item["nodes"] if n in emap]
                priority, reason = 1, "当前目标尚无独立作答证据，可先做小检查。"
                if any(r.get("status") == "needs_repair" for r in status):
                    priority, reason = 3, "有关联的待修复表现，先核实卡点。"
                elif any(r.get("due") and r["due"] <= today() for r in status):
                    priority, reason = 2, "有关联的到期复测，需独立作答。"
                candidates.append((priority, item.get("order", 10000), item, reason))
            candidates.sort(key=lambda x: (-x[0], x[1], x[2]["id"]))
            chosen, groups, chosen_items = [], set(), []
            for _, _, item, reason in candidates:
                group = item.get("groupId", item["id"])
                if group in groups or any(shares_exposure(item, other) for other in chosen_items):
                    continue
                groups.add(group)
                chosen_items.append(item)
                chosen.append({"item_id": item["id"], "item_version": item["version"],
                               "display_title": item.get("displayTitle", "原创练习"), "subjects": item["subjects"],
                               "nodes": item["nodes"], "role": item.get("role"), "reason": reason,
                               "practice_scope": practice_scope(item), "kind": item["kind"],
                               "images_required": bool(item.get("visualRequired") or item["kind"] == "official"),
                               "image_count": len((item.get("delivery") or {}).get("assets", [])),
                               "time_note": "任务容量启发式，非实测预计耗时。"})
                if len(chosen) >= 3:
                    break
            return {"ok": True, "revision": self.meta(db, "revision"), "minutes": available,
                    "candidates": chosen, "no_candidate": not chosen, "purpose": purpose,
                    "message": "日常study以材料完整且已核对的EJU原题为主；supplement为辅助原创，prerequisite为基础补练。无可用原题时说明材料或审核缺口，不静默切换原创。索引和图片读取都不代表已经审题或已展示。"}

    def read_material(self, session_id: str, resource_id: str, section: str = "stem",
                      offset: int = 0, limit: int = 6000) -> dict:
        integer(offset, "offset", 0, MAX_FILE_BYTES)
        integer(limit, "limit", 1, 12000)
        with self.connection() as db:
            self.check_session(db, session_id)
            if resource_id in self.items:
                item = self.items[resource_id]
                if section not in ("stem", "coach"):
                    raise CoreError("invalid_section", "题目 section 仅支持 stem 或 coach。")
                from .evidence import item_status
                from .selection import practice_scope
                current_status = item_status(self.events(db), item)
                base = {"ok": True, "resource_id": resource_id, "item_version": item["version"],
                        "kind": item["kind"], "status": current_status, "catalog_status": item["status"], "usable_for_assessment": current_status in {"reviewed_original", "reviewed_official"}, "pages": item.get("pages", []),
                        "source": item["sourcePath"], "untrusted_source_material": True,
                        "learner_exposure_recorded": False, "practice_scope": practice_scope(item)}
                if section == "stem":
                    from .question_media import assets
                    try:
                        images = assets(item)
                    except CoreError as exc:
                        return {**base, "available": False, "usable_for_assessment": False,
                                "images_required": True, "images": [], "error": exc.result()["error"],
                                "message": "完整图片暂不可用；先取得并核对原页，不能从索引重建题目。"}
                    base.update(images_required=bool(item.get("visualRequired") or item["kind"] == "official"),
                                images=images, source_url=(item.get("delivery") or {}).get("source_url"))
                    stem = item.get("stem")
                    if not stem:
                        return {**base, "available": False, "message": "只有索引，尚无完整题面；请提供对应原件。"}
                    return {**base, "available": True, "display_title": item.get("displayTitle", "题目"),
                            "stem": stem, "nodes": item["nodes"]}
                return {**base, "available": bool(item.get("solution")), "audience": "coach_only",
                        "answer": item.get("answer"), "solution": item.get("solution"),
                        "steps": item.get("steps", []), "hint": item.get("hint", ""),
                        "grading": item.get("grading"), "prerequisites": item.get("prerequisites", []),
                        "exam_alignment_review": item.get("examAlignment"),
                        "coaching": self.study_guidance(item),
                        "instruction": "按需批改；读取不等于展示，展示提示或答案须单独记录。"}
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (resource_id,)).fetchone()
            if not row:
                raise CoreError("material_missing", "没有这个已登记的资料 ID。")
            path = self.verify_artifact(row)
            result = {"ok": True, "resource_id": resource_id, "name": row["name"], "sha256": row["sha256"],
                      "media_type": row["media_type"], "size": row["size"], "untrusted_source_material": True}
            if row["media_type"].startswith("text/") or path.suffix == ".json":
                try:
                    text = path.read_text(encoding="utf-8")
                except UnicodeError as exc:
                    raise CoreError("encoding_unsupported", "该文件不是 UTF-8 文本，原件仍保留。") from exc
                return {**result, "text": text[offset:offset+limit], "next_offset": min(len(text), offset+limit),
                        "has_more": offset+limit < len(text)}
            return {**result, "path": str(path), "extracted": False,
                    "message": "原件已校验。由宿主可用的看图/PDF/音频能力读取；本版未执行 OCR 或转录。"}

    def validate_native(self, db, payload: dict, session_id: str) -> dict:
        from .legacy import validate_event
        if not isinstance(payload, dict):
            raise CoreError("invalid_event", "事件必须是对象。")
        if any(k in payload for k in ("createdAt", "date", "sessionId", "origin", "reportedHint")):
            raise CoreError("reserved_field", "原生事件的时间、会话和派生字段由服务写入；历史记录请走迁移。")
        event = json.loads(canonical(payload))
        identifier(event.get("id"), "event.id")
        kind = event.get("type")
        if kind not in LEGACY_TYPES:
            raise CoreError("invalid_event", "不支持此学习事件类型。档案和计划请使用专用工具。")
        event.update(createdAt=stamp(), date=today(), sessionId=session_id)
        if kind in ("attempt", "exposure", "issue", "resolve"):
            item = self.items.get(event.get("itemId"))
            if not item:
                raise CoreError("item_missing", "题目不在当前登记内容中；先登记资料或保留原文，不编造题号。")
        if kind == "attempt":
            if event.get("itemVersion") != item["version"]:
                raise CoreError("item_version_conflict", "题目版本已经变化，未记录为当前版本作答。")
            if event.get("hint") not in ("H0", "H1", "H2", "H3", "H4"):
                raise CoreError("invalid_hint", "提示等级须为 H0 到 H4。")
            event["nodes"] = list(item["nodes"])
            supplied = event["hint"]
            prior = [json.loads(r[0]) for r in db.execute("SELECT body FROM events")]
            if any(e.get("type") in ("attempt", "exposure") and e.get("itemId") != item["id"]
                   and shares_exposure(item, self.items.get(e.get("itemId"), {})) for e in prior):
                event["seenBefore"] = True
            floor = max([int(e.get("hint", "H1")[1:]) for e in prior
                         if e["type"] == "exposure" and e.get("level") in ("hint", "answer")
                         and e.get("sessionId") == session_id and e.get("itemId") == item["id"]] or [0])
            event["hint"] = "H" + str(max(int(supplied[1:]), floor))
            event["reportedHint"] = supplied
            if event.get("seconds") is not None and event.get("timingSource") not in ("learner_report", "workbench"):
                raise CoreError("timing_source_required", "有解题用时时须注明本人报告或工作台实测，不能用消息间隔猜测。")
            if event.get("seconds") is None:
                event["timingSource"] = "unmeasured"
            if event.get("grading") == "automatic":
                grading = item.get("grading") or {}
                if grading.get("mode") not in ("number", "choice"):
                    raise CoreError("automatic_grading_unavailable", "本题没有确定性对照器，请保留未评分或注明外部核对。")
                event["reportedResult"] = event.get("result")
                answer = short_text(event.get("answer"), "answer", 20000, empty=True)
                normalized = unicodedata.normalize("NFKC", answer).strip().replace("−", "-")
                if grading["mode"] == "choice":
                    event["result"] = "correct" if normalized == str(grading["value"]) else "incorrect"
                else:
                    compact = re.sub(r"\s", "", normalized)
                    number_pattern = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
                    actual = None
                    if re.fullmatch(number_pattern, compact):
                        actual = float(compact)
                    elif re.fullmatch(number_pattern + "/" + number_pattern, compact):
                        numerator, denominator = map(float, compact.split("/"))
                        if denominator:
                            actual = numerator / denominator
                    expected = float(grading["value"])
                    tolerance = min(1e-4, max(1e-10, grading.get("tolerance", max(1e-9, abs(expected)*1e-8))))
                    event["result"] = ("ungraded" if actual is None else
                                       "correct" if abs(actual-expected) <= tolerance else "incorrect")
        if kind == "exposure":
            if event.get("level") not in ("stem", "hint", "answer"):
                raise CoreError("invalid_exposure", "展示记录需要 stem、hint 或 answer 类型。")
            floor = {"stem": "H0", "hint": "H1", "answer": "H4"}[event["level"]]
            h = event.get("hint", floor)
            if h not in ("H0", "H1", "H2", "H3", "H4") or int(h[1]) < int(floor[1]):
                raise CoreError("invalid_hint", "展示类型与提示等级不符。")
            event["hint"] = h
            if event.get("source") not in ("codex_report", "learner_report", "workbench"):
                raise CoreError("invalid_source", "展示记录必须说明来源。")
        if kind in ("retract", "reason_review", "resolve"):
            target_key = "issueId" if kind == "resolve" else "targetId"
            row = db.execute("SELECT body FROM events WHERE id=?", (event.get(target_key),)).fetchone()
            target = json.loads(row[0]) if row else None
            expected_type = "issue" if kind == "resolve" else "attempt"
            if not target or target["type"] != expected_type:
                raise CoreError("reference_missing", "被核对、更正或撤销的事件不存在。")
            if kind == "resolve" and target["itemId"] != event["itemId"]:
                raise CoreError("reference_mismatch", "纠正与原问题的题号不一致。")
        candidate = dict(event)
        if candidate.get("hint") in ("H3", "H4"):
            candidate["hint"] = "H2"  # legacy shape checks; native evidence retains the true level.
        try:
            validate_event(candidate)
        except ValueError as exc:
            raise CoreError("invalid_event", str(exc)) from exc
        if len(canonical(event).encode()) > 100000:
            raise CoreError("too_large", "单个事件过大。")
        return event

    def record_learning_event(self, session_id: str, expected_revision: int, event: dict) -> dict:
        if not isinstance(event, dict):
            raise CoreError("invalid_event", "事件必须是对象。")
        request_id = identifier(event.get("id"), "event.id")
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "event", event, expected_revision, session_id)
            if replay:
                return replay
            saved = self.validate_native(db, event, session_id)
            self.insert_event(db, saved, "native_codex")
            return self.finish_request(db, request_id, digest, event_id=request_id,
                                       applied_hint=saved.get("hint"), date=saved["date"])

    def set_profile(self, session_id: str, expected_revision: int, request_id: str, profile: dict) -> dict:
        if not isinstance(profile, dict) or set(profile) - {"subjects", "minutes", "goal", "exam", "language"}:
            raise CoreError("invalid_profile", "档案字段不支持。")
        subjects = profile.get("subjects")
        if not isinstance(subjects, list) or any(
                not isinstance(x, str) or x not in self.catalog["subjects"] for x in subjects) or len(subjects) != len(set(subjects)):
            raise CoreError("invalid_profile", "请选择已确认的科目；不确定时可保留空列表。")
        if profile.get("minutes") is not None:
            integer(profile["minutes"], "minutes", 1, 180)
        for field in ("goal", "exam", "language"):
            if profile.get(field) is not None:
                short_text(profile[field], field, 1000)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "profile", profile, expected_revision, session_id)
            if replay:
                return replay
            saved = {**self.meta(db, "profile"), **profile}
            event = {"id": request_id, "type": "profile", "createdAt": stamp(), "date": today(),
                     "sessionId": session_id, "profile_patch": profile,
                     "operation": "merge_preserving_unknown"}
            self.insert_event(db, event, "native_codex")
            self.set_meta(db, "profile", saved)
            return self.finish_request(db, request_id, digest, event_id=request_id)

    def save_learning_plan(self, session_id: str, expected_revision: int, request_id: str, plan: dict) -> dict:
        if not isinstance(plan, dict) or set(plan) - {"objective", "next_action", "reason", "status",
                                                       "basis_event_ids", "item_ids", "hypotheses"}:
            raise CoreError("invalid_plan", "计划字段不支持。")
        for field in ("objective", "next_action", "reason"):
            short_text(plan.get(field), field, 2000)
        if plan.get("status") not in ("active", "paused", "completed"):
            raise CoreError("invalid_plan", "计划状态无效。")
        basis = plan.get("basis_event_ids", [])
        item_ids = plan.get("item_ids", [])
        if not isinstance(basis, list) or len(basis) > 30 or not isinstance(item_ids, list) or len(item_ids) > 10:
            raise CoreError("invalid_plan", "依据或题目列表无效。")
        hypotheses = plan.get("hypotheses", [])
        if not isinstance(hypotheses, list) or len(hypotheses) > 10:
            raise CoreError("invalid_plan", "假设列表无效。")
        for hypothesis in hypotheses:
            short_text(hypothesis, "hypothesis", 1000)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "plan", plan, expected_revision, session_id)
            if replay:
                return replay
            for event_id in basis:
                identifier(event_id, "basis_event_id")
                if not db.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone():
                    raise CoreError("reference_missing", "计划引用的依据事件不存在。")
            if any(item_id not in self.items for item_id in item_ids):
                raise CoreError("item_missing", "计划引用了未登记的题目。")
            event = {"id": request_id, "type": "plan", "createdAt": stamp(), "date": today(),
                     "sessionId": session_id, **plan}
            self.insert_event(db, event, "native_codex")
            return self.finish_request(db, request_id, digest, event_id=request_id, attempts_added=0)

    def import_v5(self, raw: str, expected_revision: int, request_id: str, apply: bool = False) -> dict:
        from .legacy import parse_backup
        from .evidence import evidence_map
        if not isinstance(raw, str) or len(raw.encode()) > MAX_JSON_BYTES:
            raise CoreError("too_large", "备份过大或格式无效。")
        try:
            incoming = parse_backup(raw)
        except ValueError as exc:
            raise CoreError("legacy_invalid", str(exc)) from exc
        source_sha = sha(raw.encode())
        payload = {"source_sha256": source_sha}
        with self.connection(write=apply) as db:
            if apply:
                digest, replay = self.begin_request(db, request_id, "import_v5", payload, expected_revision, None)
                if replay:
                    return replay
            current_events = {e["id"]: e for e in self.events(db)}
            existing_source = self.meta(db, "legacy_source_id")
            if existing_source is not None and existing_source != incoming["id"]:
                raise CoreError("source_identity_conflict", "该库已经关联另一份旧记录身份，不能按相同选科合并两个人的记录。")
            for e in incoming["events"]:
                if e["id"] in current_events and canonical(current_events[e["id"]]) != canonical(e):
                    raise CoreError("event_conflict", "备份含同 ID 不同内容的事件，全部拒绝，没有覆盖。", event_id=e["id"])
            to_add = [e for e in incoming["events"] if e["id"] not in current_events]
            if len(current_events) + len(to_add) > MAX_EVENTS:
                raise CoreError("capacity_reached", "合并后的事件数量超过当前版本容量。")
            vault_empty = not current_events and self.meta(db, "revision") == 0
            if not vault_empty:
                # Incompatible learner profiles are never silently merged.
                current_profile = self.meta(db, "profile")
                for key in ("subjects", "minutes"):
                    if current_profile.get(key) != incoming["profile"].get(key):
                        raise CoreError("profile_conflict", "旧备份的选科或容量与当前档案不同；请在独立资料库预览迁移。")
            preview = {"ok": True, "applied": False, "source_id": incoming["id"],
                       "source_sha256": source_sha, "source_revision": incoming["revision"],
                       "events_added": len(to_add), "events_total": len(current_events) + len(to_add),
                       "draft_retained": True, "empty_vault_restore": vault_empty,
                       "missing_images": "v5 备份原本不含照片，不能据此恢复图片。",
                       "legacy_evidence": evidence_map(incoming["events"], self.catalog["items"], self.catalog["nodes"])}
            if not apply:
                return preview
            existing = db.execute("SELECT source_sha256 FROM imports WHERE source_id=?",
                                  (incoming["id"] + ":" + source_sha,)).fetchone()
            if existing:
                return {"ok": True, "saved": True, "replayed": True, "revision": self.meta(db, "revision"),
                        "events_added": 0, "source_sha256": source_sha}
            for e in to_add:
                self.insert_event(db, e, "v5:" + source_sha)
            if vault_empty:
                self.set_meta(db, "profile", incoming["profile"])
                self.set_meta(db, "legacy_draft", incoming["draft"])
            self.set_meta(db, "legacy_source_id", incoming["id"])
            db.execute("INSERT INTO imports VALUES (?,?,?,?,?)",
                       (incoming["id"] + ":" + source_sha, source_sha, raw, canonical(incoming),
                        self.meta(db, "revision")+1))
            return self.finish_request(db, request_id, digest, events_added=len(to_add),
                                       source_sha256=source_sha, source_revision=incoming["revision"],
                                       legacy_draft_active=vault_empty)

    def registered_path(self, relative: str) -> Path:
        part = portable_relative(relative)
        if part.is_absolute() or ".." in part.parts or not part.parts or part.parts[0] != "originals":
            raise CoreError("unsafe_path", "资料位置不合法。")
        path = self.root / part
        for component in [path, *list(path.parents)[:len(part.parts)]]:
            if is_link(component):
                raise CoreError("unsafe_path", "资料路径含符号链接，拒绝读取。")
        if not path.resolve().is_relative_to(self.root):
            raise CoreError("unsafe_path", "资料路径超出学习库。")
        return path

    def verify_artifact(self, row) -> Path:
        path = self.registered_path(row["relative_path"])
        if not path.is_file():
            raise CoreError("material_missing", "登记的原件已缺失，未返回伪造内容。", resource_id=row["id"])
        regular_file(path, MAX_FILE_BYTES)
        if path.stat().st_size != row["size"] or sha(path.read_bytes()) != row["sha256"]:
            raise CoreError("material_corrupt", "原件与登记哈希不一致，请核对备份。", resource_id=row["id"])
        return path

    def import_material(self, session_id: str, expected_revision: int, request_id: str, inbox_name: str) -> dict:
        if (not isinstance(inbox_name, str) or Path(inbox_name).name != inbox_name
                or inbox_name.startswith(".") or len(inbox_name) > 180):
            raise CoreError("unsafe_path", "只接受资料库 inbox 内的单个文件名。")
        portable_relative(inbox_name)
        inbox = self.root / "inbox"
        source = inbox / inbox_name
        if is_link(inbox) or is_link(source) or not source.is_file():
            raise CoreError("material_missing", "inbox 中没有可读取的普通文件。")
        suffix = source.suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise CoreError("unsupported_file", "本版不导入此类文件。")
        regular_file(source, MAX_FILE_BYTES)
        if source.stat().st_size > MAX_FILE_BYTES:
            raise CoreError("too_large", "单个原件超过 100 MB。")
        data = source.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            raise CoreError("too_large", "读取过程中原件超过容量。")
        digest_file = sha(data)
        resource_id = "asset:" + digest_file
        relative = "originals/" + digest_file + suffix
        target = self.registered_path(relative)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "import_material",
                                                {"name": inbox_name, "sha256": digest_file}, expected_revision, session_id)
            if replay:
                return replay
            existing = db.execute("SELECT * FROM artifacts WHERE id=?", (resource_id,)).fetchone()
            if existing:
                self.verify_artifact(existing)
            else:
                if db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] >= 10000:
                    raise CoreError("capacity_reached", "当前版本最多登记10000份原件，请保留资料并升级。")
                if target.exists() and sha(target.read_bytes()) != digest_file:
                    raise CoreError("material_conflict", "归档目标已有不同内容，未覆盖。")
                temp_name = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.root/"originals", prefix=".pending-", delete=False) as f:
                        temp_name = f.name
                        os.chmod(temp_name, 0o600)
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    os.replace(temp_name, target)
                finally:
                    if temp_name and Path(temp_name).exists():
                        Path(temp_name).unlink()
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
                           (resource_id, relative, inbox_name, len(data), digest_file,
                            mimetypes.guess_type(inbox_name)[0] or "application/octet-stream", stamp()))
            event = {"id": request_id, "type": "material", "createdAt": stamp(), "date": today(),
                     "sessionId": session_id, "resource_id": resource_id, "name": inbox_name}
            self.insert_event(db, event, "native_codex")
            return self.finish_request(db, request_id, digest, resource_id=resource_id, sha256=digest_file,
                                       original_retained=True, extracted=False)

    def prepare_feedback(self, session_id: str, item_id: str, category: str, description: str) -> dict:
        short_text(description, "description", 3000)
        if item_id not in self.items:
            raise CoreError("item_missing", "请使用已登记题号。")
        if category not in ("question", "explanation", "software", "learning"):
            raise CoreError("invalid_category", "反馈类别无效。")
        with self.connection() as db:
            self.check_session(db, session_id)
        return {"ok": True, "draft_only": True, "uploaded": False,
                "draft": {"system_version": __import__("eju_core").__version__, "item_id": item_id,
                          "item_version": self.items[item_id]["version"], "category": category,
                          "description": description, "attachments": []},
                "message": "请先预览个人信息与贡献范围；本工具没有对外提交能力。"}

    def export_json(self) -> dict:
        with self.connection() as db:
            db.execute("BEGIN")
            return {"format": "eju-core-export", "schema_version": SCHEMA, "vault_id": self.meta(db, "vault_id"),
                    "revision": self.meta(db, "revision"), "profile": self.meta(db, "profile"),
                    "events": self.events(db), "legacy_draft": self.meta(db, "legacy_draft"),
                    "legacy_sources": [dict(r) for r in db.execute("SELECT * FROM imports")],
                    "artifacts": [dict(r) for r in db.execute("SELECT * FROM artifacts")],
                    "exported_at": stamp()}

    def backup(self, destination: str | Path) -> dict:
        target = Path(destination).expanduser().absolute()
        if target.exists() or is_link(target) or target.resolve().is_relative_to(self.root):
            raise CoreError("destination_exists", "备份应写入资料库外的一个新目录。")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".eju-backup-", dir=target.parent))
        try:
            copy_path = staging / "learning.sqlite"
            with self.connection() as source:
                dest = sqlite3.connect(copy_path)
                try:
                    source.backup(dest)
                    dest.execute("DELETE FROM sessions")
                    dest.commit()
                finally:
                    dest.close()
            snap = sqlite3.connect(copy_path)
            snap.row_factory = sqlite3.Row
            try:
                artifacts = [dict(r) for r in snap.execute("SELECT * FROM artifacts")]
                revision = json.loads(snap.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                vault_id = json.loads(snap.execute("SELECT value FROM meta WHERE key='vault_id'").fetchone()[0])
            finally:
                snap.close()
            entries = [{"path": "learning.sqlite", "sha256": sha(copy_path.read_bytes()), "size": copy_path.stat().st_size}]
            for row in artifacts:
                source_path = self.verify_artifact(row)
                destination_path = staging / row["relative_path"]
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source_path, destination_path)
                if sha(destination_path.read_bytes()) != row["sha256"]:
                    raise CoreError("backup_changed", "复制时资料发生变化，备份未完成。")
                entries.append({"path": row["relative_path"], "sha256": row["sha256"], "size": row["size"]})
            manifest = {"format": "eju-core-backup", "schema_version": SCHEMA, "vault_id": vault_id,
                        "revision": revision, "created_at": stamp(), "files": entries,
                        "excluded": ["inbox", "derived", "unregistered originals", "active MCP session tokens"]}
            (staging / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for file in staging.rglob("*"):
                if file.is_file():
                    os.chmod(file, 0o600)
            os.chmod(staging, 0o700)
            os.rename(staging, target)
            return {"ok": True, "backup_complete": True, "path": str(target), "revision": revision,
                    "files": len(entries), "encrypted": False,
                    "message": "本地一致快照已保存；此基础备份未加密。恢复测试另行执行。"}
        finally:
            if staging.exists():
                shutil.rmtree(staging)

    @classmethod
    def restore(cls, backup: str | Path, destination: str | Path) -> dict:
        source = Path(backup).expanduser().absolute()
        target = Path(destination).expanduser().absolute()
        if target.exists() or is_link(target):
            raise CoreError("destination_exists", "恢复只写入一个新目录，不覆盖现有资料库。")
        manifest_path = source / "manifest.json"
        if is_link(source) or is_link(manifest_path) or not manifest_path.is_file():
            raise CoreError("backup_invalid", "找不到普通备份清单。")
        regular_file(manifest_path, MAX_JSON_BYTES)
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise CoreError("backup_invalid", "备份清单不可读。") from exc
        if (not isinstance(manifest, dict) or manifest.get("format") != "eju-core-backup"
                or manifest.get("schema_version") not in (1, SCHEMA) or not isinstance(manifest.get("files"), list)):
            raise CoreError("backup_invalid", "备份格式或版本不支持。")
        if len(manifest["files"]) > 10001:
            raise CoreError("backup_invalid", "备份清单超过本版容量。")
        paths = set()
        entries = []
        for row in manifest["files"]:
            if not isinstance(row, dict):
                raise CoreError("backup_invalid", "备份条目格式无效。")
            relative = row.get("path")
            if not isinstance(relative, str):
                raise CoreError("backup_invalid", "备份条目缺少路径。")
            part = portable_relative(relative)
            if (part.is_absolute() or ".." in part.parts or relative in paths
                    or (relative != "learning.sqlite" and (len(part.parts) != 2 or part.parts[0] != "originals"))):
                raise CoreError("unsafe_path", "备份含重复或越界路径。")
            paths.add(relative)
            file = source / part
            if any(is_link(x) for x in [file, file.parent]) or not file.is_file():
                raise CoreError("backup_invalid", "备份文件缺失或为符号链接。")
            regular_file(file, 512 * 1024 * 1024 if relative == "learning.sqlite" else MAX_FILE_BYTES)
            if type(row.get("size")) is not int or file.stat().st_size != row["size"] or sha(file.read_bytes()) != row.get("sha256"):
                raise CoreError("backup_corrupt", "备份文件校验失败，未恢复。")
            entries.append((relative, file))
        if "learning.sqlite" not in paths:
            raise CoreError("backup_invalid", "备份缺少数据库。")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".eju-restore-", dir=target.parent))
        try:
            for relative, file in entries:
                out = staging / relative
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, out)
                os.chmod(out, 0o600)
                expected = next(row for row in manifest["files"] if row["path"] == relative)
                if out.stat().st_size != expected["size"] or sha(out.read_bytes()) != expected["sha256"]:
                    raise CoreError("backup_changed", "恢复过程中备份发生变化，未启用恢复库。")
            for directory in ("originals", "inbox", "derived"):
                (staging / directory).mkdir(exist_ok=True, mode=0o700)
            # A backups are upgraded only inside a new verified restoration directory.
            if manifest["schema_version"] == 1:
                with sqlite3.connect(staging / "learning.sqlite") as migrated:
                    actual = json.loads(migrated.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
                    if actual != 1:
                        raise CoreError("backup_invalid", "备份声明与数据库版本不一致。")
                    migrated.execute("UPDATE meta SET value=? WHERE key='schema_version'", (canonical(SCHEMA),))
            core = cls(staging)
            with core.connection() as db:
                if core.meta(db, "vault_id") != manifest.get("vault_id") or core.meta(db, "revision") != manifest.get("revision"):
                    raise CoreError("backup_invalid", "清单和数据库身份或修订不一致。")
                registered = {row["relative_path"] for row in db.execute("SELECT relative_path FROM artifacts")}
                if registered != paths - {"learning.sqlite"}:
                    raise CoreError("backup_invalid", "附件清单与数据库登记不一致。")
                for row in db.execute("SELECT * FROM artifacts"):
                    core.verify_artifact(row)
            os.rename(staging, target)
            return {"ok": True, "restored": True, "path": str(target), "revision": manifest["revision"],
                    "vault_id": manifest["vault_id"], "files_verified": len(entries)}
        finally:
            if staging.exists():
                shutil.rmtree(staging)


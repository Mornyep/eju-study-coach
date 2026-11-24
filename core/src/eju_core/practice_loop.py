"""Read-only study review and explicitly requested, atomic next practice.

Recorded answers, reason reviews and reflections are claims in the learning
record. This module neither promotes them to mastery nor writes profile state.
"""
from __future__ import annotations

import json

from .errors import CoreError
from .exposure import shares_exposure


def _text(value, maximum):
    return value[:maximum] if isinstance(value, str) else ""


class PracticeLoopMixin:
    def _practice_subject(self, subject, *, optional=False):
        if subject is None and optional:
            return
        if not isinstance(subject, str) or subject not in self.catalog["subjects"]:
            raise CoreError("invalid_subject", "请选择已登记的科目。")

    def _linked_practice_reflection(self, events, attempt):
        """Find the latest valid stored link, never a draft or inferred cause."""
        item = self.items.get(attempt.get("itemId"))
        if item is None:
            return None
        options = {row["value"]: row["label"] for row in self.reflection_options(item)}
        retracted = {e.get("targetId") for e in events if e.get("type") == "retract"}
        for event in reversed(events):
            if (event.get("type") != "reflection" or event.get("attemptId") != attempt["id"]
                    or event.get("id") in retracted
                    or event.get("itemId") != attempt.get("itemId")
                    or event.get("itemVersion") != attempt.get("itemVersion")
                    or event.get("task_id") != attempt.get("task_id")
                    or event.get("source") != "learner_self_report"
                    or event.get("cause") not in options
                    or not isinstance(event.get("evidence"), str) or not event["evidence"].strip()
                    or not isinstance(event.get("next_action", ""), str)):
                continue
            return {
                "id": event["id"], "cause": event["cause"],
                "cause_label": options[event["cause"]],
                "evidence": event["evidence"][:2000],
                "next_action": event.get("next_action", "")[:1200],
                "text_truncated": len(event["evidence"]) > 2000 or len(event.get("next_action", "")) > 1200,
                "self_report": True,
            }
        return None

    def _study_review(self, events, attempt):
        from .evidence import invalid_reason

        item = self.items.get(attempt.get("itemId"))
        review = next((event for event in reversed(events)
                       if event.get("type") == "reason_review" and event.get("targetId") == attempt["id"]), None)
        if review is not None:
            # This is the saved reviewer's claim, not a computed mastery result.
            note = review.get("note", "")
            review = {key: review[key] for key in ("id", "reviewer", "agrees", "result") if key in review}
            review.update(note=_text(note, 1000), note_truncated=isinstance(note, str) and len(note) > 1000)
        answer, reason = attempt.get("answer", ""), attempt.get("reason", "")
        return {
            "attempt_id": attempt["id"], "item_id": attempt.get("itemId"),
            "item_version": attempt.get("itemVersion"),
            "title": _text((item or {}).get("displayTitle") or (item or {}).get("title") or "题目待核对", 500),
            "subjects": list((item or {}).get("subjects", [])), "date": attempt.get("date"),
            "answer": _text(answer, 1000), "reason": _text(reason, 1000),
            "result": attempt.get("result"), "hint": attempt.get("hint"),
            "seen_before": attempt.get("seenBefore"), "guessed": attempt.get("guessed"),
            "invalid_reason": invalid_reason(events, attempt, item),
            "reason_review": review, "reflection": self._linked_practice_reflection(events, attempt),
            "text_truncated": any(isinstance(value, str) and len(value) > 1000 for value in (answer, reason)),
            "full_record_available": True,
        }

    def study_review_summary(self, events, limit=3):
        """Bound latest stored attempts for resume; no other tasks' solutions."""
        from .store import integer

        integer(limit, "limit", 1, 20)
        attempts = [event for event in reversed(events) if event.get("type") == "attempt"][:limit]
        return [self._study_review(events, attempt) for attempt in attempts]

    def read_study_review(self, session_id, subject=None, before=0, limit=10):
        from .store import integer

        self._practice_subject(subject, optional=True)
        integer(before, "before", 0, 2**53 - 1)
        integer(limit, "limit", 1, 20)
        with self.connection() as db:
            # Pin revision, joins and cursor rows to the same read snapshot.
            db.execute("BEGIN")
            self.check_session(db, session_id)
            rows = [(row["seq"], json.loads(row["body"]))
                    for row in db.execute("SELECT seq,body FROM events ORDER BY seq")]
            events = [event for _, event in rows]
            attempts = [(seq, event) for seq, event in reversed(rows)
                        if event.get("type") == "attempt" and (not before or seq < before)
                        and (subject is None or subject in self.items.get(event.get("itemId"), {}).get("subjects", []))]
            page = attempts[:limit]
            return {
                "ok": True, "revision": self.meta(db, "revision"),
                "reviews": [dict(self._study_review(events, event), seq=seq) for seq, event in page],
                "next_before": page[-1][0] if page else 0, "has_more": len(attempts) > limit,
                "subjects": dict(self.catalog["subjects"]),
            }

    def _next_practice_context(self, events, subject, basis_attempt_id=None, focus_id=None, *, focus=None):
        """Derive candidate context without state changes; also used inside start."""
        from .evidence import evidence_map, invalid_reason, item_status
        from .question_media import ready
        from .practice_focus import load_focus
        from .store import identifier, today

        self._practice_subject(subject)
        focus = focus if focus is not None else load_focus(self, subject, focus_id)
        source, reflection = None, None
        if basis_attempt_id is not None:
            identifier(basis_attempt_id, "basis_attempt_id")
            source = next((event for event in events if event.get("id") == basis_attempt_id
                           and event.get("type") == "attempt"), None)
            item = self.items.get(source.get("itemId")) if source else None
            invalid = invalid_reason(events, source, item) if source else "作答记录不存在。"
            if invalid or subject not in (item or {}).get("subjects", []):
                raise CoreError("basis_invalid", "来源作答已变化或不属于当前科目，请重新选择。",
                                invalid_reason=invalid or "来源作答不属于当前科目。")
            reflection = self._linked_practice_reflection(events, source)

        source_item = self.items[source["itemId"]] if source else None
        source_nodes = set((source_item or {}).get("nodes", []))
        seen = {event.get("itemId") for event in events if event.get("type") in ("attempt", "exposure")}
        seen_groups = {self.items[item_id].get("groupId") or item_id for item_id in seen if item_id in self.items}
        seen_items = [self.items[item_id] for item_id in seen if item_id in self.items]
        emap = evidence_map(events, list(self.items.values()), self.catalog["nodes"])
        labels = {node["id"]: node.get("label", node["id"]) for node in self.catalog["nodes"]}
        ranked = []
        current_date = today()
        for item in self.items.values():
            group = item.get("groupId") or item["id"]
            if ((focus.selected is not None and item["id"] not in focus.matching_items)
                    or item.get("kind") != "official" or subject not in item.get("subjects", [])
                    or item_status(events, item) != "reviewed_official"
                    or item["id"] in seen or group in seen_groups
                    or any(shares_exposure(item, other) for other in seen_items)
                    or not ready(item)):
                continue
            shared = sorted(source_nodes.intersection(item.get("nodes", [])))
            knowledge = [emap[node] for node in item.get("nodes", []) if node in emap]
            priority, knowledge_reason = 0, "当前尚无独立作答证据。"
            if any(row.get("status") == "needs_repair" for row in knowledge):
                priority, knowledge_reason = 2, "有关联的待修复表现，可继续独立核实。"
            elif any(row.get("due") and row["due"] <= current_date for row in knowledge):
                priority, knowledge_reason = 1, "有关联的到期复测，可继续独立核实。"
            relation = "shared_nodes" if shared else "same_subject"
            relation_reason = "与来源作答共享登记知识点。" if shared else "同一科目的完整官方题；未断言匹配具体错因。"
            candidate = {
                "item_id": item["id"], "item_version": item["version"],
                "title": _text(item.get("displayTitle") or item.get("title") or "官方练习", 500),
                "subjects": list(item.get("subjects", [])), "relation": relation,
                "shared_nodes": [{"id": node, "label": labels.get(node, node)} for node in shared],
                "reason": relation_reason + knowledge_reason,
                "image_count": len((item.get("delivery") or {}).get("assets", [])),
            }
            ranked.append((not bool(shared), -priority, item.get("order", 10000), item["id"], item, candidate))
        ranked.sort(key=lambda row: row[:4])
        candidates, chosen_groups, chosen_items = [], set(), []
        for _, _, _, _, item, candidate in ranked:
            group = item.get("groupId") or item["id"]
            if group in chosen_groups or any(shares_exposure(item, other) for other in chosen_items):
                continue
            candidates.append(candidate)
            chosen_groups.add(group)
            chosen_items.append(item)
            if len(candidates) == 3:
                break
        task = self.task_state(events)
        return {
            "subject": subject, "base_task_id": task["id"] if task else None,
            "basis_attempt_id": source["id"] if source else None,
            "basis_reflection_id": reflection["id"] if reflection else None,
            "basis": {"title": _text(source_item.get("displayTitle") or source_item.get("title") or "来源作答", 500),
                      "reflection_cause_label": reflection["cause_label"] if reflection else None,
                      "self_report": True} if source else None,
            "candidates": candidates, "no_candidate": not candidates,
            **focus.response_fields(),
            "message": "可选择一题开始；实际完整展示后才记录看题。" if candidates else
                       "当前没有符合所选重点、完整、已核对且未见过的官方材料；可以明确取消重点后重新选择。" if focus.selected is not None else
                       "当前没有完整、已核对且未见过的官方材料可继续；请补充完整官方材料后再选择。",
        }

    def get_next_practice(self, session_id, subject, basis_attempt_id=None, focus_id=None):
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            context = self._next_practice_context(self.events(db), subject, basis_attempt_id, focus_id)
            return {"ok": True, "revision": self.meta(db, "revision"), **context}

    def start_next_practice(self, session_id, expected_revision, request_id, item_id, item_version, subject,
                            base_task_id=None, basis_attempt_id=None, basis_reflection_id=None,
                            focus_id=None, expected_focus_version=None):
        from .store import identifier, stamp, today
        from .practice_focus import load_focus, validate_preview_version

        payload = dict(item_id=item_id, item_version=item_version, subject=subject, base_task_id=base_task_id,
                       basis_attempt_id=basis_attempt_id, basis_reflection_id=basis_reflection_id)
        # Preserve pre-focus unfiltered receipt identities across upgrades. Any
        # supplied focus component binds both fields, including an explicit null.
        if focus_id is not None or expected_focus_version is not None:
            payload.update(focus_id=focus_id, expected_focus_version=expected_focus_version)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "start_next_practice", payload,
                                                expected_revision, session_id)
            if replay:
                return replay
            # Dynamic validation must follow replay: a saved response remains
            # recoverable after later tasks, exposures, revisions or catalog edits.
            identifier(item_id, "item_id")
            identifier(item_version, "item_version")
            events = self.events(db)
            task = self.task_state(events)
            if (task["id"] if task else None) != base_task_id:
                raise CoreError("task_changed", "当前任务已变化，请重新读取下一题候选。")
            if task and task["status"] == "active":
                raise CoreError("task_active", "已有未完任务，请先完成当前任务。")
            if task and task["status"] == "paused":
                raise CoreError("task_paused", "当前任务已暂停，请先继续并完成它。")
            if task and task["status"] != "completed":
                raise CoreError("task_active", "当前任务尚未完成。")
            self._practice_subject(subject)
            focus = load_focus(self, subject, focus_id)
            validate_preview_version(focus, focus_id, expected_focus_version)
            context = self._next_practice_context(events, subject, basis_attempt_id, focus_id, focus=focus)
            if context["basis_reflection_id"] != basis_reflection_id:
                raise CoreError("basis_changed", "来源复盘已变化，请重新读取候选并确认。")
            item = self.items.get(item_id)
            if item and item.get("version") != item_version:
                raise CoreError("item_version_conflict", "候选题目版本已变化，请重新读取候选。")
            selected = next((candidate for candidate in context["candidates"]
                             if candidate["item_id"] == item_id and candidate["item_version"] == item_version), None)
            if selected is None:
                raise CoreError("candidate_unavailable", "这题已不可用或不在当前候选中，请重新选择。")
            event = dict(id=request_id, type="task", itemId=item_id, itemVersion=item_version,
                         instruction="先完整查看题面，再独立写下回答和依据；提交后核对并保存本人复盘。",
                         basis_attempt_id=basis_attempt_id, basis_reflection_id=basis_reflection_id,
                         focus_id=focus_id, focus_version=focus.version if focus_id is not None else None,
                         selection_relation=selected["relation"], createdAt=stamp(), date=today(), sessionId=session_id)
            self.insert_event(db, event, "native_workbench")
            return self.finish_request(db, request_id, digest, task_id=request_id,
                                       learner_exposure_recorded=False)

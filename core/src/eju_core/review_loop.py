"""Explicit review of old attempts and immutable, separately attributed tutor notes.

These projections describe recorded claims. They never promote a note, reflection,
or a redo of seen material into independent transfer or mastery evidence.
"""
from __future__ import annotations

import json

from .errors import CoreError
from .practice_loop import _text


class ReviewLoopMixin:
    @staticmethod
    def _review_attempt(events, attempt_id):
        from .store import identifier

        identifier(attempt_id, "attempt_id")
        attempt = next((event for event in events if event.get("type") == "attempt"
                        and event.get("id") == attempt_id), None)
        if attempt is None:
            raise CoreError("attempt_missing", "来源作答不存在，请重新选择。")
        return attempt

    @staticmethod
    def _review_reason(events, attempt):
        """Only use a valid recorded judgment, never a tutor-note inference."""
        retracted = {event.get("targetId") for event in events if event.get("type") == "retract"}
        for event in reversed(events):
            if (event.get("type") != "reason_review" or event.get("targetId") != attempt["id"]
                    or event.get("id") in retracted or type(event.get("agrees")) is not bool
                    or event.get("reviewer") not in ("self", "tutor")
                    or event.get("result", "ungraded") not in ("correct", "incorrect", "ungraded")
                    or not isinstance(event.get("note"), str) or not event["note"].strip()):
                continue
            return {"id": event["id"], "reviewer": event["reviewer"], "agrees": event["agrees"],
                    **({"result": event["result"]} if "result" in event else {}),
                    "note": event["note"][:1000], "note_truncated": len(event["note"]) > 1000}
        return None

    def _review_basis(self, events, attempt):
        reflection = self._linked_practice_reflection(events, attempt)
        reason_review = self._review_reason(events, attempt)
        return reflection, reason_review

    def _coaching_focus_context(self, attempt, unavailable):
        """One fresh metadata snapshot; only reviewed source-item bindings qualify."""
        from .practice_focus import FOCUS_SUBJECTS, _matches, _read_definition

        result = {"practice_focus_options": [], "practice_focus_version": None,
                  "practice_focus_available": False, "practice_focus_unavailable_reason": unavailable}
        if unavailable:
            return result
        item = self.items.get(attempt.get("itemId"), {})
        if not FOCUS_SUBJECTS.intersection(item.get("subjects", [])):
            result["practice_focus_unavailable_reason"] = "当前科目没有可用的原题练习方向。"
            return result
        try:
            version, definitions, rows = _read_definition()
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            result["practice_focus_unavailable_reason"] = "练习方向资料当前不可用，请重新读取。"
            return result
        result["practice_focus_version"] = version
        for focus_id, definition in definitions.items():
            subject = definition["subject"]
            if _matches(rows.get(item["id"]), item, self.coaching.get(item["id"]),
                        definitions, focus_id, subject):
                result["practice_focus_options"].append(
                    {key: definition[key] for key in ("id", "label", "description", "subject")})
                if len(result["practice_focus_options"]) == 20:
                    break
        result["practice_focus_available"] = bool(result["practice_focus_options"])
        if not result["practice_focus_available"]:
            result["practice_focus_unavailable_reason"] = "来源题没有当前有效且已审核的练习方向映射。"
        return result

    @staticmethod
    def _validate_reasoning_assessment(assessment, attempt, reason_review, open_question):
        from .store import short_text

        if not isinstance(assessment, dict) or set(assessment) != {"status", "reason_quote", "error_stage"}:
            raise CoreError("invalid_field", "reasoning_assessment 必须仅含 status、reason_quote 和 error_stage。")
        status, quote, stage = (assessment[key] for key in ("status", "reason_quote", "error_stage"))
        if status not in ("supported", "needs_revision", "needs_clarification"):
            raise CoreError("invalid_field", "理由判断状态无效。")
        stages = ("question_reading", "knowledge", "method", "calculation", "graph", "unknown")
        if ((status == "supported" and stage != "none")
                or (status == "needs_revision" and stage not in stages)
                or (status == "needs_clarification" and stage != "unknown")):
            raise CoreError("invalid_field", "理由判断与归因阶段不一致。")
        short_text(quote, "reason_quote", 500, empty=True)
        original = attempt.get("reason", "")
        if not isinstance(original, str):
            raise CoreError("reason_quote_mismatch", "来源理由不是可核对的文字。")
        if status == "needs_clarification" and not original.strip():
            valid_quote = quote == ""
        else:
            valid_quote = bool(quote.strip()) and quote in original
        if not valid_quote:
            raise CoreError("reason_quote_mismatch", "请逐字引用本次作答的原始理由，不补写或改写学习者的话。")
        tutor = reason_review is not None and reason_review["reviewer"] == "tutor"
        if status == "needs_clarification":
            short_text(open_question, "open_question", 1200)
            if tutor:
                raise CoreError("reason_review_conflict", "已有辅导者理由核对，请先核对当前依据；不能同时标为尚需澄清。")
        elif not tutor or reason_review["agrees"] != (status == "supported"):
            raise CoreError("reason_review_required", "理由判断必须对应当前有效的辅导者理由核对及其认可状态。")
        return dict(assessment)

    def _coaching_projection(self, events, attempt, reflection, reason_review, focus_context):
        note = next((event for event in reversed(events)
                     if event.get("type") == "coaching_note" and event.get("attemptId") == attempt["id"]
                     and event.get("itemId") == attempt.get("itemId")
                     and event.get("itemVersion") == attempt.get("itemVersion")
                     and event.get("source") == "tutor_summary"), None)
        if note is None:
            return None
        reflection_id = reflection["id"] if reflection else None
        reason_id = reason_review["id"] if reason_review else None
        stale = (note.get("basis_reflection_id") != reflection_id
                 or note.get("basis_reason_review_id") != reason_id)
        projected = {
            "id": note["id"], "summary": _text(note.get("summary"), 2000),
            "hypothesis": _text(note.get("hypothesis"), 1000),
            "next_step": _text(note.get("next_step"), 1200),
            "open_question": _text(note.get("open_question"), 1200),
            "date": note.get("date"), "source": "tutor_summary",
            "attemptId": attempt["id"], "itemId": attempt.get("itemId"),
            "itemVersion": attempt.get("itemVersion"),
            "basis_reflection_id": note.get("basis_reflection_id"),
            "basis_reason_review_id": note.get("basis_reason_review_id"),
            "previous_id": note.get("previous_id"),
            "stale": stale,
            "stale_reason": "本人复盘或理由核对已变化，请重新确认辅导说明。" if stale else "",
        }
        assessment = note.get("reasoning_assessment")
        if isinstance(assessment, dict):
            projected["reasoning_assessment"] = {
                key: _text(assessment.get(key), maximum)
                for key, maximum in (("status", 32), ("reason_quote", 500), ("error_stage", 32))}
        recommendation = note.get("practice_recommendation")
        if isinstance(recommendation, dict):
            frozen = {key: _text(recommendation.get(key), maximum) for key, maximum in
                      (("id", 120), ("label", 120), ("description", 1000), ("subject", 40), ("version", 64))}
            reason = projected["stale_reason"]
            if not reason and not focus_context["practice_focus_available"]:
                reason = focus_context["practice_focus_unavailable_reason"]
            if not reason and frozen["version"] != focus_context["practice_focus_version"]:
                reason = "练习方向资料已变化，请重新核对建议。"
            if not reason and not any(all(frozen[key] == option[key] for key in
                                         ("id", "label", "description", "subject"))
                                      for option in focus_context["practice_focus_options"]):
                reason = "来源题的练习方向映射已变化或不可用。"
            if not reason and (not reason_review or reason_review["reviewer"] != "tutor"
                               or not isinstance(assessment, dict)
                               or assessment.get("status") not in ("supported", "needs_revision")):
                reason = "当前建议缺少有效的辅导者理由判断。"
            projected["practice_recommendation"] = dict(frozen, available=not bool(reason),
                                                       stale=bool(reason), reason=reason)
        return projected

    def _review_context(self, events, attempt):
        from .question_media import ready

        reflection, reason_review = self._review_basis(events, attempt)
        bounded_attempt = self._study_review(events, attempt)
        # Keep the displayed judgment aligned with the exact valid saved basis;
        # malformed historical events remain available through raw record reads.
        bounded_attempt["reason_review"] = reason_review
        item = self.items.get(attempt.get("itemId"))
        latest = next((event for event in reversed(events) if event.get("type") == "attempt"
                       and event.get("itemId") == attempt.get("itemId")), None)
        unavailable = bounded_attempt["invalid_reason"]
        if not unavailable and latest and latest["id"] != attempt["id"]:
            unavailable = "source_changed：同一题已有更新作答，请从最新作答开始重做。"
        if not unavailable and not ready(item):
            unavailable = "完整题面或必要图片不可用，请先核对材料。"
        focus_context = self._coaching_focus_context(attempt, unavailable)
        note = self._coaching_projection(events, attempt, reflection, reason_review, focus_context)
        task = self.task_state(events)
        context = {
            "attempt": bounded_attempt, "coaching_note": note,
            "basis_reflection_id": reflection["id"] if reflection else None,
            "basis_reason_review_id": reason_review["id"] if reason_review else None,
            "base_note_id": note["id"] if note else None,
            "base_task_id": task["id"] if task else None,
            "review_available": not bool(unavailable), "review_unavailable_reason": unavailable,
            "previous_attempt_id": None, "previous_coaching_note": None,
            **focus_context,
        }
        # One validated hop preserves the previous explanation after a redo.
        # Historical notes never become the new attempt's note or queue basis.
        source_task = next((event for event in events if event.get("type") == "task"
                            and event.get("id") == attempt.get("task_id")
                            and event.get("practice_mode") == "review"
                            and event.get("itemId") == attempt.get("itemId")
                            and event.get("itemVersion") == attempt.get("itemVersion")), None)
        if source_task:
            previous = next((event for event in events if event.get("type") == "attempt"
                             and event.get("id") == source_task.get("review_source_attempt_id")
                             and event.get("id") != attempt["id"]
                             and event.get("itemId") == attempt.get("itemId")
                             and event.get("itemVersion") == attempt.get("itemVersion")), None)
            if previous:
                context["previous_attempt_id"] = previous["id"]
                old_reflection, old_reason = self._review_basis(events, previous)
                old_focus = self._coaching_focus_context(previous, "历史作答的建议仅供回顾，请从最新作答核对。")
                old_note = self._coaching_projection(events, previous, old_reflection, old_reason, old_focus)
                context["previous_coaching_note"] = dict(old_note, historical=True) if old_note else None
        return context

    def read_coaching_context(self, session_id, attempt_id):
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            events = self.events(db)
            attempt = self._review_attempt(events, attempt_id)
            return {"ok": True, "revision": self.meta(db, "revision"),
                    "context": self._review_context(events, attempt)}

    @staticmethod
    def _review_context_ids(context, base_note_id, basis_reflection_id, basis_reason_review_id):
        from .store import identifier

        for key, value in (("base_note_id", base_note_id), ("basis_reflection_id", basis_reflection_id),
                           ("basis_reason_review_id", basis_reason_review_id)):
            if value is not None:
                identifier(value, key)
        if context["base_note_id"] != base_note_id:
            raise CoreError("coaching_conflict", "辅导说明已变化，请读取并比较最新说明。")
        if (context["basis_reflection_id"] != basis_reflection_id
                or context["basis_reason_review_id"] != basis_reason_review_id):
            raise CoreError("basis_changed", "本人复盘或理由核对已变化，请读取最新依据。")

    def save_coaching_note(self, session_id, expected_revision, request_id, attempt_id, summary,
                           next_step, open_question="", hypothesis="", base_note_id=None,
                           basis_reflection_id=None, basis_reason_review_id=None,
                           reasoning_assessment=None, practice_focus_id=None, practice_focus_version=None):
        from .store import short_text, stamp, today

        payload = dict(attempt_id=attempt_id, summary=summary, next_step=next_step,
                       open_question=open_question, hypothesis=hypothesis, base_note_id=base_note_id,
                       basis_reflection_id=basis_reflection_id, basis_reason_review_id=basis_reason_review_id)
        # Omitted and explicit-null additions retain the exact Beta8 request digest.
        for key, value in (("reasoning_assessment", reasoning_assessment),
                           ("practice_focus_id", practice_focus_id), ("practice_focus_version", practice_focus_version)):
            if value is not None:
                payload[key] = value
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "save_coaching_note", payload,
                                                expected_revision, session_id)
            if replay:
                return replay
            for key, value, maximum, empty in (("summary", summary, 2000, False),
                                               ("next_step", next_step, 1200, False),
                                               ("open_question", open_question, 1200, True),
                                               ("hypothesis", hypothesis, 1000, True)):
                short_text(value, key, maximum, empty=empty)
            events = self.events(db)
            attempt = self._review_attempt(events, attempt_id)
            context = self._review_context(events, attempt)
            if context["attempt"]["invalid_reason"]:
                raise CoreError("source_invalid", "来源作答已失效，不能保存新的辅导说明。",
                                invalid_reason=context["attempt"]["invalid_reason"])
            self._review_context_ids(context, base_note_id, basis_reflection_id, basis_reason_review_id)
            optional = {}
            if reasoning_assessment is not None:
                optional["reasoning_assessment"] = self._validate_reasoning_assessment(
                    reasoning_assessment, attempt, context["attempt"]["reason_review"], open_question)
            if (practice_focus_id is None) != (practice_focus_version is None):
                raise CoreError("invalid_field", "练习方向 ID 和版本必须一同提供。")
            if practice_focus_id is not None:
                short_text(practice_focus_id, "practice_focus_id", 120)
                short_text(practice_focus_version, "practice_focus_version", 64)
                if reasoning_assessment is None or reasoning_assessment["status"] == "needs_clarification":
                    raise CoreError("reason_review_required", "练习建议需要已有的辅导者理由判断；尚需澄清时不能推荐方向。")
                if not context["review_available"]:
                    code = "source_changed" if context["review_unavailable_reason"].startswith("source_changed") else "material_unavailable"
                    raise CoreError(code, context["review_unavailable_reason"])
                if context["practice_focus_version"] is None:
                    raise CoreError("focus_unavailable", context["practice_focus_unavailable_reason"])
                if practice_focus_version != context["practice_focus_version"]:
                    raise CoreError("focus_changed", "练习方向资料已变化，请重新读取并核对建议。")
                selected = next((option for option in context["practice_focus_options"]
                                 if option["id"] == practice_focus_id), None)
                if selected is None:
                    raise CoreError("focus_unavailable", "来源题没有这个当前有效且已审核的练习方向。")
                optional["practice_recommendation"] = dict(selected, version=practice_focus_version)
            event = dict(id=request_id, type="coaching_note", attemptId=attempt_id,
                         itemId=attempt["itemId"], itemVersion=attempt["itemVersion"],
                         summary=summary, next_step=next_step, open_question=open_question,
                         hypothesis=hypothesis, source="tutor_summary", previous_id=base_note_id,
                         basis_reflection_id=basis_reflection_id, basis_reason_review_id=basis_reason_review_id,
                         createdAt=stamp(), date=today(), sessionId=session_id, **optional)
            self.insert_event(db, event, "native_codex")
            return self.finish_request(db, request_id, digest, coaching_note_id=request_id,
                                       source_attempt_id=attempt_id)

    def _review_queue_entries(self, events, rows=None, subject=None):
        # Choose latest per item before applying the cursor or source validity.
        rows = rows if rows is not None else list(enumerate(events, 1))
        latest, entries = set(), []
        for seq, attempt in reversed(rows):
            if attempt.get("type") != "attempt" or attempt.get("itemId") in latest:
                continue
            latest.add(attempt.get("itemId"))
            item = self.items.get(attempt.get("itemId"), {})
            if subject is not None and subject not in item.get("subjects", []):
                continue
            context = self._review_context(events, attempt)
            review = context["attempt"]
            reflection, reason_review = self._review_basis(events, attempt)
            note, reasons = context["coaching_note"], []
            if note and "reasoning_assessment" in note:
                # Quotes belong to the exact answer context, not repeated list summaries.
                note = dict(note, reasoning_assessment={key: note["reasoning_assessment"][key]
                                                       for key in ("status", "error_stage")})
            def reason(code, label):
                reasons.append({"code": code, "label": label})
            if not context["review_available"]:
                status = "unavailable"
                reason("source_unavailable", context["review_unavailable_reason"])
            else:
                effective_result = attempt.get("result")
                if reason_review and not reason_review["agrees"]:
                    effective_result = "incorrect"
                elif effective_result == "ungraded" and reason_review:
                    effective_result = reason_review.get("result", "ungraded")
                if effective_result != "correct":
                    reason("answer_incorrect" if effective_result == "incorrect" else "answer_ungraded",
                           "本次回答仍需核对。" if effective_result == "incorrect" else "本次回答尚未评分。")
                if reason_review is None:
                    reason("reason_unreviewed", "本次作答理由尚未单独核对。")
                elif not reason_review["agrees"]:
                    reason("reason_disagrees", "已记录的理由核对尚不认可本次依据。")
                current_note = note is not None and not note["stale"]
                if reflection and reflection["cause"] != "independent_ok" and not current_note:
                    reason("reflection_open", "本人复盘仍有待解释的问题；这仅是本人报告。")
                if note and note["stale"]:
                    reason("coaching_stale", note["stale_reason"])
                if note and note["open_question"].strip():
                    reason("open_question", "辅导说明仍保留待确认问题。")
                status = "pending" if reasons else "checked"
                if status == "checked":
                    reason("attempt_reason_checked", "仅本次作答理由已核对；见过或受助作答不证明独立迁移。")
            entry = {key: review[key] for key in ("attempt_id", "item_id", "item_version", "title", "subjects",
                                                   "date", "result", "seen_before", "hint", "guessed", "invalid_reason")}
            entry.update(seq=seq, reason_review=reason_review, reflection=reflection, coaching_note=note,
                         basis_reflection_id=context["basis_reflection_id"],
                         basis_reason_review_id=context["basis_reason_review_id"], base_note_id=context["base_note_id"],
                         status=status, reasons=reasons, review_available=context["review_available"],
                         review_unavailable_reason=context["review_unavailable_reason"])
            entries.append(entry)
        return entries

    def read_review_queue(self, session_id, subject=None, before=0, limit=10, include_checked=False):
        from .store import integer

        self._practice_subject(subject, optional=True)
        integer(before, "before", 0, 2**53 - 1)
        integer(limit, "limit", 1, 20)
        if type(include_checked) is not bool:
            raise CoreError("invalid_field", "include_checked 必须是布尔值。")
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            rows = [(row["seq"], json.loads(row["body"]))
                    for row in db.execute("SELECT seq,body FROM events ORDER BY seq")]
            events = [event for _, event in rows]
            entries = self._review_queue_entries(events, rows, subject)
            filtered = [entry for entry in entries if (include_checked or entry["status"] != "checked")
                        and (not before or entry["seq"] < before)]
            page = filtered[:limit]
            task = self.task_state(events)
            return {"ok": True, "revision": self.meta(db, "revision"), "subjects": dict(self.catalog["subjects"]),
                    "entries": page, "next_before": page[-1]["seq"] if page else 0,
                    "has_more": len(filtered) > limit, "base_task_id": task["id"] if task else None,
                    **{status + "_count": sum(entry["status"] == status for entry in entries)
                       for status in ("pending", "checked", "unavailable")}}

    def start_review_practice(self, session_id, expected_revision, request_id, source_attempt_id,
                              item_version, base_task_id=None, base_note_id=None,
                              basis_reflection_id=None, basis_reason_review_id=None):
        from .store import identifier, stamp, today
        from .question_media import ready

        payload = dict(source_attempt_id=source_attempt_id, item_version=item_version, base_task_id=base_task_id,
                       base_note_id=base_note_id, basis_reflection_id=basis_reflection_id,
                       basis_reason_review_id=basis_reason_review_id)
        with self.connection(write=True) as db:
            digest, replay = self.begin_request(db, request_id, "start_review_practice", payload,
                                                expected_revision, session_id)
            if replay:
                return replay
            identifier(item_version, "item_version")
            if base_task_id is not None:
                identifier(base_task_id, "base_task_id")
            events = self.events(db)
            task = self.task_state(events)
            if (task["id"] if task else None) != base_task_id:
                raise CoreError("task_changed", "当前任务已变化，请重新读取复习队列。")
            if task and task["status"] == "paused":
                raise CoreError("task_paused", "当前任务已暂停，请先继续并完成它。")
            if task and task["status"] != "completed":
                raise CoreError("task_active", "已有未完任务，请先完成当前任务。")
            attempt = self._review_attempt(events, source_attempt_id)
            item = self.items.get(attempt.get("itemId"))
            if attempt.get("itemVersion") != item_version or (item and item.get("version") != item_version):
                raise CoreError("item_version_conflict", "来源题目版本已变化，请重新读取复习记录。")
            context = self._review_context(events, attempt)
            if context["attempt"]["invalid_reason"]:
                raise CoreError("source_invalid", "来源作答已失效，不能开始重做。",
                                invalid_reason=context["attempt"]["invalid_reason"])
            latest = next(event for event in reversed(events) if event.get("type") == "attempt"
                          and event.get("itemId") == attempt.get("itemId"))
            if latest["id"] != source_attempt_id:
                raise CoreError("source_changed", "同一题已有更新作答，请从最新作答开始重做。")
            if not ready(item):
                raise CoreError("material_unavailable", "完整题面或必要图片不可用，不能开始重做。")
            self._review_context_ids(context, base_note_id, basis_reflection_id, basis_reason_review_id)
            event = dict(id=request_id, type="task", itemId=attempt["itemId"], itemVersion=item_version,
                         practice_mode="review", review_source_attempt_id=source_attempt_id,
                         base_note_id=base_note_id, basis_reflection_id=basis_reflection_id,
                         basis_reason_review_id=basis_reason_review_id,
                         instruction="这是已经做过的旧题重做。完整查看题面后，重新写下回答和依据；本次表现不证明新题独立迁移。",
                         createdAt=stamp(), date=today(), sessionId=session_id)
            self.insert_event(db, event, "native_workbench")
            return self.finish_request(db, request_id, digest, task_id=request_id,
                                       learner_exposure_recorded=False, practice_mode="review")

    def review_queue_summary(self, events, limit=3):
        from .store import integer

        integer(limit, "limit", 1, 3)
        entries = self._review_queue_entries(events)
        summary = []
        for entry in entries[:limit]:
            note = entry["coaching_note"]
            if note:
                note = {key: note[key] for key in ("id", "source", "stale", "stale_reason")}
                original = entry["coaching_note"]
                note.update({key: _text(original[key], maximum)
                             for key, maximum in (("summary", 350), ("next_step", 250),
                                                  ("open_question", 250), ("hypothesis", 250))})
                if "reasoning_assessment" in original:
                    note["reasoning_assessment"] = dict(original["reasoning_assessment"])
                if "practice_recommendation" in original:
                    note["practice_recommendation"] = dict(original["practice_recommendation"])
                    note["practice_recommendation"]["description"] = _text(
                        note["practice_recommendation"]["description"], 250)
            summary.append({"attempt_id": entry["attempt_id"], "item_id": entry["item_id"],
                            "item_version": entry["item_version"], "title": entry["title"][:200],
                            "status": entry["status"], "reasons": entry["reasons"], "coaching_note": note})
        return {"entries": summary,
                **{status + "_count": sum(entry["status"] == status for entry in entries)
                   for status in ("pending", "checked", "unavailable")}}

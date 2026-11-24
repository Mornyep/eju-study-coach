"""Append-only learner writing and source-bound tutor feedback, without grading.

All state lives in the existing events/requests tables. Draft text is verbatim;
comparisons are literal text pairs, never inferred intent or mastery judgments.
"""
from __future__ import annotations

import json

from .errors import CoreError


class WritingLoopMixin:
    @staticmethod
    def _writing_text(value, name, maximum, *, empty=False):
        from .store import short_text

        short_text(value, name, maximum, empty=empty)
        try:
            value.encode("utf-8")
        except UnicodeError as exc:
            raise CoreError("invalid_field", f"{name} 必须是有效的 Unicode 文字。") from exc
        return value

    @staticmethod
    def _writing_fingerprint(item):
        from .store import canonical, sha

        # Bind all registered source content, including unversioned corrections.
        return sha(canonical(item).encode("utf-8"))

    def _writing_availability(self, events, item, root=None):
        from .evidence import item_status
        from .question_media import ready

        if item is None:
            return "source_missing", "题目不在当前目录中；已存原稿仍可读取。"
        if "japanese_writing" not in item.get("subjects", []):
            return "source_invalid", "该题不属于日语记述任务。"
        kind = item.get("kind")
        if kind not in ("original", "official") or item_status(events, item) != "reviewed_" + kind:
            return "source_invalid", "题目尚未核对、存在争议或已被纠正；请先核实来源。"
        if root and (item.get("version") != root["itemVersion"]
                     or self._writing_fingerprint(item) != root["source_fingerprint"]):
            return "source_changed", "题目内容或版本已变化；已存原稿与反馈仅作历史读取。"
        stem = item.get("stem")
        if not isinstance(stem, str) or not stem.strip() or len(stem) > 16000 or not ready(item):
            return "material_unavailable", "完整题面或所需原图不可用；请先恢复材料。"
        return None, ""

    @staticmethod
    def _writing_rows(db):
        return [(row["seq"], json.loads(row["body"]))
                for row in db.execute("SELECT seq,body FROM events ORDER BY seq")]

    def _writing_source_view(self, events, item, root=None):
        """Expose only verified current image descriptors, never local paths.

        Historical roots did not store source URLs or media. Do not attach a
        replacement source's pages to their unchanged historical text.
        """
        from .question_media import assets

        code, reason = self._writing_availability(events, item, root)
        images, source_url = [], None
        if code is None:
            try:
                images = [{key: row[key] for key in ("index", "sha256", "width", "height", "pdf_page", "alt", "media_type")}
                          for row in assets(item)]
                source_url = (item.get("delivery") or {}).get("source_url")
            except (CoreError, OSError, ValueError, TypeError, KeyError, AttributeError):
                # Files can disappear between the readiness check and this read.
                code, reason = "material_unavailable", "完整题面或所需原图不可用；请先恢复材料。"
                images = []
        return code, reason, images, source_url

    def _writing_resume_summary(self, rows, limit=3):
        """Project genuine version/feedback activity from one caller's snapshot."""
        events = [event for _, event in rows]
        drafts = {}
        for seq, event in rows:
            if (event.get("type") == "writing_version" and event.get("base_version_id") is None
                    and event.get("draft_id") == event.get("id")):
                drafts[event["id"]] = dict(root=event, latest=event, versions={event["id"]},
                                           feedback=None, activity=(seq, event["createdAt"]))
            elif event.get("type") == "writing_version" and event.get("draft_id") in drafts:
                draft = drafts[event["draft_id"]]
                # Unrelated or orphan records cannot manufacture recent activity.
                if event.get("base_version_id") != draft["latest"]["id"]:
                    continue
                draft["latest"] = event
                draft["versions"].add(event["id"])
                draft["feedback"] = None
                draft["activity"] = (seq, event["createdAt"])
            elif event.get("type") == "writing_feedback" and event.get("draft_id") in drafts:
                draft = drafts[event["draft_id"]]
                if event.get("source_version_id") not in draft["versions"]:
                    continue
                draft["activity"] = (seq, event["createdAt"])
                if event["source_version_id"] == draft["latest"]["id"]:
                    draft["feedback"] = event
        result = []
        for draft in sorted(drafts.values(), key=lambda row: row["activity"][0], reverse=True)[:limit]:
            root, latest, feedback = draft["root"], draft["latest"], draft["feedback"]
            code, reason = self._writing_availability(events, self.items.get(root["itemId"]), root)
            state = ("source_unavailable" if code else "empty_draft" if not latest["text"].strip()
                     else "feedback_available" if feedback else "awaiting_feedback")
            result.append(dict(draft_id=root["id"], item_id=root["itemId"], item_version=root["itemVersion"],
                title=root["title"], kind=root["kind"], practice_label=root["practice_label"],
                latest_version_id=latest["id"], latest_excerpt=latest["text"][:80], version_count=len(draft["versions"]),
                last_activity_at=draft["activity"][1], activity_seq=draft["activity"][0], source_available=code is None,
                source_unavailable_code=code, source_unavailable_reason=reason, state=state,
                current_feedback_id=feedback["id"] if feedback and code is None else None))
        return dict(drafts=result, draft_count=len(drafts), has_more=len(drafts) > len(result))

    def read_writing_resume(self, session_id, limit=3):
        """Read recent writing activity without mutating drafts, profile or exposure."""
        from .store import integer

        integer(limit, "limit", 1, 10)
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            summary = self._writing_resume_summary(self._writing_rows(db), limit)
            return dict(ok=True, revision=self.meta(db, "revision"), **summary)

    @staticmethod
    def _writing_root(events, draft_id):
        from .store import identifier

        identifier(draft_id, "draft_id")
        root = next((event for event in events if event.get("id") == draft_id
                     and event.get("type") == "writing_version"
                     and event.get("draft_id") == draft_id and event.get("base_version_id") is None), None)
        if root is None:
            raise CoreError("writing_missing", "没有这份写作草稿；请重新读取草稿列表。")
        return root

    @staticmethod
    def _writing_versions(events, draft_id):
        return [event for event in events if event.get("type") == "writing_version"
                and event.get("draft_id") == draft_id]

    @staticmethod
    def _writing_version(event, *, summary=False):
        result = {"id": event["id"], "base_version_id": event["base_version_id"],
                  "created_at": event["createdAt"], "author": "learner",
                  "character_count": len(event["text"]), "empty": not event["text"].strip()}
        if not summary:
            result["text"] = event["text"]
        return result

    @staticmethod
    def _writing_feedback_view(event, latest_id, latest_feedback_id, source_available):
        if event is None:
            return None
        historical = event["source_version_id"] != latest_id or event["id"] != latest_feedback_id
        result = {key: event[key] for key in ("id", "draft_id", "source_version_id", "task_response",
                  "task_evidence_quote", "task_comment", "issues", "base_feedback_id")}
        result.update(created_at=event["createdAt"], author="tutor", historical=historical,
                      stale=historical or not source_available,
                      stale_reason="source_unavailable" if not source_available else "historical_feedback" if historical else "",
                      current=not historical and source_available)
        return result

    def read_writing_drafts(self, session_id, before=0, limit=10):
        """Bounded read-only index; candidates are explicitly labeled by origin."""
        from .store import integer

        integer(before, "before", 0, 2**53 - 1)
        integer(limit, "limit", 1, 20)
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            rows = self._writing_rows(db)
            events = [event for _, event in rows]
            roots = [(seq, event) for seq, event in reversed(rows)
                     if event.get("type") == "writing_version" and event.get("base_version_id") is None
                     and event.get("draft_id") == event.get("id") and (not before or seq < before)]
            page, drafts = roots[:limit], []
            for seq, root in page:
                versions = self._writing_versions(events, root["id"])
                code, reason = self._writing_availability(events, self.items.get(root["itemId"]), root)
                drafts.append(dict(draft_id=root["id"], item_id=root["itemId"], item_version=root["itemVersion"],
                    title=root["title"], kind=root["kind"], practice_label=root["practice_label"],
                    latest_version_id=versions[-1]["id"], latest_excerpt=versions[-1]["text"][:80], version_count=len(versions),
                    updated_at=versions[-1]["createdAt"], seq=seq, source_available=code is None,
                    source_unavailable_reason=reason, source_unavailable_code=code))
            candidates = []
            for item in sorted(self.items.values(), key=lambda row: (
                    0 if row.get("kind") == "official" else 1,
                    {"transfer": 0, "delayed": 1}.get(row.get("role"), 2), row.get("order", 10000), row["id"])):
                if "japanese_writing" not in item.get("subjects", []):
                    continue
                code, _, images, source_url = self._writing_source_view(events, item)
                if code:
                    continue
                candidates.append(dict(item_id=item["id"], item_version=item["version"],
                    title=(item.get("displayTitle") or item.get("title") or "日语记述")[:500], kind=item["kind"],
                    practice_label="原创辅助练习" if item["kind"] == "original" else "官方题写作练习",
                    stem=item["stem"], role=item.get("role"), source_fingerprint=self._writing_fingerprint(item), ready=True,
                    images=images, source_url=source_url,
                    images_required=bool(item.get("visualRequired") or item["kind"] == "official")))
            return dict(ok=True, revision=self.meta(db, "revision"), drafts=drafts,
                        next_before=page[-1][0] if page else 0, has_more=len(roots) > limit,
                        available_items=candidates[:20], available_items_truncated=len(candidates) > 20,
                        available_item_count=len(candidates))

    def read_writing_context(self, session_id, draft_id, version_id=None, versions_before=0,
                             feedback_before=0, limit=10):
        from .store import identifier, integer

        integer(versions_before, "versions_before", 0, 2**53 - 1)
        integer(feedback_before, "feedback_before", 0, 2**53 - 1)
        integer(limit, "limit", 1, 20)
        if version_id is not None:
            identifier(version_id, "version_id")
        with self.connection() as db:
            db.execute("BEGIN")
            self.check_session(db, session_id)
            rows = self._writing_rows(db)
            events = [event for _, event in rows]
            root = self._writing_root(events, draft_id)
            versions = self._writing_versions(events, draft_id)
            latest = versions[-1]
            selected = next((event for event in versions if event["id"] == version_id), None) if version_id else latest
            if selected is None:
                raise CoreError("writing_version_missing", "这份草稿没有该版本；未读取其他草稿内容。")
            code, reason, images, source_url = self._writing_source_view(events, self.items.get(root["itemId"]), root)
            feedbacks = [event for event in events if event.get("type") == "writing_feedback"
                         and event.get("draft_id") == draft_id]
            latest_feedback = next((event for event in reversed(feedbacks)
                                    if event["source_version_id"] == latest["id"]), None)
            selected_feedback = next((event for event in reversed(feedbacks)
                                      if event["source_version_id"] == selected["id"]), None)
            latest_feedback_id = latest_feedback["id"] if latest_feedback else None
            feedback_view = lambda event: self._writing_feedback_view(event, latest["id"], latest_feedback_id, code is None)
            version_rows = [(seq, event) for seq, event in reversed(rows)
                            if event.get("type") == "writing_version" and event.get("draft_id") == draft_id
                            and (not versions_before or seq < versions_before)]
            feedback_rows = [(seq, event) for seq, event in reversed(rows)
                             if event.get("type") == "writing_feedback" and event.get("draft_id") == draft_id
                             and (not feedback_before or seq < feedback_before)]
            vp, fp = version_rows[:limit], feedback_rows[:limit]
            # Comparison always follows the selected version's actual parent.
            # The default selected version is latest, never a newer learner text overwritten by an old one.
            parent = next((event for event in versions if event["id"] == selected["base_version_id"]), None)
            comparison = None if parent is None else dict(before_version_id=parent["id"], after_version_id=selected["id"],
                before_text=parent["text"], after_text=selected["text"], changed=parent["text"] != selected["text"],
                interpretation="literal_text_only")
            context = dict(draft_id=draft_id, item_id=root["itemId"], item_version=root["itemVersion"],
                title=root["title"], kind=root["kind"], practice_label=root["practice_label"], prompt=root["prompt"],
                source_fingerprint=root["source_fingerprint"], source_available=code is None,
                source_unavailable_reason=reason, source_unavailable_code=code,
                ready=code is None, images_required=root["images_required"], images=images, source_url=source_url,
                original_version=self._writing_version(root), latest_version=self._writing_version(latest),
                selected_version=self._writing_version(selected), base_version_id=latest["id"],
                versions=[dict(self._writing_version(event, summary=True), seq=seq) for seq, event in vp],
                versions_next_before=vp[-1][0] if vp else 0, versions_has_more=len(version_rows) > limit,
                current_feedback=feedback_view(latest_feedback) if code is None else None,
                selected_feedback=feedback_view(selected_feedback),
                feedback_history=[dict(feedback_view(event), seq=seq) for seq, event in fp],
                feedback_next_before=fp[-1][0] if fp else 0, feedback_has_more=len(feedback_rows) > limit,
                comparison=comparison, evidence_scope="writing_practice_only")
            return dict(ok=True, revision=self.meta(db, "revision"), context=context)

    def save_writing_draft(self, session_id, expected_revision, request_id, item_id, item_version, text,
                           draft_id=None, base_version_id=None, source_fingerprint=None):
        from .store import identifier, stamp, today

        payload = dict(item_id=item_id, item_version=item_version, text=text, draft_id=draft_id,
                       base_version_id=base_version_id, source_fingerprint=source_fingerprint)
        with self.connection(write=True) as db:
            try:
                digest, replay = self.begin_request(db, request_id, "save_writing_draft", payload, expected_revision, session_id)
            except UnicodeError as exc:
                raise CoreError("invalid_field", "写作内容必须是有效的 Unicode 文字。") from exc
            if replay:
                return replay
            identifier(item_id, "item_id")
            identifier(item_version, "item_version")
            self._writing_text(text, "text", 8000, empty=True)
            events = self.events(db)
            item = self.items.get(item_id)
            root = None
            if (draft_id is None) != (base_version_id is None):
                raise CoreError("invalid_field", "修订时必须同时提供 draft_id 与 base_version_id。")
            if draft_id is not None:
                root = self._writing_root(events, draft_id)
                identifier(base_version_id, "base_version_id")
                if root["itemId"] != item_id or root["itemVersion"] != item_version:
                    raise CoreError("writing_source_conflict", "修订必须保留原稿的题目与版本。")
                if self._writing_versions(events, draft_id)[-1]["id"] != base_version_id:
                    raise CoreError("writing_version_conflict", "草稿已有较新版本，请先读取并比较；原输入未覆盖。")
            code, reason = self._writing_availability(events, item, root)
            if code:
                raise CoreError(code, reason)
            if item["version"] != item_version:
                raise CoreError("item_version_conflict", "题目版本已变化，请重新读取写作任务。")
            expected_source = root["source_fingerprint"] if root else self._writing_fingerprint(item)
            if not isinstance(source_fingerprint, str) or source_fingerprint != expected_source:
                raise CoreError("source_changed", "题目指纹缺失或已变化，请重新读取并核对题面。")
            event = dict(id=request_id, type="writing_version", draft_id=draft_id or request_id,
                base_version_id=base_version_id, itemId=item_id, itemVersion=item_version, text=text,
                source="learner_draft", createdAt=stamp(), date=today(), sessionId=session_id)
            if root is None:
                event.update(source_fingerprint=source_fingerprint, prompt=item["stem"],
                    title=(item.get("displayTitle") or item.get("title") or "日语记述")[:500], kind=item["kind"],
                    practice_label="原创辅助练习" if item["kind"] == "original" else "官方题写作练习",
                    images_required=bool(item.get("visualRequired") or item["kind"] == "official"))
            self.insert_event(db, event, "native_codex")
            return self.finish_request(db, request_id, digest, draft_id=event["draft_id"], version_id=request_id,
                                       parent_version_id=base_version_id)

    def save_writing_feedback(self, session_id, expected_revision, request_id, draft_id, source_version_id,
                              task_response, task_evidence_quote, task_comment, issues, base_feedback_id=None):
        from .store import identifier, stamp, today

        payload = dict(draft_id=draft_id, source_version_id=source_version_id, task_response=task_response,
            task_evidence_quote=task_evidence_quote, task_comment=task_comment, issues=issues, base_feedback_id=base_feedback_id)
        with self.connection(write=True) as db:
            try:
                digest, replay = self.begin_request(db, request_id, "save_writing_feedback", payload, expected_revision, session_id)
            except UnicodeError as exc:
                raise CoreError("invalid_field", "写作内容必须是有效的 Unicode 文字。") from exc
            if replay:
                return replay
            identifier(source_version_id, "source_version_id")
            if base_feedback_id is not None:
                identifier(base_feedback_id, "base_feedback_id")
            events = self.events(db)
            root = self._writing_root(events, draft_id)
            latest = self._writing_versions(events, draft_id)[-1]
            if source_version_id != latest["id"]:
                raise CoreError("writing_version_conflict", "原稿已有较新版本，不能把旧稿反馈作为本次反馈保存。")
            code, reason = self._writing_availability(events, self.items.get(root["itemId"]), root)
            if code:
                raise CoreError(code, reason)
            old = next((event for event in reversed(events) if event.get("type") == "writing_feedback"
                        and event.get("draft_id") == draft_id and event.get("source_version_id") == source_version_id), None)
            if base_feedback_id != (old["id"] if old else None):
                raise CoreError("writing_feedback_conflict", "本稿已有较新反馈，请先读取并核对。")
            if task_response not in ("answered", "partial", "off_topic", "unclear"):
                raise CoreError("invalid_field", "task_response 只支持 answered、partial、off_topic、unclear。")
            self._writing_text(task_comment, "task_comment", 1000)
            self._writing_text(task_evidence_quote, "task_evidence_quote", 1000, empty=True)
            if not isinstance(issues, list) or len(issues) > 2:
                raise CoreError("invalid_field", "每次只能保存零到两个反馈点。")
            if not latest["text"].strip():
                if task_response != "unclear" or task_evidence_quote != "" or issues:
                    raise CoreError("writing_empty", "空稿只能记录尚需本人作答，不能判定答题完成或编造引文。")
            elif not task_evidence_quote.strip() or task_evidence_quote not in latest["text"]:
                raise CoreError("writing_quote_mismatch", "审题反馈须引用来源版本中的逐字原文。")
            for issue in issues:
                if not isinstance(issue, dict) or set(issue) != {"category", "quote", "request"}:
                    raise CoreError("invalid_field", "反馈点只允许 category、quote、request。")
                if issue["category"] not in ("content", "structure", "language"):
                    raise CoreError("invalid_field", "反馈类别只支持 content、structure、language。")
                self._writing_text(issue["quote"], "quote", 1000)
                self._writing_text(issue["request"], "request", 1000)
                if issue["quote"] not in latest["text"]:
                    raise CoreError("writing_quote_mismatch", "反馈引文必须逐字出现在对应原稿版本中。")
            event = dict(id=request_id, type="writing_feedback", draft_id=draft_id,
                source_version_id=source_version_id, itemId=root["itemId"], itemVersion=root["itemVersion"],
                task_response=task_response, task_evidence_quote=task_evidence_quote, task_comment=task_comment,
                issues=issues, base_feedback_id=base_feedback_id, source="tutor_feedback",
                createdAt=stamp(), date=today(), sessionId=session_id)
            self.insert_event(db, event, "native_codex")
            return self.finish_request(db, request_id, digest, draft_id=draft_id, feedback_id=request_id,
                                       source_version_id=source_version_id)

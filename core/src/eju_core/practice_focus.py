"""Explicit temporary practice filters, independent of learner self-reports.

Only a current, reviewed item-to-focus binding may narrow a candidate query.
Reviewer rationales and source guidance never cross the public response boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import re

from .errors import CoreError


FOCUS_PATH = files("eju_core").joinpath("data/practice_focus.json")
MAX_FOCUS_BYTES = 2 * 1024 * 1024
FOCUS_SUBJECTS = frozenset({"japanese_reading", "integrated"})
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def guidance_sha256(guidance):
    """Hash the exact per-item guidance object, not its surrounding map."""
    raw = json.dumps(guidance, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _text(value, maximum):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _identifier(value):
    return isinstance(value, str) and ID_PATTERN.fullmatch(value) is not None


def _public_focus(definition):
    return {key: definition[key] for key in ("id", "label", "description")}


@dataclass(frozen=True)
class FocusSnapshot:
    version: str | None
    options: tuple
    selected: dict | None
    matching_items: frozenset

    def response_fields(self):
        return {"focus_options": [dict(option) for option in self.options],
                "focus_version": self.version,
                "selected_focus": dict(self.selected) if self.selected is not None else None}


def _read_definition():
    raw = FOCUS_PATH.read_bytes()
    if len(raw) > MAX_FOCUS_BYTES:
        raise ValueError("oversized focus definition")
    document = json.loads(raw, object_pairs_hook=_unique_object)
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int
            or document["schema_version"] != 1 or not isinstance(document.get("focuses"), list)
            or not isinstance(document.get("items"), dict)):
        raise ValueError("invalid focus schema")
    definitions = {}
    for definition in document["focuses"]:
        if (not isinstance(definition, dict) or not _identifier(definition.get("id"))
                or definition["id"] in definitions
                or definition.get("subject") not in FOCUS_SUBJECTS
                or not _text(definition.get("label"), 120)
                or not _text(definition.get("description"), 1000)):
            raise ValueError("invalid focus taxonomy")
        definitions[definition["id"]] = definition
    return hashlib.sha256(raw).hexdigest(), definitions, document["items"]


def _matches(row, item, guidance, definitions, focus_id, subject):
    """Bad/stale per-item records exclude that item; never guess a replacement."""
    if (not isinstance(row, dict) or row.get("reviewed") is not True
            or row.get("item_version") != item.get("version")
            or not _text(row.get("rationale"), 20000)
            or not isinstance(row.get("focus_ids"), list)
            or not row["focus_ids"] or not all(_identifier(value) for value in row["focus_ids"])
            or len(set(row["focus_ids"])) != len(row["focus_ids"])
            or focus_id not in row["focus_ids"]
            or any(value not in definitions or definitions[value]["subject"] != subject
                   for value in row["focus_ids"])
            or subject not in item.get("subjects", []) or item.get("kind") != "official"
            or not isinstance(row.get("basis"), dict)
            or not isinstance(guidance, dict) or guidance.get("reviewed") is not True
            or guidance.get("item_version") != item.get("version")):
        return False
    expected = row["basis"].get("guidance_sha256")
    if not isinstance(expected, str) or SHA_PATTERN.fullmatch(expected) is None:
        return False
    try:
        return expected == guidance_sha256(guidance)
    except (TypeError, ValueError, OverflowError, RecursionError):
        return False


def load_focus(core, subject, focus_id=None):
    """Read fresh metadata per query/transaction; no writes or answer disclosure."""
    try:
        version, definitions, rows = _read_definition()
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        if focus_id is not None:
            raise CoreError("focus_unavailable", "练习重点资料当前不可用，请重新读取；也可以明确取消重点后选择。") from None
        return FocusSnapshot(None, (), None, frozenset())
    options = tuple(_public_focus(row) for row in definitions.values() if row["subject"] == subject)
    if focus_id is None:
        return FocusSnapshot(version, options, None, frozenset())
    if not _identifier(focus_id) or focus_id not in definitions or definitions[focus_id]["subject"] != subject:
        raise CoreError("focus_unavailable", "当前科目没有这个练习重点，请重新选择或明确取消重点。")
    matches = frozenset(item_id for item_id, item in core.items.items()
                        if _matches(rows.get(item_id), item, core.coaching.get(item_id),
                                    definitions, focus_id, subject))
    return FocusSnapshot(version, options, _public_focus(definitions[focus_id]), matches)


def validate_preview_version(focus, focus_id, expected_focus_version):
    if focus_id is None:
        if expected_focus_version is not None:
            raise CoreError("focus_changed", "练习重点与预览不一致，请重新读取候选并确认。")
    elif expected_focus_version != focus.version:
        raise CoreError("focus_changed", "练习重点资料已变化或缺少预览版本，请重新读取候选并确认。")

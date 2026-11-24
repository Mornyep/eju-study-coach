"""Pure conservative FSRS adapter for independent factual recall.

The DB owns source provenance/eligibility and immutable attempt persistence.
Ratings describe recall difficulty, never EJU proficiency or exam scores.
No legacy stage/due conversion is provided.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from importlib.metadata import version
import json
import math

from fsrs import Card, Rating, Scheduler, State

ADAPTER_VERSION = "1.0.0"
FSRS_VERSION = "6.3.2"
SCHEDULER_VERSION = "fsrs6-defaults-no-fuzz-v1"
ELIGIBLE_KINDS = frozenset({"vocabulary", "definition", "formula_conditions"})
RATINGS = {name: getattr(Rating, name) for name in ("Again", "Hard", "Good", "Easy")}
_STATE_KEYS = {"schema_version", "adapter_version", "fsrs_version", "scheduler", "card_id", "created_at", "review_count", "card"}
_CARD_KEYS = {"card_id", "state", "step", "stability", "difficulty", "due", "last_review"}


class RecallError(ValueError):
    """Invalid adapter input; no scheduling or persistence has occurred."""


def _sdk_version():
    if version("fsrs") != FSRS_VERSION:
        raise RecallError(f"fsrs dependency must be {FSRS_VERSION}; state migration is not automatic")


def _utc(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        raise RecallError("clock must be an aware UTC datetime")
    return now.astimezone(timezone.utc)


def _timestamp(value, field):
    if not isinstance(value, str):
        raise RecallError(f"{field} must be an ISO UTC timestamp")
    try:
        result = _utc(datetime.fromisoformat(value))
    except (ValueError, TypeError) as exc:
        raise RecallError(f"{field} must be an ISO UTC timestamp") from exc
    if result.isoformat() != value:
        raise RecallError(f"{field} must use canonical ISO UTC (+00:00)")
    return result


def _canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RecallError("state/evidence must contain finite JSON values") from exc


def _sha(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _sdk_id(card_id):
    # Stable 48-bit integer also survives ordinary JSON/JavaScript number transport.
    # External string card_id remains the authoritative identity.
    return int.from_bytes(hashlib.sha256(card_id.encode()).digest()[:6], "big")


def _valid_id(card_id):
    if not isinstance(card_id, str) or not card_id or card_id != card_id.strip() or len(card_id) > 512:
        raise RecallError("card_id must be a nonempty trimmed string of at most 512 characters")


def _settings():
    return Scheduler(enable_fuzzing=False).to_dict()


def is_eligible_kind(kind):
    """True only for independently recallable factual card kinds."""
    return isinstance(kind, str) and kind in ELIGIBLE_KINDS


def validate_state(state):
    """Validate this exact version; return a detached JSON-compatible state.

    Unknown versions/settings/fields and malformed SDK card payloads raise
    RecallError. There is intentionally no legacy state inference or migration.
    """
    _sdk_version()
    if not isinstance(state, dict) or set(state) != _STATE_KEYS:
        raise RecallError("invalid recall state schema; legacy states are unsupported")
    _canonical(state)
    if (type(state["schema_version"]) is not int or state["schema_version"] != 1
            or state["adapter_version"] != ADAPTER_VERSION or state["fsrs_version"] != FSRS_VERSION):
        raise RecallError("unknown recall adapter/schema/fsrs version; explicit migration required")
    _valid_id(state["card_id"])
    expected_scheduler = {"version": SCHEDULER_VERSION, "settings": _settings()}
    if _canonical(state["scheduler"]) != _canonical(expected_scheduler):
        raise RecallError("unknown or modified scheduler version/settings")
    count = state["review_count"]
    if type(count) is not int or count < 0:
        raise RecallError("review_count must be a nonnegative integer")
    created = _timestamp(state["created_at"], "created_at")
    card = state["card"]
    if not isinstance(card, dict) or set(card) != _CARD_KEYS:
        raise RecallError("invalid FSRS card schema")
    if type(card["card_id"]) is not int or card["card_id"] != _sdk_id(state["card_id"]):
        raise RecallError("FSRS card identity mismatch")
    if type(card["state"]) is not int or card["state"] not in (1, 2, 3):
        raise RecallError("unknown FSRS card phase")
    step = card["step"]
    if card["state"] == State.Review.value:
        if step is not None:
            raise RecallError("Review phase must have no learning step")
    else:
        steps = state["scheduler"]["settings"]["learning_steps" if card["state"] == State.Learning.value else "relearning_steps"]
        if type(step) is not int or not 0 <= step < len(steps):
            raise RecallError("invalid FSRS learning/relearning step")
    for name in ("stability", "difficulty"):
        number = card[name]
        if number is not None:
            try:
                finite = type(number) in (int, float) and math.isfinite(number)
            except OverflowError:
                finite = False
            if not finite:
                raise RecallError(f"{name} must be finite numeric or null")
            if (name == "stability" and number < .001) or (name == "difficulty" and not 1 <= number <= 10):
                raise RecallError(f"{name} is outside the FSRS range")
    due = _timestamp(card["due"], "card.due")
    if count == 0:
        expected_card = Card(card_id=_sdk_id(state["card_id"]), due=created).to_dict()
        if _canonical(card) != _canonical(expected_card):
            raise RecallError("unreviewed state must match its deterministic initial card")
    else:
        reviewed = _timestamp(card["last_review"], "card.last_review")
        if reviewed < created or due < reviewed or card["stability"] is None or card["difficulty"] is None:
            raise RecallError("reviewed card has inconsistent times or missing FSRS parameters")
    try:
        restored = Card.from_dict(card)
        Scheduler.from_dict(state["scheduler"]["settings"])
        if _canonical(restored.to_dict()) != _canonical(card):
            raise RecallError("FSRS card cannot round-trip without coercion")
    except (TypeError, ValueError, KeyError, OverflowError) as exc:
        raise RecallError(f"malformed FSRS state: {exc}") from exc
    return deepcopy(state)


def new_state(card_id: str, now: datetime) -> dict:
    """Create only a new FSRS card, with injected identity and UTC clock."""
    _sdk_version()
    _valid_id(card_id)
    now = _utc(now)
    return {"schema_version": 1, "adapter_version": ADAPTER_VERSION, "fsrs_version": FSRS_VERSION,
            "scheduler": {"version": SCHEDULER_VERSION, "settings": _settings()},
            "card_id": card_id, "created_at": now.isoformat(), "review_count": 0,
            "card": Card(card_id=_sdk_id(card_id), due=now).to_dict()}


def _suppression_reason(evidence):
    if not isinstance(evidence, dict):
        return "missing_or_invalid_evidence"
    if not is_eligible_kind(evidence.get("kind")):
        return "ineligible_kind"
    hint = evidence.get("hint")
    if hint not in ("H0", "H1", "H2", "H3", "H4"):
        return "hint_unknown"
    if hint != "H0":
        return "hint_used"
    if type(evidence.get("answer_seen_before")) is not bool:
        return "answer_exposure_unknown"
    if evidence["answer_seen_before"]:
        return "answer_seen_before"
    if evidence.get("grading") != "verified":
        return "grading_unverified"
    if type(evidence.get("guessed")) is not bool:
        return "guessing_unknown"
    if evidence["guessed"]:
        return "guessed"
    return None


def apply_review(state: dict, rating: str, now: datetime, evidence: dict) -> dict:
    """Apply an eligible independent review or emit a suppressed attempt log.

    Only H0, unseen answer, verified grading and no guessing may advance.
    Again is permitted for verified independent failed recall. Suppression
    preserves the entire state; attempt order is persisted by the caller.
    Time may equal but never precede creation or the last applied review.
    """
    state = validate_state(state)
    now = _utc(now)
    if not isinstance(rating, str) or rating not in RATINGS:
        raise RecallError("rating must be Again, Hard, Good or Easy (recall difficulty)")
    lower_bound = _timestamp(state["card"]["last_review"] or state["created_at"], "review lower bound")
    if now < lower_bound:
        raise RecallError("review time must not precede creation or last applied review")
    _canonical(evidence)
    reason = _suppression_reason(evidence)
    log = {"schema_version": 1, "adapter_version": ADAPTER_VERSION, "fsrs_version": FSRS_VERSION,
           "card_id": state["card_id"], "review_datetime": now.isoformat(), "rating": rating,
           "rating_scope": "recall_difficulty", "evidence": deepcopy(evidence),
           "state_before_sha256": _sha(state), "applied": reason is None,
           "reason": reason or "applied", "fsrs_log": None}
    if reason is not None:
        log["state_after_sha256"] = log["state_before_sha256"]
        return {"state": state, "log": log, "applied": False, "reason": reason}
    scheduler = Scheduler.from_dict(state["scheduler"]["settings"])
    card, fsrs_log = scheduler.review_card(Card.from_dict(state["card"]), RATINGS[rating], review_datetime=now)
    state["card"] = card.to_dict()
    state["review_count"] += 1
    validate_state(state)
    log["fsrs_log"] = fsrs_log.to_dict()
    log["state_after_sha256"] = _sha(state)
    return {"state": state, "log": log, "applied": True, "reason": "applied"}

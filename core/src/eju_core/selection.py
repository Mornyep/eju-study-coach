"""Keep component drills distinct from reviewed exam-style practice.

This structural gate does not certify item quality. The recorded source review,
subject reasoning and option checks still require actual human/coach review.
"""
from __future__ import annotations


def alignment_complete(item: dict) -> bool:
    review = item.get("examAlignment")
    if not isinstance(review, dict) or review.get("status") != "reviewed":
        return False
    if review.get("format") != "four_choice_single" or not review.get("knowledgeTested"):
        return False
    if not item.get("stem") or item.get("grading", {}).get("mode") != "choice":
        return False
    anchors = review.get("patternAnchors", [])
    if not anchors or not all(isinstance(a, dict) and a.get("source") and a.get("question") and a.get("observedMechanism") for a in anchors):
        return False
    options = review.get("optionReview", [])
    if len(options) != 4 or {str(o.get("option")) for o in options} != {"1", "2", "3", "4"}:
        return False
    if sum(o.get("correct") is True for o in options) != 1:
        return False
    for option in options:
        if not option.get("reason") or type(option.get("correct")) is not bool:
            return False
        if not option["correct"] and not option.get("misconception"):
            return False
        if option["correct"] and str(option["option"]) != str(item["grading"].get("value")):
            return False
    return bool(review.get("independentCheck") and review.get("limitations"))


def practice_scope(item: dict) -> str:
    if item.get("kind") == "official":
        return "official_material"
    if item.get("practicePurpose") == "component_skill":
        return "component_skill"
    if alignment_complete(item):
        return "exam_style_original"
    return "original_alignment_unreviewed"


def integrated_eligible(item: dict, purpose: str) -> bool:
    scope = practice_scope(item)
    return scope == ("component_skill" if purpose == "prerequisite" else "exam_style_original")

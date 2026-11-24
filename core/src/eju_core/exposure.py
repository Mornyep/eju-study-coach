"""Conservative exposure identity for complete official pages and passages.

Importing/reading a page internally is not an exposure. Callers pass only items
linked to actual recorded exposure/attempt events. No event or catalog is edited.
"""


def official_pages(item):
    delivery = item.get("delivery") or {}
    source = delivery.get("source_sha256")
    if item.get("kind") != "official" or delivery.get("format") != "official_pages" or not source:
        return set()
    return {(source, asset["pdf_page"], asset["sha256"])
            for asset in delivery.get("assets", [])
            if asset.get("pdf_page") and asset.get("sha256")}


def shares_exposure(item, other):
    if not item or not other:
        return False
    if item.get("id") and item.get("id") == other.get("id"):
        return True
    group = item.get("exposureGroupId")
    if group and group == other.get("exposureGroupId"):
        return True
    return bool(official_pages(item) & official_pages(other))

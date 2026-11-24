"""Integrity checked question pictures; no OCR, network or learner-state writes."""
from pathlib import Path, PurePosixPath
import hashlib
import struct
from .errors import CoreError
from .safe_files import is_link, regular_file, portable_relative

MEDIA_ROOT = Path(__file__).parent / "data/media"
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def assets(item, root=None):
    delivery = item.get("delivery") or {}
    required = item.get("visualRequired", False) or item.get("kind") == "official"
    rows = delivery.get("assets", [])
    if not rows and not required:
        return []
    if delivery.get("review") != "complete_clean" or not isinstance(rows, list) or not 1 <= len(rows) <= 5:
        raise CoreError("question_image_missing", "题目需要完整、已核对的图片，目前不能发题。")
    if not all(isinstance(row, dict) for row in rows):
        raise CoreError("question_image_invalid", "图片记录必须是对象。")
    official = item.get("kind") == "official"
    expected = "official_pages" if official else "authored_figure"
    if delivery.get("format") != expected:
        raise CoreError("question_image_invalid", "原页与原创配图的来源类型不匹配。")
    if official and (not delivery.get("source_url") or not delivery.get("source_sha256")
                     or [x.get("pdf_page") for x in rows] != item.get("pages")):
        raise CoreError("question_image_invalid", "原页来源或完整页码记录不匹配。")
    root = Path(root or MEDIA_ROOT).resolve()
    result = []
    for index, row in enumerate(rows):
        rel = row.get("path", "")
        if not isinstance(rel, str) or not rel or PurePosixPath(rel).is_absolute() or ".." in PurePosixPath(rel).parts or "\\" in rel:
            raise CoreError("unsafe_path", "图片路径不合法。")
        portable_relative(rel)
        path = root / rel
        if any(is_link(p) for p in [path, *path.parents] if p != root and p.is_relative_to(root)):
            raise CoreError("unsafe_path", "图片不能使用符号链接。")
        if not path.resolve().is_relative_to(root) or not path.is_file():
            raise CoreError("question_image_missing", "题目图片不存在；不能省略图片继续发题。")
        if not 24 <= path.stat().st_size <= MAX_IMAGE_BYTES:
            raise CoreError("question_image_invalid", "题目图片大小超出支持范围。")
        regular_file(path, MAX_IMAGE_BYTES)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row.get("sha256") or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise CoreError("question_image_invalid", "题目图片与已核对版本不一致。")
        width, height = struct.unpack(">II", data[16:24])
        if (width, height) != (row.get("width"), row.get("height")) or not 1 <= width * height <= 16_000_000:
            raise CoreError("question_image_invalid", "题目图片尺寸记录不一致。")
        result.append({"index": index, "path": str(path), "sha256": row["sha256"],
                       "width": width, "height": height, "pdf_page": row.get("pdf_page"),
                       "alt": row.get("alt", "题目完整图片"), "media_type": "image/png"})
    return result


def ready(item, root=None):
    try:
        images = assets(item, root)
        return bool(images) if item.get("kind") == "official" else bool(item.get("stem"))
    except (CoreError, OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


class QuestionMediaMixin:
    def read_question_images(self, session_id, item_id):
        with self.connection() as db:
            self.check_session(db, session_id)
            item = self.items.get(item_id)
            if not item:
                raise CoreError("item_missing", "题目未登记。")
            images = assets(item)
            return {"ok": True, "item_id": item_id, "item_version": item["version"],
                    "kind": item["kind"], "images": images, "source": item.get("sourcePath"),
                    "source_url": (item.get("delivery") or {}).get("source_url"),
                    "learner_exposure_recorded": False,
                    "instruction": "实际查看所有图片并在学习者消息中展示；内部读取图片不等于学生已看过。不要只给文件链接、图片占位符或凭文字重绘原图。"}

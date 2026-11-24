"""Portable local-file boundaries. No account or global permission changes."""
from contextlib import contextmanager
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat

from .errors import CoreError


def is_link(path):
    """Windows junctions/reparse points are links even when is_symlink is false."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def portable_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\x00" in value:
        raise CoreError("unsafe_path", "只接受可移植的相对文件路径。")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive:
        raise CoreError("unsafe_path", "文件路径不能是绝对路径。")
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}
    if any(p in ("", ".", "..") or p.rstrip(". ") != p or p.split(".")[0].upper() in reserved
           or any(ord(c) < 32 or c in '<>"|?*' for c in p) for p in parts):
        raise CoreError("unsafe_path", "文件名含越界或不支持的部分。")
    return Path(*parts)


def regular_file(path, maximum=None):
    path = Path(path)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise CoreError("material_missing", "所需本地文件不存在。") from exc
    if is_link(path) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise CoreError("unsafe_path", "文件不能是链接或特殊设备。")
    if maximum is not None and info.st_size > maximum:
        raise CoreError("too_large", "本地文件超过本版容量。")
    return info


def private_mode(path, directory=False):
    # Windows chmod is not an ACL. The installer creates the vault inside the
    # user's private profile; documentation states this inherited-ACL boundary.
    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)


@contextmanager
def private_text_create(path):
    """Create with private mode before any credential or learner bytes exist."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        yield handle
        handle.flush()
        os.fsync(handle.fileno())

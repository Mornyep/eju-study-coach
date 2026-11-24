"""Passphrase-encrypted, authenticated snapshots; no password persistence or network.

Format 2 fixes the KDF cost instead of trusting parameters from an input file:
8-byte magic/version, 16-byte random salt, 12-byte random nonce, AES-256-GCM
ciphertext with its 16-byte authentication tag. The entire header is AAD.
The plaintext is a bounded ZIP_STORED archive of a normal verified Core backup.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
import secrets
import stat
import tempfile
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .errors import CoreError
from .safe_files import is_link, portable_relative, regular_file

MAGIC = b'EJUBAK\x00\x02'
HEADER_SIZE = len(MAGIC) + 16 + 12
MAX_PLAINTEXT = 128 * 1024 * 1024
MAX_FILES = 10002


def new_path(value, *, outside=None):
    path = Path(value).expanduser().absolute()
    if any(c in str(path) for c in '\r\n\x00'):
        raise CoreError('unsafe_path', '路径不能含换行或 NUL。')
    if any(is_link(p) for p in (path, *path.parents)):
        raise CoreError('unsafe_path', '备份和恢复路径不能经过链接或 junction。')
    if path.exists():
        raise CoreError('destination_exists', '请使用一个尚不存在的新目标，已有文件不会覆盖。')
    if outside is not None and path.resolve().is_relative_to(Path(outside).resolve()):
        raise CoreError('unsafe_path', '备份应保存在私人资料库之外。')
    return path


def derive(passphrase: str, salt: bytes) -> bytes:
    if not isinstance(passphrase, str) or len(passphrase) < 12:
        raise CoreError('passphrase_short', '请使用至少 12 个字符的独立备份口令，并妥善保存。')
    password = passphrase.encode('utf-8')
    if len(password) > 1024:
        raise CoreError('passphrase_long', '口令超过本版长度限制。')
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password)


def _publish(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix='.eju-encrypted-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # Publish a complete file exclusively; never replace an existing target.
        os.link(temp, path)
    except FileExistsError as exc:
        raise CoreError('destination_exists', '目标已出现，未覆盖。') from exc
    finally:
        Path(temp).unlink(missing_ok=True)


def seal_backup(core, destination, passphrase):
    target = new_path(destination, outside=core.root)
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    key = derive(passphrase, salt)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Restrict temporary plaintext to a private directory and always clean it.
    with tempfile.TemporaryDirectory(prefix='.eju-seal-', dir=target.parent) as temp:
        snapshot = Path(temp) / 'snapshot'
        result = core.backup(snapshot)
        files = sorted(p for p in snapshot.rglob('*') if p.is_file())
        if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_PLAINTEXT - 4 * 1024 * 1024:
            raise CoreError('backup_too_large', '加密备份限 128 MiB；本次未生成。较大的资料库可继续使用普通备份。')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_STORED) as archive:
            for file in files:
                regular_file(file, MAX_PLAINTEXT)
                archive.write(file, file.relative_to(snapshot).as_posix())
        plaintext = stream.getvalue()
        if len(plaintext) > MAX_PLAINTEXT:
            raise CoreError('backup_too_large', '加密备份超过 128 MiB，未生成目标。')
        header = MAGIC + salt + nonce
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, header)
        _publish(target, header + ciphertext)
    return {'ok': True, 'backup_complete': True, 'encrypted': True, 'path': str(target),
            'revision': result['revision'], 'files': result['files'],
            'format': 'eju-encrypted-backup-v2',
            'message': '加密备份已保存。口令不会保存；遗失后无法恢复，请另存口令。'}


def restore_sealed(source, destination, passphrase):
    from .store import Core
    target = new_path(destination)
    source = Path(source).expanduser().absolute()
    if any(is_link(p) for p in (source, *source.parents)):
        raise CoreError('unsafe_path', '备份源路径不能经过链接或 junction。')
    regular_file(source, MAX_PLAINTEXT + HEADER_SIZE + 16)
    data = source.read_bytes()
    if len(data) < HEADER_SIZE + 16 or data[:len(MAGIC)] != MAGIC:
        raise CoreError('encrypted_backup_invalid', '不支持或不完整的加密备份格式。')
    header = data[:HEADER_SIZE]
    salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
    key = derive(passphrase, salt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, data[HEADER_SIZE:], header)
    except InvalidTag as exc:
        raise CoreError('backup_authentication_failed', '口令不正确或备份已损坏，未恢复任何资料。') from exc
    # Authenticate before parsing any archive contents or creating target paths.
    try:
        with zipfile.ZipFile(io.BytesIO(plaintext)) as archive:
            entries = archive.infolist()
            if not 2 <= len(entries) <= MAX_FILES:
                raise ValueError('file count')
            names, total = set(), 0
            for entry in entries:
                part = portable_relative(entry.filename)
                if (entry.filename in names or entry.is_dir() or entry.flag_bits & 1
                        or entry.compress_type != zipfile.ZIP_STORED
                        or entry.compress_size != entry.file_size
                        or stat.S_IFMT(entry.external_attr >> 16) not in (0, stat.S_IFREG)
                        or (entry.filename not in ('manifest.json', 'learning.sqlite')
                            and (len(part.parts) != 2 or part.parts[0] != 'originals'))):
                    raise ValueError('unsafe archive')
                names.add(entry.filename)
                total += entry.file_size
            if not {'manifest.json', 'learning.sqlite'} <= names or total > MAX_PLAINTEXT:
                raise ValueError('archive content')
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.eju-unseal-', dir=target.parent) as temp:
                snapshot = Path(temp)
                for entry in entries:
                    out = snapshot / portable_relative(entry.filename)
                    out.parent.mkdir(mode=0o700, exist_ok=True)
                    descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, 'wb') as handle:
                        handle.write(archive.read(entry))
                result = Core.restore(snapshot, target)
    except (zipfile.BadZipFile, RuntimeError, ValueError) as exc:
        raise CoreError('encrypted_backup_invalid', '备份内容不符合格式，未启用恢复库。') from exc
    result.update(encrypted_source=True, current_vault_changed=False)
    return result


def prompt_passphrase(confirm=False):
    import getpass
    import hmac
    import sys
    import warnings
    if not sys.stdin.isatty():
        raise CoreError('interactive_terminal_required', '请在本机交互终端输入口令；不接受命令参数、环境变量或管道传入口令。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            first = getpass.getpass('备份口令（不显示）：')
            second = getpass.getpass('再次输入口令：') if confirm else None
        if confirm:
            if not hmac.compare_digest(first.encode('utf-8'), second.encode('utf-8')):
                raise CoreError('passphrase_mismatch', '两次口令不一致，未生成备份。')
        return first
    except getpass.GetPassWarning as exc:
        raise CoreError('hidden_input_unavailable', '当前终端不能隐藏口令输入，已停止。请使用本机交互终端。') from exc
    except (EOFError, KeyboardInterrupt) as exc:
        raise CoreError('cancelled', '已取消，未继续备份或恢复。') from exc

from __future__ import annotations

import argparse
import json
import sys
import sqlite3
from pathlib import Path

from . import __version__
from .errors import CoreError
from .safe_files import private_text_create
from .store import Core, sha


def main(argv=None):
    parser = argparse.ArgumentParser(description="Codex EJU local learning core")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--vault", help="Explicit private learning vault; never inferred from current directory")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create a new empty private vault")
    init.add_argument("path")
    commands.add_parser("serve", help="Serve MCP over STDIO")
    web = commands.add_parser("workbench", help="Serve authenticated local workbench")
    web.add_argument("--port", type=int, default=0)
    commands.add_parser("status", help="Read bounded recovery status (creates a local session token)")
    commands.add_parser("doctor", help="Inspect vault and verify registered attachments")
    migrate = commands.add_parser("import-v5", help="Preview a v5 JSON backup; --apply commits")
    migrate.add_argument("file")
    migrate.add_argument("--apply", action="store_true")
    migrate.add_argument("--expected-revision", type=int, default=0)
    migrate.add_argument("--request-id")
    backup = commands.add_parser("backup", help="Consistent unencrypted local backup to a new directory")
    backup.add_argument("destination")
    sealed = commands.add_parser("backup-encrypted", help="Passphrase-encrypted backup to a new file; local terminal prompt only")
    sealed.add_argument("destination")
    unseal = commands.add_parser("restore-encrypted", help="Authenticate encrypted backup and restore into a new directory")
    unseal.add_argument("backup")
    unseal.add_argument("destination")
    restore = commands.add_parser("restore", help="Verify and restore a backup into a new directory")
    restore.add_argument("backup")
    restore.add_argument("destination")
    export = commands.add_parser("export", help="Export structured records without binary media")
    export.add_argument("destination")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = Core.initialize(args.path)
        elif args.command == "restore-encrypted":
            from .sealed_backup import restore_sealed, prompt_passphrase
            result = restore_sealed(args.backup, args.destination, prompt_passphrase())
        elif args.command == "restore":
            result = Core.restore(args.backup, args.destination)
        else:
            if not args.vault:
                parser.error("this command requires --vault; no default private directory is assumed")
            if args.command == "serve":
                from .server import build_server
                build_server(args.vault).run(transport="stdio")
                return 0
            core = Core(args.vault)
            if args.command == "workbench":
                from .http_server import serve
                serve(core, args.port)
                return 0
            if args.command == "status":
                result = core.resume_learning()
            elif args.command == "doctor":
                problems = []
                with core.connection() as db:
                    for row in db.execute("SELECT * FROM artifacts"):
                        try:
                            core.verify_artifact(row)
                        except CoreError as exc:
                            problems.append({"resource_id": row["id"], **exc.result()["error"]})
                    registered = {row["relative_path"] for row in db.execute("SELECT relative_path FROM artifacts")}
                    originals = core.root / "originals"
                    unregistered = [p.name for p in originals.iterdir()
                                    if ("originals/" + p.name) not in registered] if originals.is_dir() else []
                    from .question_media import ready
                    official=[x for x in core.items.values() if x.get("kind")=="official"]
                    result = {"official_registered":len(official), "official_available":sum(ready(x) for x in official),
                              "official_import_help":"完整原页未安装时，按随包 README 从指定官方 PDF 本地导入；不会自动下载或退回原创。",
                              "optional_ocr_configured":all(__import__("os").environ.get(k) for k in ("EJU_DOCLING_PYTHON","EJU_DOCLING_RUNNER","EJU_DOCLING_MODELS")),
                              "ok": not problems, "version": __version__, "revision": core.meta(db, "revision"),
                              "schema_version": core.meta(db, "schema_version"), "artifact_problems": problems,
                              "unregistered_originals": unregistered,
                              "meaning": "本地库检查；不代表 Codex 桌面端已接入或模型已调用。"}
            elif args.command == "import-v5":
                file = Path(args.file)
                if file.stat().st_size > 12_000_000:
                    raise CoreError("too_large", "备份超过 12 MB。")
                raw = file.read_text(encoding="utf-8")
                result = core.import_v5(raw, args.expected_revision,
                                        args.request_id or ("import:" + sha(raw.encode())), args.apply)
            elif args.command == "backup-encrypted":
                from .sealed_backup import seal_backup, prompt_passphrase
                result = seal_backup(core, args.destination, prompt_passphrase(confirm=True))
            elif args.command == "backup":
                result = core.backup(args.destination)
            elif args.command == "export":
                output = Path(args.destination)
                with private_text_create(output) as f:
                    json.dump(core.export_json(), f, ensure_ascii=False, indent=2)
                    f.write("\n")
                result = {"ok": True, "exported": str(output), "binary_media_included": False}
            else:
                raise CoreError("unsupported", "不支持此命令。")
    except CoreError as exc:
        result = exc.result()
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        result = CoreError("operation_failed", "本地操作未完成。请检查目标、文件格式和读写条件；未确认成功。").result()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())


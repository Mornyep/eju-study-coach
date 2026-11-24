"""Launch this private installation using argv lists, never shell commands."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def runtime_python(root=HERE, platform=None):
    return Path(root) / '.venv' / ('Scripts/python.exe' if (platform or os.name) == 'nt' else 'bin/python')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['workbench', 'doctor', 'status', 'serve', 'backup', 'export', 'restore', 'import-official', 'backup-encrypted', 'restore-encrypted'])
    parser.add_argument('destination', nargs='?')
    parser.add_argument('new_destination', nargs='?')
    args = parser.parse_args(argv)
    try:
        state = json.loads((HERE / 'installation.json').read_text(encoding='utf-8'))
        if state['stage'] != 'complete':
            raise ValueError('Installation is incomplete; inspect installation.json and INSTALL.md.')
        command = [str(runtime_python(HERE)), str(HERE / 'core/run.py'), '--vault', state['vault'], args.command]
        if args.command == 'import-official':
            if not args.destination or args.new_destination:
                parser.error('import-official requires exactly one local source PDF')
            command = [str(runtime_python(HERE)), str(HERE / 'import_official.py'), '--source', str(Path(args.destination).expanduser().resolve())]
        elif args.command in ('restore', 'restore-encrypted'):
            if not args.destination or not args.new_destination:
                parser.error('restore requires BACKUP NEW_DEST')
            command = [str(runtime_python(HERE)), str(HERE / 'core/run.py'), args.command, args.destination, args.new_destination]
        elif args.new_destination:
            parser.error('only restore accepts a second path')
        elif args.command in ('backup', 'export', 'backup-encrypted'):
            if not args.destination:
                parser.error('backup/export requires a new destination path')
            command.append(args.destination)
        elif args.destination:
            parser.error('this command does not accept a destination')
        return subprocess.call(command)
    except (OSError, ValueError, KeyError) as exc:
        print(f'Launch failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

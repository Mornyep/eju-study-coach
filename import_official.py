"""Render supported official pages from the user's own PDF. No network access."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'core/src'))
from eju_core.errors import CoreError
from eju_core.safe_files import is_link, regular_file, portable_relative, private_mode


def import_pdf(source):
    from eju_core.official_import import import_pdf_bytes, MAX_PDF_BYTES
    source=Path(source).expanduser().absolute()
    regular_file(source,MAX_PDF_BYTES)
    return import_pdf_bytes(source.read_bytes())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',required=True);args=parser.parse_args(argv)
    try:result=import_pdf(args.source)
    except CoreError as exc:result=exc.result()
    except (OSError,ValueError,TypeError,ImportError,KeyError,IndexError):result=CoreError('import_failed','本地原页导入未完成。请检查文件、依赖和读写条件；原件未修改。').result()
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result.get('ok') else 1


if __name__=='__main__':raise SystemExit(main())

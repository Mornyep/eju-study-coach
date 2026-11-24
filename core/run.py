"""Source checkout launcher; no global Python or Codex configuration changes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from eju_core.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

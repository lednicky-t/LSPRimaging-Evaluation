from __future__ import annotations

from pathlib import Path
import sys


THIS_DIR = Path(__file__).resolve().parent
APPS_DIR = THIS_DIR.parent.parent

if str(APPS_DIR) not in sys.path:
    sys.path.insert(0, str(APPS_DIR))

from run_bootstrap import bootstrap_app_environment  # noqa: E402 - must follow the sys.path bootstrap above


bootstrap_app_environment("apps/LSPRi/eva/src")

if __name__ == "__main__":
    from lspr_imaging_app.app import main
    main()

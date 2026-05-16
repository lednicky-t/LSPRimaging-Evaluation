from __future__ import annotations

from lspr_imaging_app.format_versions import WORKSPACE_SCHEMA_VERSION

APP_NAME = "LSPR Imaging"
APP_VERSION = "0.1.0"
SCHEMA_VERSION = WORKSPACE_SCHEMA_VERSION


def version_string() -> str:
    return f"{APP_NAME} {APP_VERSION}"

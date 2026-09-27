"""The per-dataset session manifest - which named sessions exist, and which
one is active (2026-09-26).

A *session* (`storage/session.py`) is one complete, independently-
reproducible working copy of everything derived from a dataset: ROI table,
masks, geometry/background/chromatic/mask settings, and its own analysis
results (`analysis/data.h5`). Two sessions over the same dataset never share
a file - that is the point of this module. This is a genuinely new concept,
not a port: the stable app's own "sessions" only forked
`processing_profile.json`/`preprocessing.json`, never the ROI table or the
measurement backup, which stayed one shared file across every "session"
regardless (see `docs/rewrite_build_log_2026-09.md`, the 2026-09-26 entry
this module was built for).

**Layout**, under a dataset's own `home` folder, beside where `analysis/`
used to sit directly (that folder now lives one level deeper, per session)::

    <home>/sessions/index.json
    <home>/sessions/2026-09-26_143012/session.json
    <home>/sessions/2026-09-26_143012/masks/...
    <home>/sessions/2026-09-26_143012/analysis/data.h5

**Session ids are creation timestamps** (`YYYY-MM-DD_HHMMSS`), not a hash or
a bare counter - sortable in a plain folder listing and, on their own,
already tell a human when that session was started. Same "readable over
hash" precedent this app already set for mask/chromatic version numbers
(`analysis/provenance.py`).

**Not built here, deliberately**: rename, duplicate-as-new, and delete.
`SessionRecord.label` exists so a rename has somewhere to land later without
a schema change, but no UI or method sets it yet - this pass is
create + switch only, per the maintainer's explicit scope decision
(2026-09-26). Cloning a session's settings into a new one was also declined
(new sessions always start blank) - don't add a "duplicate" path without
checking that decision again first.

**No importer for the stable app's pre-existing `analysis/roi_table.json`/
`measurement_backup.h5`/`processing_profile.json`** (which stay exactly
where the stable app left them, directly under `home`, untouched). Opening
such a dataset here for the first time creates a fresh first session with
nothing in it - deliberate, not an oversight (see this module's build-log
entry for the maintainer's reasoning): those files aren't raw data, so
nothing is destroyed by leaving them alone, but they also aren't
automatically translated into this app's very different per-module
`SessionState` shape.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from .session import write_json_file

logger = logging.getLogger(__name__)

SESSION_INDEX_SCHEMA_NAME = "lspri_rewrite_session_index"
SESSION_INDEX_SCHEMA_VERSION = "1.0"

_ID_TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    label: str | None = None
    created_at_utc: str = ""


@dataclass
class SessionIndex:
    sessions: tuple[SessionRecord, ...] = field(default_factory=tuple)
    active_session_id: str | None = None

    def get(self, session_id: str) -> SessionRecord | None:
        for record in self.sessions:
            if record.session_id == session_id:
                return record
        return None


def sessions_root(home: Path) -> Path:
    return Path(home) / "sessions"


def index_path(home: Path) -> Path:
    return sessions_root(home) / "index.json"


def session_dir_for(home: Path, session_id: str) -> Path:
    return sessions_root(home) / session_id


def _encode(index: SessionIndex) -> dict:
    return {
        "schema_name": SESSION_INDEX_SCHEMA_NAME,
        "schema_version": SESSION_INDEX_SCHEMA_VERSION,
        "active_session_id": index.active_session_id,
        "sessions": [
            {"session_id": r.session_id, "label": r.label, "created_at_utc": r.created_at_utc}
            for r in index.sessions
        ],
    }


def _decode_records(raw: object) -> tuple[SessionRecord, ...]:
    if not isinstance(raw, list):
        return ()
    known = {f.name for f in fields(SessionRecord)}
    decoded: list[SessionRecord] = []
    for entry in raw:
        if not isinstance(entry, dict) or "session_id" not in entry:
            logger.warning("Skipping an undecodable session record in the session index")
            continue
        kwargs = {key: value for key, value in entry.items() if key in known}
        decoded.append(SessionRecord(**kwargs))
    return tuple(decoded)


def load_session_index(home: Path) -> SessionIndex:
    """Read `home/sessions/index.json`, or an empty index if there isn't one.

    A missing file is the ordinary "this dataset has never used sessions
    yet" case - same contract as `session.load_session`'s `None`, not an
    error. A file that is unreadable, isn't this schema, or is a future
    major version raises, for the same reason `load_session` does: guessing
    at an index this build doesn't understand risks creating a second
    "first" session next to ones it couldn't see."""
    path = index_path(Path(home))
    if not path.is_file():
        return SessionIndex()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a session index file (expected a JSON object)")
    name = payload.get("schema_name")
    if name != SESSION_INDEX_SCHEMA_NAME:
        raise ValueError(f"{path} has schema_name {name!r}, expected {SESSION_INDEX_SCHEMA_NAME!r}")
    version = str(payload.get("schema_version", "0.0"))
    if version.split(".")[0] != SESSION_INDEX_SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"{path} is schema version {version}, which this build "
            f"(supporting {SESSION_INDEX_SCHEMA_VERSION}) cannot read"
        )
    return SessionIndex(
        sessions=_decode_records(payload.get("sessions")),
        active_session_id=payload.get("active_session_id"),
    )


def save_session_index(home: Path, index: SessionIndex) -> Path:
    path = index_path(Path(home))
    write_json_file(path, _encode(index))
    return path


def _unique_session_id(existing: tuple[SessionRecord, ...]) -> str:
    """A creation-timestamp id, deduplicated against every id that already
    exists - two "New Session" clicks inside the same second (a fast
    double-click is entirely realistic) must not collide. Same defensive
    spirit as `RoiToolbox.restore_state`'s id-counter resume: silently
    reusing an id would mean the second session's first save quietly
    overwrites the first one's folder."""
    existing_ids = {record.session_id for record in existing}
    base = datetime.now(timezone.utc).strftime(_ID_TIMESTAMP_FORMAT)
    if base not in existing_ids:
        return base
    suffix = 2
    while f"{base}_{suffix}" in existing_ids:
        suffix += 1
    return f"{base}_{suffix}"


def create_session(home: Path, label: str | None = None) -> SessionRecord:
    """Create a new, blank session for `home` and make it the active one.

    "Blank" costs nothing to arrange here: nothing pre-populates the new
    session's folder, so `session.load_session` on it later returns `None`
    (ordinary fresh-dataset case) and `AnalysisEngine` starts with zero
    stored cells - the existing "missing file/folder means defaults"
    contracts already do the rest."""
    home = Path(home)
    index = load_session_index(home)
    record = SessionRecord(
        session_id=_unique_session_id(index.sessions),
        label=label,
        created_at_utc=datetime.now(timezone.utc).isoformat(),
    )
    index = SessionIndex(sessions=index.sessions + (record,), active_session_id=record.session_id)
    save_session_index(home, index)
    return record


def set_active_session(home: Path, session_id: str) -> None:
    home = Path(home)
    index = load_session_index(home)
    if index.get(session_id) is None:
        raise ValueError(f"No session {session_id!r} exists for {home}")
    save_session_index(home, SessionIndex(sessions=index.sessions, active_session_id=session_id))

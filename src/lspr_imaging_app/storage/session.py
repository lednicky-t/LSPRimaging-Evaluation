"""Session persistence - one JSON file plus versioned mask PNGs.

Sketch §10 says "ports `storage/workspace.py` mostly as-is". Scope-checking
the real file (2026-09-23, per this branch's working method) showed that
only half of it can:

- **The ROI half ports directly.** `_encode_area_roi` and the
  `RoiMask`/`per_wavelength` encoders below are that code, carried over
  including its documented performance reason for enumerating fields by
  hand instead of using `asdict()`.
- **The settings half cannot.** The old `processing_profile.json` has one
  flat `"preprocessing"` block mirroring `PreprocessingSettings`'s 40
  fields, and that dataclass no longer exists - the 2026-09-20 decomposition
  split it into `GeometrySettings`/`BackgroundSettings`/`MaskSettings`/
  `ChromaticSettings`, each owned by its own module. Keeping the old shape
  would mean a translation layer permanently tracking two different
  decompositions, re-coupling the new modules to exactly the grab-bag
  boundary that split removed.
- **The mask half has no old format to port at all.** The old app stored
  one `session_mask` (a single boolean array, inline in the JSON) plus
  per-frame pixel diffs. `MaskModule` instead holds a *timeline* of
  `MaskChange` records, each a full-frame mask keyed by frame and scope.

**Maintainer's decisions (2026-09-23)**, both presented with the trade-offs
before anything was written:

1. **A new per-module format, with no importer for old
   `processing_profile.json` files.** Each top-level block is one module's
   own state, so a module's encode/decode stays next to what it owns and no
   translation layer exists to drift.
2. **Mask pixel data goes to versioned PNGs**, through the exact
   `persist_mask_snapshot` mechanism `analysis/provenance.py` already uses.
   The JSON stays small and readable, and the app has one on-disk mask
   format rather than two.

   Note what that dedup does and does not do: it is **per (frame, scope)
   group**, because the filename encodes the frame. Re-saving an unchanged
   session rewrites no mask file at all, which is what matters for an
   autosave. Two *different* frames holding identical pixels still get one
   file each - content-addressing across frames would mean hash-named
   files, which the provenance design doc deliberately rejected in favour
   of names you can read.

**Layout** (under a dataset's own folder, beside `analysis/`)::

    <root>/session/session.json
    <root>/session/masks/mask_cube0_wl500_persi_v1.png

Session masks live in their own folder rather than sharing
`analysis/masks/`: those two hold the same *kind* of thing but answer
different questions - "what is the mask now" versus "what mask produced
this stored cell". Sharing a folder would make deleting a stale analysis
quietly destroy current session state.

**No Qt in this file.** It takes and returns plain dataclasses; whoever owns
the modules is responsible for reading state out of them and applying it
back (`app_rewrite.capture_session`/`apply_session`).

**Not persisted, because nothing in the rewrite owns them yet**: the old
profile's `statistics_settings` (no counterpart exists at all) and
`image_exclusions` (`ImageExclusionRule` is referenced only inside
`dataset/io.py`, owned by no module). Flagged rather than invented - both
want a real owner first, and a session format is the wrong place to decide
that.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path

import numpy as np

from ..analysis.provenance import (
    FrameNamingScheme,
    MaskSnapshotRef,
    mask_scope_tag,
    persist_mask_snapshot,
)
from ..analysis.settings import MetricSettings, StatisticsSettings
from ..image_tools.background.model import BackgroundSettings
from ..image_tools.chromatic.model import (
    ChromaticLandmarkObservation,
    ChromaticSettings,
    ChromaticTransformModel,
    GridBoundsDefinition,
)
from ..image_tools.geometry.model import CropDefinition, GeometrySettings
from ..image_tools.mask.model import MaskChange, MaskSettings
from ..roi.model import AreaRoi, AreaRoiDetectionSettings, AreaRoiGroup, RoiArrayGroup, RoiMask

logger = logging.getLogger(__name__)

SESSION_SCHEMA_NAME = "lspri_rewrite_session"
SESSION_SCHEMA_VERSION = "1.1"
"""Bumped major for a breaking change, minor for an additive one - the same
rule `docs/schemas/hdf_standard.md` sets for measurement files. `load_session`
rejects an unknown schema name and an incompatible major version rather than
guessing at a file it doesn't understand.

1.0 -> 1.1 (2026-09-23): added the `"analysis"` block (`MetricSettings` +
`StatisticsSettings`), when the query layer gave those an owner. Additive,
so a 1.0 file still loads - the block is simply absent and both fall back
to their defaults, which is the correct reading of a session written before
they could be configured at all."""

_MASK_SCOPES = ("persistent", "individual")

# MaskSettings' two np.ndarray fields are deliberately never persisted - see
# image_tools/mask/module.py's finding 1: they are the old app's inert "New
# mask system state", written only by a reset, never read as real input.
# Persisting them would put array data back into the JSON that decision
# took out.
_MASK_SETTINGS_SKIP_FIELDS = ("histogram_mask", "figure_mask")


# -- atomic JSON write (ported verbatim from storage/workspace.py) ----------


def write_json_file(path: Path, payload: dict) -> None:
    """Write `payload` to `path` as JSON, atomically.

    Ported from `storage/workspace.py`. This backs a session autosave that
    can fire frequently during normal use: writing to a temp file in the
    same directory and swapping it in with `os.replace()` (atomic on both
    POSIX and Windows) means a crash or power loss mid-write leaves either
    the old file or the new one intact, never a truncated one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(data)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# -- ROI encoding (ported from storage/workspace.py) ------------------------


def _encode_bool_mask(mask: np.ndarray) -> dict:
    mask_array = np.asarray(mask, dtype=bool)
    packed = np.packbits(mask_array.ravel(order="C"))
    return {
        "shape": [int(dim) for dim in mask_array.shape],
        "data": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def _decode_bool_mask(payload: object) -> np.ndarray | None:
    if not isinstance(payload, dict):
        return None
    shape_raw = payload.get("shape", [])
    data_raw = payload.get("data", "")
    if not (isinstance(shape_raw, list) and len(shape_raw) == 2 and isinstance(data_raw, str)):
        return None
    try:
        height, width = int(shape_raw[0]), int(shape_raw[1])
        if height <= 0 or width <= 0:
            return None
        packed = base64.b64decode(data_raw.encode("ascii"))
        unpacked = np.unpackbits(np.frombuffer(packed, dtype=np.uint8), count=height * width)
        return unpacked.astype(bool, copy=False).reshape((height, width))
    except Exception:
        # A corrupt or truncated mask should cost that one ROI its mask
        # geometry, not prevent the whole session from opening.
        logger.warning("Ignoring an undecodable ROI mask in the session file", exc_info=True)
        return None


def _encode_roi_mask(roi_mask: RoiMask | None) -> dict | None:
    if roi_mask is None:
        return None
    payload = _encode_bool_mask(roi_mask.mask)
    payload["x0"] = int(roi_mask.x0)
    payload["y0"] = int(roi_mask.y0)
    return payload


def _decode_roi_mask(payload: object) -> RoiMask | None:
    mask = _decode_bool_mask(payload)
    if mask is None:
        return None
    assert isinstance(payload, dict)  # _decode_bool_mask already rejected non-dicts
    return RoiMask(x0=int(payload.get("x0", 0)), y0=int(payload.get("y0", 0)), mask=mask)


def _encode_per_wavelength(per_wavelength: dict[tuple[int, float], tuple[float, float]] | None) -> list[dict] | None:
    if not per_wavelength:
        return None
    return [
        {"cube_index": int(cube), "wavelength_nm": float(wl), "x": float(xy[0]), "y": float(xy[1])}
        for (cube, wl), xy in per_wavelength.items()
    ]


def _decode_per_wavelength(raw: object) -> dict[tuple[int, float], tuple[float, float]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    decoded: dict[tuple[int, float], tuple[float, float]] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            decoded[(int(entry["cube_index"]), float(entry["wavelength_nm"]))] = (
                float(entry["x"]), float(entry["y"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    return decoded or None


def _encode_area_roi(area_roi: AreaRoi) -> dict:
    """Shallow field enumeration, **not** `asdict()` - ported from
    `storage/workspace.py` along with its measured reason.

    `asdict()` recursively deep-copies every field, including
    `per_wavelength` and the two mask arrays, all three of which are then
    immediately overwritten below with their own JSON-safe encoding - so
    the deep copy is thrown away unused. On a real dataset that deepcopy
    measured ~2 s of ~3.6 s per 160 ROIs, paid on every close and on every
    autosave's unchanged-check. Every other `AreaRoi` field is a plain
    scalar, for which `getattr()` is exactly as correct."""
    payload = {f.name: getattr(area_roi, f.name) for f in fields(area_roi)}
    payload["sample_mask"] = _encode_roi_mask(area_roi.sample_mask)
    payload["reference_mask"] = _encode_roi_mask(area_roi.reference_mask)
    payload["per_wavelength"] = _encode_per_wavelength(area_roi.per_wavelength)
    return payload


def _decode_area_rois(raw: object) -> list[AreaRoi]:
    """Decode leniently: an entry missing the two fields with no default
    (`area_roi_id` and a position) is skipped, unknown keys are ignored, and
    anything else falls back to the dataclass default. A session file
    written by a newer build should open in an older one minus the fields it
    doesn't know, rather than failing outright."""
    if not isinstance(raw, list):
        return []
    known = {f.name for f in fields(AreaRoi)}
    decoded: list[AreaRoi] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            kwargs = {key: value for key, value in entry.items() if key in known}
            kwargs["area_roi_id"] = int(entry["area_roi_id"])
            kwargs["center_x"] = float(entry["center_x"])
            kwargs["center_y"] = float(entry["center_y"])
            kwargs["sample_radius_px"] = float(entry.get("sample_radius_px", 10.0))
            kwargs["sample_mask"] = _decode_roi_mask(entry.get("sample_mask"))
            kwargs["reference_mask"] = _decode_roi_mask(entry.get("reference_mask"))
            kwargs["per_wavelength"] = _decode_per_wavelength(entry.get("per_wavelength"))
            decoded.append(AreaRoi(**kwargs))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping an undecodable ROI in the session file", exc_info=True)
    return decoded


def _decode_dataclass_list(raw: object, cls):
    """Rebuild a list of plain (all-scalar-field) dataclasses, ignoring
    unknown keys - the lenient counterpart to `asdict()` on the way out."""
    if not isinstance(raw, list):
        return []
    known = {f.name for f in fields(cls)}
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(cls(**{key: value for key, value in entry.items() if key in known}))
        except (TypeError, ValueError):
            logger.warning("Skipping an undecodable %s in the session file", cls.__name__, exc_info=True)
    return out


def _decode_settings(raw: object, default, nested: dict | None = None):
    """Rebuild one settings dataclass from its block, falling back to the
    default for anything missing or unknown. `nested` names fields that are
    themselves dataclasses and the class to rebuild each from."""
    if not isinstance(raw, dict):
        return default
    known = {f.name for f in fields(default)}
    kwargs = {key: value for key, value in raw.items() if key in known}
    for name, cls in (nested or {}).items():
        if isinstance(kwargs.get(name), dict):
            nested_known = {f.name for f in fields(cls)}
            kwargs[name] = cls(**{k: v for k, v in kwargs[name].items() if k in nested_known})
    try:
        return replace(default, **kwargs)
    except (TypeError, ValueError):
        logger.warning("Ignoring an undecodable settings block; using defaults", exc_info=True)
        return default


# -- the session state itself ----------------------------------------------


@dataclass
class SessionState:
    """Everything a session restores, as plain dataclasses - one field per
    owning module, mirroring the on-disk block structure.

    Deliberately *not* a reference to the modules themselves: this file
    stays Qt-free and testable, and whoever owns the modules does the
    reading and applying (`app_rewrite.capture_session`/`apply_session`)."""

    geometry: GeometrySettings = field(default_factory=GeometrySettings)
    background: BackgroundSettings = field(default_factory=BackgroundSettings)
    mask_settings: MaskSettings = field(default_factory=MaskSettings)
    mask_changes: tuple[MaskChange, ...] = ()
    chromatic_settings: ChromaticSettings = field(default_factory=ChromaticSettings)
    chromatic_models: tuple[ChromaticTransformModel, ...] = ()
    chromatic_landmarks: tuple[ChromaticLandmarkObservation, ...] = ()
    detection_settings: AreaRoiDetectionSettings = field(default_factory=AreaRoiDetectionSettings)
    metric_settings: MetricSettings = field(default_factory=MetricSettings)
    statistics_settings: StatisticsSettings = field(default_factory=StatisticsSettings)
    rois: tuple[AreaRoi, ...] = ()
    groups: tuple[AreaRoiGroup, ...] = ()
    arrays: tuple[RoiArrayGroup, ...] = ()
    selected_cube: int = 0
    selected_wavelength: float = 0.0
    selected_roi_ids: tuple[int, ...] = ()


def session_dir(root: Path) -> Path:
    return Path(root) / "session"


def session_path(root: Path) -> Path:
    return session_dir(root) / "session.json"


def _masks_dir(root: Path) -> Path:
    return session_dir(root) / "masks"


def _encode_mask_settings(settings: MaskSettings) -> dict:
    return {
        f.name: getattr(settings, f.name)
        for f in fields(settings)
        if f.name not in _MASK_SETTINGS_SKIP_FIELDS
    }


def _encode_mask_changes(root: Path, changes: tuple[MaskChange, ...], naming: FrameNamingScheme) -> list[dict]:
    """Write each change's pixels as a versioned PNG and record only the
    reference in the JSON.

    Re-saving a mask whose content is unchanged reuses its existing version
    and writes nothing - `persist_mask_snapshot`'s dedup, reused rather
    than reimplemented. That dedup is scoped to one (frame, scope) group,
    since the frame is part of the filename, so this writes one file per
    change rather than one per distinct image."""
    encoded: list[dict] = []
    for change in changes:
        cube_index, wavelength_nm = change.frame
        mask_8bit = np.asarray(change.mask, dtype=bool).astype(np.uint8) * 255
        ref = persist_mask_snapshot(
            _masks_dir(root), mask_8bit,
            cube_index=int(cube_index), wavelength_nm=float(wavelength_nm),
            tag=mask_scope_tag(change.scope), naming=naming,
        )
        encoded.append({
            "cube_index": int(cube_index),
            "wavelength_nm": float(wavelength_nm),
            "scope": str(change.scope),
            "version": int(ref.version),
        })
    return encoded


def _decode_mask_changes(root: Path, raw: object, naming: FrameNamingScheme) -> tuple[MaskChange, ...]:
    if not isinstance(raw, list) or not raw:
        return ()
    import cv2  # local import, same convention analysis/provenance.py uses

    decoded: list[MaskChange] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            cube_index = int(entry["cube_index"])
            wavelength_nm = float(entry["wavelength_nm"])
            scope = str(entry["scope"])
            if scope not in _MASK_SCOPES:
                raise ValueError(f"unknown mask scope {scope!r}")
            ref = MaskSnapshotRef(
                cube_index=cube_index, wavelength_nm=wavelength_nm,
                tag=mask_scope_tag(scope), version=int(entry["version"]),
            )
            path = _masks_dir(root) / ref.filename(naming)
            image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if image is None:
                # The JSON references a PNG that isn't there. Dropping just
                # that change is better than refusing the whole session:
                # the rest of the timeline, the ROIs and every setting are
                # still perfectly restorable.
                logger.warning("Session mask file missing, skipping that change: %s", path)
                continue
            decoded.append(MaskChange(
                frame=(cube_index, wavelength_nm), scope=scope,
                mask=np.asarray(image, dtype=bool),
            ))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping an undecodable mask change in the session file", exc_info=True)
    return tuple(decoded)


def save_session(root: Path, state: SessionState, naming: FrameNamingScheme) -> Path:
    """Write `state` under `root/session/`, returning the JSON's path.

    `naming` is the dataset's frame-tag scheme (`FrameNamingScheme.
    for_dataset`), passed in rather than derived here because only the
    caller knows the dataset - and it must be the *same* scheme on save and
    load, or a mask's filename won't be found again."""
    root = Path(root)
    payload = {
        "schema_name": SESSION_SCHEMA_NAME,
        "schema_version": SESSION_SCHEMA_VERSION,
        "frame_naming": {"cube_digits": naming.cube_digits, "wavelength_decimals": naming.wavelength_decimals},
        "geometry": asdict(state.geometry),
        "background": asdict(state.background),
        "mask": {
            "settings": _encode_mask_settings(state.mask_settings),
            "changes": _encode_mask_changes(root, state.mask_changes, naming),
        },
        "chromatic": {
            "settings": asdict(state.chromatic_settings),
            "models": [asdict(model) for model in state.chromatic_models],
            "landmarks": [asdict(landmark) for landmark in state.chromatic_landmarks],
        },
        # Its own block, not folded into "roi": these drive the query layer
        # (how stored numbers are displayed), and nothing in them can make a
        # stored cell stale - see analysis/settings.py.
        "analysis": {
            "metric": asdict(state.metric_settings),
            "statistics": asdict(state.statistics_settings),
        },
        "roi": {
            "detection_settings": asdict(state.detection_settings),
            "rois": [_encode_area_roi(roi) for roi in state.rois],
            "groups": [asdict(group) for group in state.groups],
            "arrays": [asdict(array_group) for array_group in state.arrays],
        },
        "selection": {
            "cube_index": int(state.selected_cube),
            "wavelength_nm": float(state.selected_wavelength),
            "selected_roi_ids": sorted(int(roi_id) for roi_id in state.selected_roi_ids),
        },
    }
    path = session_path(root)
    write_json_file(path, payload)
    return path


def load_session(root: Path) -> SessionState | None:
    """Read `root/session/session.json`, or `None` if there isn't one.

    A missing file is the ordinary fresh-dataset case, not an error - the
    same contract `analysis/store.read_all_cells` follows. A file that is
    unreadable, isn't this schema, or is a future major version raises:
    silently starting from defaults would look identical to "this dataset
    was never set up", and the difference matters when the alternative is
    losing an afternoon of ROI placement."""
    path = session_path(Path(root))
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a session file (expected a JSON object)")

    name = payload.get("schema_name")
    if name != SESSION_SCHEMA_NAME:
        raise ValueError(f"{path} has schema_name {name!r}, expected {SESSION_SCHEMA_NAME!r}")
    version = str(payload.get("schema_version", "0.0"))
    if version.split(".")[0] != SESSION_SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"{path} is schema version {version}, which this build "
            f"(supporting {SESSION_SCHEMA_VERSION}) cannot read"
        )

    naming_raw = payload.get("frame_naming") or {}
    naming = FrameNamingScheme(
        cube_digits=int(naming_raw.get("cube_digits", 1)),
        wavelength_decimals=int(naming_raw.get("wavelength_decimals", 0)),
    )

    mask_block = payload.get("mask") or {}
    chromatic_block = payload.get("chromatic") or {}
    roi_block = payload.get("roi") or {}
    analysis_block = payload.get("analysis") or {}
    selection_block = payload.get("selection") or {}

    return SessionState(
        geometry=_decode_settings(payload.get("geometry"), GeometrySettings(), {"crop": CropDefinition}),
        background=_decode_settings(payload.get("background"), BackgroundSettings()),
        mask_settings=_decode_settings(mask_block.get("settings"), MaskSettings()),
        mask_changes=_decode_mask_changes(Path(root), mask_block.get("changes"), naming),
        chromatic_settings=_decode_settings(
            chromatic_block.get("settings"), ChromaticSettings(),
            {"chromatic_grid_bounds": GridBoundsDefinition},
        ),
        chromatic_models=tuple(_decode_dataclass_list(chromatic_block.get("models"), ChromaticTransformModel)),
        chromatic_landmarks=tuple(
            _decode_dataclass_list(chromatic_block.get("landmarks"), ChromaticLandmarkObservation)
        ),
        detection_settings=_decode_settings(
            roi_block.get("detection_settings"), AreaRoiDetectionSettings()
        ),
        # Absent in a 1.0 file - `_decode_settings` returns the default,
        # which is the right reading of a session written before these
        # could be configured.
        metric_settings=_decode_settings(analysis_block.get("metric"), MetricSettings()),
        statistics_settings=_decode_settings(analysis_block.get("statistics"), StatisticsSettings()),
        rois=tuple(_decode_area_rois(roi_block.get("rois"))),
        groups=tuple(_decode_dataclass_list(roi_block.get("groups"), AreaRoiGroup)),
        arrays=tuple(_decode_dataclass_list(roi_block.get("arrays"), RoiArrayGroup)),
        selected_cube=int(selection_block.get("cube_index", 0)),
        selected_wavelength=float(selection_block.get("wavelength_nm", 0.0)),
        selected_roi_ids=tuple(int(roi_id) for roi_id in selection_block.get("selected_roi_ids", ())),
    )

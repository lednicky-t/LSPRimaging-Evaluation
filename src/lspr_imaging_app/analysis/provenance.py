"""Per-cell provenance: fingerprinting + the file-backed dedup/versioning
scheme (sketch §5 "The analysis store: one file, per-cell provenance", full
detail in `docs/analysis_provenance_store_design_2026-09.md`). New code,
not a port.

No Qt import allowed in this file (AGENTS.md testing rule) - every function
here takes plain, already-resolved values (settings dataclasses, arrays,
plain dicts), never a live QObject module reference. Gathering those values
*from* the real modules (`GeometryModule.settings()`,
`MaskModule.resolve_mask_source()`, `ChromaticModule.affine_for()`, ...) is
`engine.py`/`worker.py`'s job, not this file's.

**Six provenance inputs, not five** - widened from the sketch's original
list 2026-09-22 (see the design doc): `GeometryModule`'s crop/rotate/flip
settings are a real fingerprint input too, since they change the pixel grid
every ROI's coordinates are already defined against.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..image_tools.background.model import BackgroundSettings
from ..roi.model import AreaRoi, AreaRoiDetectionSettings

# -- naming: frame identity + adaptive numeric formatting --------------------


def cube_digit_width(max_cube_index: int) -> int:
    """Zero-padding width for a cube index, derived from the real dataset
    (the highest cube index actually present) rather than a fixed guess -
    `cube007` sorts correctly next to `cube008`/`cube100` in a folder
    listing; an unpadded `cube7`/`cube8`/`cube100` would not."""
    return max(len(str(max(int(max_cube_index), 0))), 1)


def wavelength_decimal_precision(wavelengths_nm: list[float], max_decimals: int = 3) -> int:
    """Smallest decimal precision at which every wavelength in the dataset
    stays distinguishable from every other - checked once per dataset, then
    used for every wavelength-derived filename in it (mixing precisions
    within one dataset would break simple pattern-matching/sorting). `0`
    (whole nm, e.g. `wl500`) is expected to be sufficient for real
    acquisition hardware - confirmed with the maintainer 2026-09-22 - but
    this stays correct if two configured wavelengths ever round to the same
    whole nanometer."""
    for decimals in range(max_decimals + 1):
        rounded = [round(float(wl), decimals) for wl in wavelengths_nm]
        if len(set(rounded)) == len(wavelengths_nm):
            return decimals
    return max_decimals  # pathological collision even at max precision - not this scheme's problem to solve further


def format_wavelength(wavelength_nm: float, decimals: int) -> str:
    if decimals == 0:
        return str(int(round(float(wavelength_nm))))
    return f"{float(wavelength_nm):.{decimals}f}"


@dataclass(frozen=True)
class FrameNamingScheme:
    """Per-dataset numeric formatting, derived once (see `cube_digit_width`/
    `wavelength_decimal_precision`) and reused for every provenance filename
    in that dataset's `analysis/` folder."""

    cube_digits: int
    wavelength_decimals: int

    @classmethod
    def for_dataset(cls, cube_indices: list[int], wavelengths_nm: list[float]) -> "FrameNamingScheme":
        max_cube = max(cube_indices) if cube_indices else 0
        return cls(
            cube_digits=cube_digit_width(max_cube),
            wavelength_decimals=wavelength_decimal_precision(wavelengths_nm),
        )

    def frame_tag(self, cube_index: int, wavelength_nm: float) -> str:
        cube_part = str(int(cube_index)).zfill(self.cube_digits)
        wl_part = format_wavelength(wavelength_nm, self.wavelength_decimals)
        return f"cube{cube_part}_wl{wl_part}"


# -- versioning + dedup: sequential per-group counters, no hashing -----------


def next_version(existing: dict[int, object], candidate: object, *, equal) -> tuple[int, bool]:
    """Core dedup/versioning rule shared by every provenance-input kind
    (mask/background/chromatic/settings-snapshot): `existing` maps already-
    assigned version numbers (1-based) to their stored content for this same
    group (e.g. this exact (cube, wavelength, tag) group - there are only
    ever a handful of versions per group in practice, so a direct content
    comparison is cheap and needs no hashing - see the design doc's
    "Versioning and dedup" section for why this replaced an earlier
    hash-based draft). Returns `(version, is_new)`: if `candidate` matches
    an existing version's content (via the caller-supplied `equal`
    predicate), reuses that version number and `is_new=False` - nothing new
    should be written. Otherwise returns the next unused version number and
    `is_new=True`.
    """
    for version, stored in existing.items():
        if equal(stored, candidate):
            return version, False
    return (max(existing.keys()) + 1 if existing else 1), True


def _arrays_equal(a: np.ndarray, b: np.ndarray) -> bool:
    return a.shape == b.shape and bool(np.array_equal(a, b))


def _json_values_equal(a: dict, b: dict) -> bool:
    return a == b


# -- mask / background / chromatic snapshots: file-backed, versioned ---------


MASK_SCOPE_TAGS: dict[str, str] = {"persistent": "persi", "individual": "indiv"}
"""Translates `MaskModule`'s own scope vocabulary into this layer's
fixed-width filename tag.

Two vocabularies exist on purpose, and this is the single place they meet
(added 2026-09-23, while wiring `AnalysisEngine`). `MaskModule` says
`"persistent"`/`"individual"` because that is what the timeline actually
means; filenames say `persi`/`indiv` because both are 5 characters, which
keeps a directory listing aligned (see the provenance design doc). Doing
the translation here rather than in whoever wires the engine keeps the
knowledge of the filename format inside the module that owns the format."""


def mask_scope_tag(scope: str) -> str:
    """`MASK_SCOPE_TAGS` as a lookup that fails loudly on an unknown scope
    rather than silently writing a mask under a wrong-length tag."""
    try:
        return MASK_SCOPE_TAGS[scope]
    except KeyError:
        raise ValueError(f"scope must be one of {tuple(MASK_SCOPE_TAGS)}, got {scope!r}") from None


@dataclass(frozen=True)
class MaskSnapshotRef:
    """A reference to one versioned mask image file - what a
    `ProvenanceRecord`/settings snapshot actually stores, not the pixel
    data itself."""

    cube_index: int
    wavelength_nm: float
    tag: str  # "persi" or "indiv" - see design doc; matches MaskModule's own "persistent"/"individual" scope
    version: int

    def filename(self, naming: FrameNamingScheme) -> str:
        return f"mask_{naming.frame_tag(self.cube_index, self.wavelength_nm)}_{self.tag}_v{self.version}.png"


@dataclass(frozen=True)
class ChromaticSnapshotRef:
    cube_index: int
    wavelength_nm: float
    version: int

    def filename(self, naming: FrameNamingScheme) -> str:
        return f"chromatic_{naming.frame_tag(self.cube_index, self.wavelength_nm)}_v{self.version}.json"


def _existing_mask_versions(masks_dir: Path, cube_index: int, wavelength_nm: float, tag: str, naming: FrameNamingScheme) -> dict[int, np.ndarray]:
    """Reads back whatever mask versions already exist on disk for this
    exact (cube, wavelength, tag) group, for `next_version` to compare
    against. Local import of `cv2` (this app's existing dependency for
    16-bit-safe PNG I/O - see the design doc's "Formats" section) kept
    inside the function so this module stays importable without it in
    contexts that never touch the filesystem (e.g. unit tests that only
    exercise `compute_fingerprint`)."""
    import cv2

    prefix = f"mask_{naming.frame_tag(cube_index, wavelength_nm)}_{tag}_v"
    found: dict[int, np.ndarray] = {}
    if not masks_dir.is_dir():
        return found
    for path in masks_dir.glob(f"{prefix}*.png"):
        suffix = path.stem[len(prefix):]
        if not suffix.isdigit():
            continue
        version = int(suffix)
        image = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if image is not None:
            found[version] = image
    return found


def resolve_mask_snapshot_ref(
    masks_dir: Path,
    mask_8bit: np.ndarray,
    *,
    cube_index: int,
    wavelength_nm: float,
    tag: str,
    naming: FrameNamingScheme,
) -> tuple[MaskSnapshotRef, bool]:
    """Work out which version this exact mask content *is* (or would be),
    reading existing files but **writing nothing**. Returns the ref plus
    whether it is new (i.e. not yet on disk).

    **Exists to fix a real bug, found 2026-09-23 by running a real analysis
    with an ignore mask set.** Planning (`AnalysisEngine._gather_current_
    inputs`) may not write mask files - the design doc's "written lazily,
    only when actually analyzed" rule - so it used to substitute
    `version=0` as a placeholder identity. But that placeholder goes
    straight into the `SettingsSnapshot` the planner fingerprints, while
    `compute_cell` records the *real* version. The two could therefore
    never match: with any mask present, every cell looked permanently
    stale, so "N cells will be recomputed" always said "all of them" and
    every run recomputed everything from scratch, forever.

    Splitting version-resolution out of the write is what lets planning ask
    "which version would this content be?" honestly. If the mask has been
    persisted before, this finds that existing version and the fingerprints
    match; if it genuinely is new, it returns the version it will get, which
    correctly fails to match any stored fingerprint.

    Cost: planning now reads back the existing mask PNGs for each frame
    instead of skipping them. That is the same I/O `compute_cell` already
    does, and it buys a `preview_recompute` that tells the truth."""
    if tag not in ("persi", "indiv"):
        raise ValueError(f"tag must be 'persi' or 'indiv', got {tag!r}")
    mask_8bit = np.asarray(mask_8bit, dtype=np.uint8)
    existing = _existing_mask_versions(masks_dir, cube_index, wavelength_nm, tag, naming)
    version, is_new = next_version(existing, mask_8bit, equal=_arrays_equal)
    ref = MaskSnapshotRef(cube_index=cube_index, wavelength_nm=wavelength_nm, tag=tag, version=version)
    return ref, is_new


def persist_mask_snapshot(
    masks_dir: Path,
    mask_8bit: np.ndarray,
    *,
    cube_index: int,
    wavelength_nm: float,
    tag: str,
    naming: FrameNamingScheme,
) -> MaskSnapshotRef:
    """Write `mask_8bit` (a coverage-fraction mask, 0-255, matching the same
    fractional-weighting headroom as ROI's §6a rasterization) as a new
    versioned file, or return a reference to an already-identical existing
    version without writing anything - the "written lazily, only when
    actually analyzed" rule (design doc) is the *caller's* job (only call
    this once a cell is genuinely being computed), not something this
    function enforces itself.
    """
    ref, is_new = resolve_mask_snapshot_ref(
        masks_dir, mask_8bit, cube_index=cube_index, wavelength_nm=wavelength_nm, tag=tag, naming=naming,
    )
    if is_new:
        import cv2

        masks_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(masks_dir / ref.filename(naming)), np.asarray(mask_8bit, dtype=np.uint8))
    return ref


def _existing_chromatic_versions(chromatic_dir: Path, cube_index: int, wavelength_nm: float, naming: FrameNamingScheme) -> dict[int, dict]:
    prefix = f"chromatic_{naming.frame_tag(cube_index, wavelength_nm)}_v"
    found: dict[int, dict] = {}
    if not chromatic_dir.is_dir():
        return found
    for path in chromatic_dir.glob(f"{prefix}*.json"):
        suffix = path.stem[len(prefix):]
        if not suffix.isdigit():
            continue
        try:
            found[int(suffix)] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return found


def persist_chromatic_snapshot(
    chromatic_dir: Path,
    affine_matrix: np.ndarray,
    *,
    cube_index: int,
    wavelength_nm: float,
    naming: FrameNamingScheme,
    round_decimals: int = 9,
) -> ChromaticSnapshotRef:
    """Same lazy/versioned/deduplicated treatment as
    `persist_mask_snapshot`, for a chromatic affine matrix instead of a mask
    image. `round_decimals` matches sketch §5's fixed rounding (absorbs
    floating-point re-serialization noise, not a materiality judgment) so
    two runs that land on the same model don't spuriously get different
    versions."""
    payload = {"affine_matrix": np.round(np.asarray(affine_matrix, dtype=np.float64), round_decimals).tolist()}
    existing = _existing_chromatic_versions(chromatic_dir, cube_index, wavelength_nm, naming)
    version, is_new = next_version(existing, payload, equal=_json_values_equal)
    ref = ChromaticSnapshotRef(cube_index=cube_index, wavelength_nm=wavelength_nm, version=version)
    if is_new:
        chromatic_dir.mkdir(parents=True, exist_ok=True)
        (chromatic_dir / ref.filename(naming)).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return ref


# -- settings snapshot: ties one combination of inputs together --------------


REFERENCE_EXCLUSION_MODES: tuple[str, ...] = ("none", "exclude_all_sample_rois")
"""How a reference ring treats pixels belonging to sample apertures
(2026-09-22 maintainer decision - see `sample_exclusion_digest` below and
`tasks.py`'s `compute_cell` for the mechanism):

- ``"none"`` - no cross-ROI exclusion at all; a reference ring counts every
  pixel inside it that the ignore mask doesn't remove.
- ``"exclude_all_sample_rois"`` - a reference ring never counts a pixel that
  falls inside *any* ROI's sample aperture. Reference rings overlapping each
  other are still counted normally - only sample pixels are removed.

**Deliberately computed from every ROI, never from "the currently selected
ones"** - the old app (`gui/analysis_tasks.py`'s `all_selected_sample_mask`)
built this union from the selected subset, which made a cell's correct value
depend on *what else happened to be selected when it was computed* - a
genuinely nasty dependency to fingerprint. Making it all-ROIs removes that
entirely: the exclusion is a deterministic function of ROI geometry alone.
"""

DEFAULT_REFERENCE_EXCLUSION_MODE = "exclude_all_sample_rois"
"""Maintainer's call (2026-09-23), changed from the initial `"none"`: a
reference ring should never count sample pixels by default.

`"none"` was only ever the *safe* default (it changes nothing relative to
code that predates the mode), not the *right* one. The stable app
effectively behaved like `"exclude_all_sample_rois"` whenever more than one
ROI was selected, which is the normal case - so `"none"` matched its
single-ROI behavior only, and shipping it as the default would mean a
biased reference value for any two ROIs close enough that one's sample
circle falls inside the other's reference ring."""


def sample_exclusion_digest(rois: list[AreaRoi] | tuple[AreaRoi, ...]) -> list:
    """Every ROI's *sample*-side geometry, sorted by id - the fingerprint
    input that makes `"exclude_all_sample_rois"` mode safe to cache.

    Needed because in that mode, moving ROI X genuinely changes ROI Y's
    reference-ring pixel set (X's sample circle carves into it), so Y's
    stored value must be invalidated when X moves - otherwise a reopened
    session shows a stale, biased reference value with nothing to indicate
    it. Only the *sample* side matters: reference rings overlapping each
    other are counted normally in this mode, so reference radii are not an
    input to the exclusion.

    Lives in `SettingsSnapshot` (not `ProvenanceRecord`) on purpose: the
    snapshot is already deduplicated and referenced by a small integer
    version, so this is written a handful of times per run rather than
    embedded in all (ROI x cube) cells - the difference between a few
    hundred KB and well over a GB at realistic ROI/cube counts.
    """
    digest: list = []
    for roi in sorted(rois, key=lambda item: int(item.area_roi_id)):
        digest.append({
            "area_roi_id": int(roi.area_roi_id),
            "center_x": float(roi.center_x),
            "center_y": float(roi.center_y),
            "sample_radius_px": float(roi.sample_radius_px),
            "sample_diameter_px": roi.sample_diameter_px,
            "sample_geometry_type": roi.sample_geometry_type,
            "sample_mask": None if roi.sample_mask is None else {
                "x0": roi.sample_mask.x0, "y0": roi.sample_mask.y0, "mask": roi.sample_mask.mask.tolist(),
            },
        })
    return digest


def background_exclusion_digest(
    rois: list[AreaRoi] | tuple[AreaRoi, ...],
    background_settings: BackgroundSettings,
    detection_settings: AreaRoiDetectionSettings | None,
) -> dict | None:
    """What the *background estimate* excluded - the fingerprint half of
    actually wiring `rois`/`mask_settings` into `apply_preprocessing`
    (2026-09-23; see `tasks.py`'s `compute_cell` for the compute half).

    Returns `None` whenever background flattening is off or neither
    exclusion toggle is on, and `SettingsSnapshot.as_json()` then omits the
    field entirely rather than writing `null`. That is deliberate: a
    snapshot this change cannot possibly affect has to serialize exactly as
    it did before the field existed, or simply adding it would invalidate
    every stored cell of every existing analysis for no reason.

    Two independent parts, each recorded only while its own toggle is on:

    - ``"rois"`` - every ROI's background-exclusion geometry. With ROI
      exclusion on, moving ROI X changes the background estimate under ROI
      Y, so Y's stored value must be invalidated when X moves. Exactly the
      invalidation problem `sample_exclusion_digest` exists for on the
      reference-ring side, and the same answer. Only the three fields
      `background/estimate.py`'s `_roi_exclusion_mask` actually reads:
      it works off `sample_radius_px` directly and never consults
      `sample_diameter_px` or a sample mask, so recording those would
      invalidate cells on edits the background estimate cannot see.
    - ``"ignore_marked_pixels"`` - the one `AreaRoiDetectionSettings` field
      that reaches the background estimate (`roi/detection.py`'s
      `ignored_pixel_mask` gates the entire external mask on it). Flipping
      it changes the estimate while `BackgroundSettings` stays identical,
      so nothing else in the snapshot would notice.

    **Computed from every ROI, never a selected subset** - same reasoning as
    `REFERENCE_EXCLUSION_MODES`, and the maintainer's explicit instruction
    for this fix in the 2026-09-23 build log entry: the old app built its
    background exclusion from the selected ROIs, which made a stored value
    depend on what happened to be selected when it was computed.
    """
    if not background_settings.flatten_background_enabled:
        return None
    digest: dict = {}
    if background_settings.flatten_background_exclude_area_rois and rois:
        digest["rois"] = [
            {
                "area_roi_id": int(roi.area_roi_id),
                "center_x": float(roi.center_x),
                "center_y": float(roi.center_y),
                "sample_radius_px": float(roi.sample_radius_px),
            }
            for roi in sorted(rois, key=lambda item: int(item.area_roi_id))
        ]
    if background_settings.flatten_background_exclude_mask and detection_settings is not None:
        digest["ignore_marked_pixels"] = bool(detection_settings.ignore_marked_pixels)
    return digest or None


@dataclass(frozen=True)
class SettingsSnapshot:
    """One combination of dataset-wide/per-frame inputs, as actually in
    effect when a cell was computed. Dedup'd and versioned the same way as
    masks/chromatic models (a flat, dataset-wide sequential counter - not
    tied to one specific frame the way a mask edit is, since a "combination"
    isn't authored at a single frame).

    **Background is a plain settings dict here, not a `MaskSnapshotRef`-
    style image reference - and that is the final design, not a placeholder**
    (maintainer's decision 2026-09-23; an earlier version of this docstring
    called it interim scope pending a `MaskModule`-style timeline on
    `BackgroundModule`, which will not be built). Only the *method* is
    recorded, and since `BackgroundSettings` is flat and dataset-wide it is
    identical for every cube and wavelength.

    The computed background profile is deliberately **not** stored anywhere.
    It is a deterministic function of the raw frame plus `geometry`, `mask`
    and `background` - all already in this snapshot - so recording it would
    add no invalidation power, only ~4 GB of audit artifact per analysis of
    a full dataset. See the design doc's background row for the measurements
    behind that, and for the one thing it gives up (an algorithm change to
    `estimate_background_profile` will not invalidate stored cells).
    """

    geometry: dict  # GeometrySettings, as a plain dict (small, dataset-wide, changes rarely)
    mask: MaskSnapshotRef | None
    chromatic: ChromaticSnapshotRef | None
    background: dict  # BackgroundSettings (the method), as a plain dict - see docstring; the computed profile is never stored
    reduction_method: str
    reference_exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE
    sample_exclusion: list | None = None
    """`sample_exclusion_digest(all_rois)`'s output when
    `reference_exclusion_mode` needs it, `None` otherwise - see that
    function's docstring for why this lives here rather than on
    `ProvenanceRecord`."""
    background_exclusion: dict | None = None
    """`background_exclusion_digest(...)`'s output - what the background
    estimate excluded, `None` when it excluded nothing. Unlike every other
    field this one is *omitted* from `as_json()` when `None` rather than
    written as `null`; see that function's docstring for why."""

    def as_json(self) -> dict:
        payload = {
            "geometry": self.geometry,
            "mask": None if self.mask is None else {
                "cube_index": self.mask.cube_index, "wavelength_nm": self.mask.wavelength_nm,
                "tag": self.mask.tag, "version": self.mask.version,
            },
            "chromatic": None if self.chromatic is None else {
                "cube_index": self.chromatic.cube_index, "wavelength_nm": self.chromatic.wavelength_nm,
                "version": self.chromatic.version,
            },
            "background": self.background,
            "reduction_method": self.reduction_method,
            "reference_exclusion_mode": self.reference_exclusion_mode,
            "sample_exclusion": self.sample_exclusion,
        }
        # Added 2026-09-23, and added *conditionally* on purpose: this
        # payload is compared key-for-key against already-written snapshot
        # files to dedup them (`persist_settings_snapshot`), so an
        # unconditional `"background_exclusion": null` would differ from
        # every file written before the field existed and mint a fresh
        # version - invalidating every stored cell of every analysis where
        # background flattening is off, i.e. exactly the analyses this
        # change cannot affect. See `background_exclusion_digest`.
        if self.background_exclusion is not None:
            payload["background_exclusion"] = self.background_exclusion
        return payload


def _existing_settings_versions(settings_dir: Path) -> dict[int, dict]:
    found: dict[int, dict] = {}
    if not settings_dir.is_dir():
        return found
    for path in settings_dir.glob("settings_v*.json"):
        suffix = path.stem[len("settings_v"):]
        if not suffix.isdigit():
            continue
        try:
            found[int(suffix)] = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return found


def persist_settings_snapshot(settings_dir: Path, snapshot: SettingsSnapshot) -> int:
    """Write `snapshot` as a new version, or return the version number of an
    already-identical existing one. Returns the plain version number (the
    snapshot's own id) - `ProvenanceRecord` stores these, not the full
    snapshot content."""
    payload = snapshot.as_json()
    existing = _existing_settings_versions(settings_dir)
    version, is_new = next_version(existing, payload, equal=_json_values_equal)
    if is_new:
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / f"settings_v{version}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return version


# -- the cell-level provenance record ----------------------------------------


def roi_geometry_fingerprint_fields(roi: AreaRoi) -> dict:
    """Just the geometry-affecting fields of `roi` - deliberately excludes
    metadata (score, label, notes, created_by, quality_score, inferred,
    support_*, array_id, per_wavelength) that doesn't affect where pixels
    are read from, matching the sketch's cosmetic-vs-computational split
    applied to a ROI's own fields, not just to other modules' settings.
    Shared by `compute_fingerprint`'s callers (`tasks.py`, `engine.py`) so
    there's exactly one definition of what counts as "this ROI's geometry"
    for provenance purposes."""
    return {
        "center_x": float(roi.center_x),
        "center_y": float(roi.center_y),
        "sample_radius_px": float(roi.sample_radius_px),
        "sample_geometry_type": roi.sample_geometry_type,
        "sample_mask": None if roi.sample_mask is None else {
            "x0": roi.sample_mask.x0, "y0": roi.sample_mask.y0, "mask": roi.sample_mask.mask.tolist(),
        },
        "sample_diameter_px": roi.sample_diameter_px,
        "reference_geometry_type": roi.reference_geometry_type,
        "reference_mask": None if roi.reference_mask is None else {
            "x0": roi.reference_mask.x0, "y0": roi.reference_mask.y0, "mask": roi.reference_mask.mask.tolist(),
        },
        "reference_inner_diameter_px": roi.reference_inner_diameter_px,
        "reference_outer_diameter_px": roi.reference_outer_diameter_px,
    }


@dataclass(frozen=True)
class ProvenanceRecord:
    """The complete set of inputs that produced one (ROI, cube) cell's
    stored spectrum. `roi_geometry` and `reduction_method` don't vary across
    a cube's wavelengths (ROI position is not time/wavelength-varying yet;
    reduction method is a session-wide choice); `per_wavelength_settings`
    can, since a mask/chromatic individual override is frame-specific -
    holds one settings-snapshot version id per wavelength actually present
    in this cell's spectrum."""

    roi_geometry: dict  # this ROI's own AreaRoi geometry fields, as a plain dict
    reduction_method: str
    per_wavelength_settings: tuple[tuple[float, int], ...]  # (wavelength_nm, settings_snapshot_version)


def compute_fingerprint(
    roi_geometry: dict,
    reduction_method: str,
    per_wavelength_settings: dict[float, SettingsSnapshot],
    settings_dir: Path,
) -> ProvenanceRecord:
    """Compute the current, live fingerprint for one (ROI, cube) cell -
    cheap: `per_wavelength_settings`'s snapshots are already-resolved plain
    data (the caller gathered them from the real modules), this only needs
    to resolve each into its version id via `persist_settings_snapshot`'s
    same dedup rule - but does **not** persist anything to `data.h5` or
    write mask/chromatic image files itself (those are written separately,
    by whichever code actually persists a computed cell - see
    `persist_mask_snapshot`/`persist_chromatic_snapshot`, called by the
    caller before this, once real content exists to snapshot).

    Note this still touches `settings_dir` (reads/writes the settings-
    snapshot JSON) - not literally zero I/O despite "cheap, no pixel
    access" framing inherited from the sketch's original wording; "cheap"
    means no image decode/raster/reduce work, not zero disk access.
    """
    resolved = tuple(
        sorted(
            (wavelength_nm, persist_settings_snapshot(settings_dir, snapshot))
            for wavelength_nm, snapshot in per_wavelength_settings.items()
        )
    )
    return ProvenanceRecord(
        roi_geometry=roi_geometry,
        reduction_method=reduction_method,
        per_wavelength_settings=resolved,
    )


class ProvenanceStore:
    """Documents the read-only interface :func:`~lspr_imaging_app.analysis.
    planner.plan_recompute` needs for its ``stored`` argument -
    ``fingerprint_for(roi_id, cube_index) -> ProvenanceRecord | None``.
    Not an ABC/Protocol (nothing enforces it at runtime - `plan_recompute`
    works with any duck-typed object that has the method, verified by its
    own tests using a hand-written fake with no inheritance from this
    class), just a documented shape.

    **Superseded by a different design (2026-09-22), not "blocked" -
    no separate `data.h5`-reading implementation of this class was ever
    built or is planned.** `data.h5` exists now (`store.py`), but the
    design that got built avoids ever reading it per-query at all: HDF5
    doesn't support safe concurrent cross-thread read/write, so
    `AnalysisEngine` instead bulk-loads everything into
    `InMemoryProvenanceStore` once at construction (`store.read_all_cells`)
    and answers every query from memory afterward - see `store.py`'s
    module docstring for the full reasoning. `InMemoryProvenanceStore` is
    the real, permanent implementation of this interface, not a temporary
    stand-in.
    """

    def fingerprint_for(self, roi_id: int, cube_index: int) -> ProvenanceRecord | None:
        raise NotImplementedError("not implemented and not planned - see class docstring")


class InMemoryProvenanceStore:
    """The real, permanent implementation of the `ProvenanceStore`
    interface - not a temporary stand-in (an earlier version of this
    docstring called it one, before `data.h5`/`store.py` existed and the
    write-through-plus-bulk-rehydration design was settled; see
    `ProvenanceStore`'s own docstring). Backed by a plain in-memory dict,
    populated either by `record()` (called by `AnalysisEngine` after a real
    `compute_cell` call) or by bulk-loading `data.h5`'s contents at
    construction (`store.read_all_cells`) - either way, nothing in this
    class touches a file itself.
    """

    def __init__(self) -> None:
        self._fingerprints: dict[tuple[int, int], ProvenanceRecord] = {}

    def fingerprint_for(self, roi_id: int, cube_index: int) -> ProvenanceRecord | None:
        return self._fingerprints.get((roi_id, cube_index))

    def record(self, roi_id: int, cube_index: int, fingerprint: ProvenanceRecord) -> None:
        self._fingerprints[(roi_id, cube_index)] = fingerprint

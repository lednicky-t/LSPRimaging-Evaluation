"""Analysis step 1, first half: turn the app's current state into the plain data a scan needs (:class:`ScanPlan`).

Design record: ``docs/analysis_redesign_plan_2026-10-19.md`` (sections 11-15). The scan itself is ``scan.py``; this file only
*resolves inputs*. It is the one place where "which ROI layout, which chromatic affine and which ignore mask belong to frame
(cube, wavelength)" is decided, so a wrong answer here would put a wrong number into every stored cell. The same decisions are
made by ``tools/reference_inputs.py`` (the input gathering of the former ``AnalysisEngine``) and ``tasks.compute_cell`` (the golden
reference); ``tools/scan_golden_check.py`` compares the two on real data.

This takes **callables, not module references**, so the full list of what a scan reads from the app is
visible in :class:`ScanSource`, and the builder is testable with plain fakes. No Qt import (workers and the shell use it).

Call it on the thread that owns the modules (the GUI thread): several module queries hand out live objects. The result is plain
data, safe to give to any thread or process, and a later edit in the app cannot change a running scan (the engine's problem P2).

What is deliberately handled here, not in the scan:

* the dark frame and other non-spectral frames are left out (``is_spectral``), as in ``_gather_wavelength_inputs``;
* the wavelength axis is the **union** over cubes (compared after rounding to 0.01 nm). Cubes with different wavelength sets are not
  expected; if it happens a missing frame stays empty in the result and a warning says how many cubes differ. Two frames of one cube
  that fall on the same rounded wavelength are an error, never silently merged;
* every cube must have the same ROI ids in the same order (the stored result has one ROI axis); otherwise ``ValueError``;
* ignore masks are resolved to processed space here (they need the module's mask and chromatic state) and **shared** between frames
  that use the same authored mask and the same warp, so one persistent mask costs one array per wavelength, not one per frame.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from ..image_tools.background.model import BackgroundSettings
from ..image_tools.geometry.model import GeometrySettings
from ..image_tools.preprocess import resolve_external_mask
from ..roi.model import AreaRoi, AreaRoiDetectionSettings
from .frame_reduce import RoiSet, build_roi_set
from .provenance import DEFAULT_REFERENCE_EXCLUSION_MODE
from .reduction import DEFAULT_TRIMMED_MEAN_FRACTION
from .scan import CubeJob, FrameJob, ScanSettings

logger = logging.getLogger(__name__)

WAVELENGTH_KEY_DECIMALS = 2  # two wavelengths closer than 0.01 nm are the same column of the result
IGNORE_MASK_WARN_BYTES = 2 * 1024**3  # a plan holding more ignore-mask arrays than this logs a warning (it still runs)

Frame = tuple[int, float]


@dataclass(frozen=True)
class MaskRef:
    """The ignore mask that applies at a frame, **not yet decoded**: masks are stored compressed (a few KB) and a full-frame array is
    1.2 MB, so the lookup is cheap (GUI thread) and ``load()`` (decode) is called later, on the thread that builds the plan."""

    authored_frame: Frame  # the frame the mask was drawn at
    scope: str  # "individual" | "persistent"
    load: Callable[[], np.ndarray]  # the mask as authored (raw image space), read-only
    digest: Callable[[], bytes] | None = None
    """Digest of the authored pixels, made without decoding them (`MaskChange.content_digest`). None = hash what ``load()`` returns."""


@dataclass(frozen=True)
class ScanSource:
    """Everything a scan reads from the app. Each field is one narrow read (plain callables, never module references)."""

    cube_indices: Callable[[], Sequence[int]]  # spectral cubes, in dataset order
    wavelengths_for_cube: Callable[[int], Sequence[float]]  # per cube, never the union (a cube may be short one wavelength)
    is_spectral: Callable[[int, float], bool]  # False for the dark frame and frames the user set aside
    record_path: Callable[[int, float], str]  # the plane's record path, as ``dataset.io.load_image_array_uncached`` takes it
    rois: Callable[[], Sequence[AreaRoi]]
    rois_at: Callable[[int], Sequence[AreaRoi]]  # ROIs as they are on that cube (differs by cube only with a geometry timeline)
    has_timeline: Callable[[], bool]
    chromatic_affine: Callable[[int, float], np.ndarray]  # 2x3, identity when there is no correction
    chromatic_affine_between: Callable[[Frame, Frame], np.ndarray]
    resolve_mask: Callable[[int, float], MaskRef | None]
    geometry: Callable[[], GeometrySettings]
    background: Callable[[], BackgroundSettings]
    detection: Callable[[], AreaRoiDetectionSettings]
    default_reference_diameters: Callable[[], tuple[float, float]]  # (inner, outer) px for a ROI without its own ring
    # Optional. ``cube_indices`` is what a scan covers by default; a snapshot of a cube subset sets this to every spectral cube of the dataset,
    # so the result file's cube and wavelength axes are the whole dataset's and a partial run fits the same file as a full one.
    all_cube_indices: Callable[[], Sequence[int]] | None = None
    frame_times_ms: Callable[[], Mapping[Frame, int]] | None = None  # (cube, wavelength) -> acquisition time, unix ms; missing = unknown


@dataclass(frozen=True)
class ScanPlan:
    """A whole scan as plain data. ``jobs[p]`` is cube ``cube_indices[p]``; its frames are placed on the result's wavelength axis by
    ``axis_positions[p]`` (frame ``k`` of the job -> column ``axis_positions[p][k]``). ``dataset_cubes`` is every spectral cube of the dataset
    (the result file's cube axis; ``cube_indices`` may be a subset of it) and ``acquired_at_unix_ms[p, column]`` the time of each frame (0 = unknown)."""

    settings: ScanSettings
    jobs: tuple[CubeJob, ...]
    cube_indices: tuple[int, ...]
    wavelength_axis_nm: tuple[float, ...]
    axis_positions: tuple[tuple[int, ...], ...]
    roi_ids: tuple[int, ...]
    warnings: tuple[str, ...] = field(default=())
    ignore_mask_bytes: int = 0  # memory held by the distinct ignore-mask arrays of this plan
    masks_resolved: bool = True  # False: made by ``build_scan_plan(resolve_masks=False)``: frames carry ``ignore_key`` but no mask array
    dataset_cubes: tuple[int, ...] = ()
    acquired_at_unix_ms: np.ndarray | None = None
    mask_resolver: Callable[[bytes], np.ndarray] | None = field(default=None, compare=False, repr=False)
    """``ignore_key`` of a frame -> its ignore mask in processed space (decoded, rotated and warped on demand, read-only). Lets the cell hash look at
    the part of a mask a ROI can see (`scan_fingerprint`) without the plan holding 400 MB of masks; None for a plan made without masks to resolve.
    Not sent to workers (only the jobs are) and not part of a plan's identity."""

    @property
    def n_frames(self) -> int:
        return sum(len(job.frames) for job in self.jobs)


def authored_mask_digest(mask: np.ndarray) -> bytes:
    """16-byte digest of a mask's pixels and shape; `MaskChange.content_digest` returns the same value for the same pixels without decoding them."""
    return hashlib.blake2b(np.packbits(np.ascontiguousarray(mask, dtype=bool)).tobytes() + str(tuple(mask.shape)).encode(), digest_size=16).digest()


def ignore_key_of(authored_digest: bytes, warp: np.ndarray | None) -> bytes:
    """What the processed-space ignore mask at a frame is made from: the authored pixels and the chromatic warp that carries them to the frame
    (the geometry that rotates and crops them is in the scan recipe). `scan_fingerprint.cell_hashes` hashes this, so the mask need not be built."""
    return hashlib.blake2b(authored_digest + (b"-" if warp is None else np.ascontiguousarray(warp, dtype=np.float64).tobytes()), digest_size=16).digest()


def _wl_key(wavelength_nm: float) -> float:
    return round(float(wavelength_nm), WAVELENGTH_KEY_DECIMALS)


def _dataset_cubes(source: ScanSource) -> tuple[int, ...]:
    return tuple(int(c) for c in (source.all_cube_indices if source.all_cube_indices is not None else source.cube_indices)())


def _choose_cubes(source: ScanSource, cubes: Sequence[int] | None) -> tuple[int, ...]:
    chosen = tuple(int(c) for c in source.cube_indices()) if cubes is None else tuple(int(c) for c in cubes)
    known = set(_dataset_cubes(source))
    unknown = [c for c in chosen if c not in known]
    if unknown:
        raise ValueError(f"cubes {unknown[:5]} are not spectral cubes of this dataset")
    return chosen


def build_scan_plan(
    source: ScanSource,
    *,
    cubes: Sequence[int] | None = None,
    methods: tuple[str, ...] = ("mean",),
    exclusion_mode: str = DEFAULT_REFERENCE_EXCLUSION_MODE,
    trimmed_fraction: float = DEFAULT_TRIMMED_MEAN_FRACTION,
    fast_tiff_decoder: bool = True,
    resolve_masks: bool = True,
) -> ScanPlan:
    """Resolve the inputs of a scan of ``cubes`` (default: every spectral cube).

    ``resolve_masks=False`` (the staleness check): the ignore masks are not decoded, rotated and warped (that was 3 s of the 3.9 s check on the
    real dataset, 411 masks, 400 MB); every frame gets its ``ignore_key`` instead, which is all the fingerprint reads. Such a plan can be
    hashed but not scanned (``scan_cubes`` refuses it).

    Always every ROI: the reference-ring exclusion uses **all** ROIs of a cube (a stored value must not depend on what else was
    selected), and the frame stage dominates the cost, so a caller who wants some ROIs keeps those rows of the result."""
    chosen = _choose_cubes(source, cubes)
    if not chosen:
        raise ValueError("nothing to scan: no cubes")

    geometry = source.geometry()
    background = source.background()
    detection = source.detection()
    inner, outer = source.default_reference_diameters()
    timeline = bool(source.has_timeline())

    # -- wavelength axis: union over EVERY cube of the dataset (so a partial scan fits the same result file as a full one) ----
    dataset_cubes = _dataset_cubes(source)
    frames_of: dict[int, list[float]] = {}
    axis_by_key: dict[float, float] = {}
    for cube in dataset_cubes:
        seen: dict[float, float] = {}
        for wl in source.wavelengths_for_cube(cube):
            if not source.is_spectral(cube, wl):
                continue
            key = _wl_key(wl)
            if key in seen:
                raise ValueError(
                    f"cube {cube} has two spectral frames at {key} nm ({seen[key]} and {float(wl)}): they would share one result column"
                )
            seen[key] = float(wl)
            axis_by_key.setdefault(key, float(wl))
        frames_of[cube] = list(seen.values())
    if not axis_by_key:
        raise ValueError("no spectral frames in the cubes to scan")
    axis_keys = sorted(axis_by_key)
    axis_index = {key: i for i, key in enumerate(axis_keys)}
    axis = tuple(axis_by_key[key] for key in axis_keys)

    warnings: list[str] = []
    differing = [c for c in dataset_cubes if tuple(_wl_key(w) for w in frames_of[c]) != tuple(axis_keys)]
    if differing:
        message = (
            f"{len(differing)} of {len(dataset_cubes)} cubes do not have the same spectral wavelengths as the result axis ({len(axis)} columns); "
            f"their missing frames stay empty (first such cube: {differing[0]})"
        )
        warnings.append(message)
        logger.warning(message)

    # -- ROIs: one RoiSet per distinct layout ------------------------------------------------------------------------------
    roi_sets: dict[int, RoiSet] = {}
    shared: RoiSet | None = None
    reference_ids: tuple[int, ...] | None = None
    for cube in chosen:
        if timeline or shared is None:
            current = build_roi_set(list(source.rois_at(cube) if timeline else source.rois()), inner, outer)
            if not timeline:
                shared = current
        else:
            current = shared
        ids = tuple(int(r.area_roi_id) for r in current.rois)
        if reference_ids is None:
            reference_ids = ids
        elif ids != reference_ids:
            raise ValueError(f"cube {cube} has ROI ids {ids[:6]}... but the first cube has {reference_ids[:6]}...: the ROI axis must be one list")
        roi_sets[cube] = current
    if not reference_ids:
        raise ValueError("nothing to scan: no ROIs")

    # -- frames: path, chromatic affine, ignore mask in processed space ----------------------------------------------------
    ignore_cache: dict[tuple, np.ndarray] = {}
    authored_digests: dict[tuple, bytes] = {}  # (authored frame, scope) -> digest of the authored pixels
    decoded: dict[tuple, np.ndarray] = {}  # (authored frame, scope) -> pixels, for a MaskRef that cannot make its digest without decoding
    mask_table: dict[bytes, tuple] = {}  # ignore_key -> (MaskRef, chromatic warp, digest of the authored pixels): what `mask_resolver` needs
    jobs: list[CubeJob] = []
    positions: list[tuple[int, ...]] = []
    for cube_pos, cube in enumerate(chosen):
        frames: list[FrameJob] = []
        columns: list[int] = []
        for wl in frames_of[cube]:
            frame: Frame = (cube, wl)
            affine = np.array(source.chromatic_affine(cube, wl), dtype=np.float64, copy=True)
            ignore = None
            ignore_key = None
            ref = source.resolve_mask(cube, wl)
            if ref is not None:
                authored_frame = (int(ref.authored_frame[0]), float(ref.authored_frame[1]))
                warp = None
                if authored_frame != frame:
                    # Only when the mask was authored at another frame; same rule as tools/reference_inputs.py.
                    warp = np.asarray(source.chromatic_affine_between(authored_frame, frame), dtype=np.float64)
                key = (authored_frame, ref.scope, None if warp is None else warp.tobytes())
                digest_key = (authored_frame, ref.scope)
                if digest_key not in authored_digests:
                    if ref.digest is not None:
                        authored_digests[digest_key] = ref.digest()
                    else:
                        decoded[digest_key] = ref.load()
                        authored_digests[digest_key] = authored_mask_digest(decoded[digest_key])
                ignore_key = ignore_key_of(authored_digests[digest_key], warp)
                if ignore_key not in mask_table:
                    mask_table[ignore_key] = (ref, warp, authored_digests[digest_key])
                if resolve_masks:
                    if key not in ignore_cache:
                        pixels = decoded.get(digest_key)
                        ignore_cache[key] = resolve_external_mask(
                            ref.load() if pixels is None else pixels, geometry, warp, content_key=authored_digests[digest_key]
                        )
                    ignore = ignore_cache[key]
            frames.append(FrameJob(str(source.record_path(cube, wl)), affine, ignore, ignore_key))
            columns.append(axis_index[_wl_key(wl)])
        jobs.append(CubeJob(cube_pos, roi_sets[cube], tuple(frames)))
        positions.append(tuple(columns))

    distinct = {id(a): a for a in ignore_cache.values() if a is not None}  # the resolver may already share arrays by content
    ignore_bytes = sum(int(a.nbytes) for a in distinct.values())
    if ignore_bytes > IGNORE_MASK_WARN_BYTES:
        message = f"the scan holds {len(distinct)} distinct ignore masks, {ignore_bytes / 1024**3:.1f} GB"
        warnings.append(message)
        logger.warning(message)

    times = source.frame_times_ms() if source.frame_times_ms is not None else {}
    acquired = np.zeros((len(chosen), len(axis)), dtype=np.int64)
    for p, cube in enumerate(chosen):
        for wl, column in zip(frames_of[cube], positions[p]):
            acquired[p, column] = int(times.get((cube, float(wl)), 0))

    settings = ScanSettings(geometry, background, detection, methods, exclusion_mode, trimmed_fraction, fast_tiff_decoder)
    return ScanPlan(
        settings, tuple(jobs), chosen, axis, tuple(positions), reference_ids, tuple(warnings), ignore_bytes,
        dataset_cubes=dataset_cubes, acquired_at_unix_ms=acquired, masks_resolved=resolve_masks,
        mask_resolver=_mask_resolver(mask_table, geometry) if mask_table else None,
    )


def _mask_resolver(table: dict[bytes, tuple], geometry: GeometrySettings) -> Callable[[bytes], np.ndarray]:
    """The plan's ``mask_resolver``: the processed-space ignore mask of a frame, made from its ``ignore_key`` (authored pixels, chromatic warp and
    the plan's geometry), the same way ``build_scan_plan`` makes the masks of a plan that resolves them."""

    def resolve(key: bytes) -> np.ndarray:
        ref, warp, digest = table[key]
        return resolve_external_mask(ref.load(), geometry, warp, content_key=digest)

    return resolve


def subset_plan(plan: ScanPlan, cube_indices: Sequence[int]) -> ScanPlan:
    """The same plan restricted to some of its cubes, in the order given (positions renumbered from 0; everything else unchanged)."""
    where = {cube: p for p, cube in enumerate(plan.cube_indices)}
    picked = [where[int(c)] for c in cube_indices]
    return replace(
        plan,
        jobs=tuple(replace(plan.jobs[p], cube_pos=i) for i, p in enumerate(picked)),
        cube_indices=tuple(plan.cube_indices[p] for p in picked),
        axis_positions=tuple(plan.axis_positions[p] for p in picked),
        acquired_at_unix_ms=None if plan.acquired_at_unix_ms is None else plan.acquired_at_unix_ms[picked],
    )


def source_from_modules(
    *,
    dataset,
    frame_roles,
    roi_toolbox,
    geometry,
    background,
    chromatic,
    mask,
    records: dict,
) -> ScanSource:
    """The :class:`ScanSource` of the live app modules (the same reads ``tools/reference_inputs.py`` makes for the golden reference).

    ``records`` is ``dataset.io.dataset_record_map(image_dataset)``: (cube, wavelength) -> record with a ``path``. Takes the modules by
    keyword so the call site names what it passes; nothing here keeps a module beyond the lifetime of the returned callables."""
    return ScanSource(
        cube_indices=dataset.spectral_cubes,
        wavelengths_for_cube=dataset.wavelengths_for_cube,
        is_spectral=frame_roles.is_spectral,
        record_path=lambda cube, wl: str(records[(cube, wl)].path),
        rois=roi_toolbox.rois,
        rois_at=roi_toolbox.rois_at,
        has_timeline=roi_toolbox.has_timeline,
        chromatic_affine=lambda cube, wl: chromatic.affine_for((cube, wl)),
        chromatic_affine_between=chromatic.affine_between,
        resolve_mask=lambda cube, wl: _mask_ref(mask.resolve_mask_change((cube, wl))),
        geometry=geometry.settings,
        background=background.settings,
        detection=roi_toolbox.detection_settings,
        default_reference_diameters=lambda: (
            roi_toolbox.detection_settings().reference_inner_diameter_px,
            roi_toolbox.detection_settings().reference_outer_diameter_px,
        ),
        frame_times_ms=dataset.frame_times_unix_ms,
    )


def _mask_ref(change) -> MaskRef | None:
    """A ``MaskModule`` change (immutable, pixels compressed) as a :class:`MaskRef`; decoding waits for ``load()``."""
    if change is None:
        return None
    return MaskRef((int(change.frame[0]), float(change.frame[1])), str(change.scope), lambda: change.mask, change.content_digest)


def snapshot_source(source: ScanSource, cubes: Sequence[int] | None = None) -> ScanSource:
    """Read everything a scan needs from ``source`` **now** and return a :class:`ScanSource` over plain tables.

    Call this on the thread that owns the modules (the GUI thread); it is a few hundred lookups of microseconds each. Afterwards
    :func:`build_scan_plan` can run on any thread: nothing it reads is a live module or a live ROI object (the ROIs are copied),
    so an edit made while the scan is being prepared or run cannot change it. The only deferred work is decoding the stored ignore
    masks (``MaskRef.load``), which is the expensive part and is safe off-thread because a ``MaskChange`` is immutable.

    ``cubes`` limits what is read (unknown cubes raise ``ValueError``, as in ``build_scan_plan``)."""
    chosen = _choose_cubes(source, cubes)
    timeline = bool(source.has_timeline())
    wavelengths: dict[int, tuple[float, ...]] = {}
    spectral: dict[tuple[int, float], bool] = {}
    paths: dict[tuple[int, float], str] = {}
    affines: dict[tuple[int, float], np.ndarray] = {}
    masks: dict[tuple[int, float], MaskRef | None] = {}
    warps: dict[tuple[Frame, Frame], np.ndarray] = {}
    dataset_cubes = _dataset_cubes(source)
    chosen_set = set(chosen)
    for cube in dataset_cubes:  # the wavelength axis of the result covers every cube; the details below only the chosen ones
        wavelengths[cube] = tuple(float(w) for w in source.wavelengths_for_cube(cube))
        for wl in wavelengths[cube]:
            is_spectral = bool(source.is_spectral(cube, wl))
            spectral[(cube, wl)] = is_spectral
            if not is_spectral or cube not in chosen_set:
                continue
            paths[(cube, wl)] = str(source.record_path(cube, wl))
            affines[(cube, wl)] = np.array(source.chromatic_affine(cube, wl), dtype=np.float64, copy=True)
            ref = source.resolve_mask(cube, wl)
            masks[(cube, wl)] = ref
            if ref is not None:
                authored = (int(ref.authored_frame[0]), float(ref.authored_frame[1]))
                if authored != (cube, wl):
                    warps[(authored, (cube, wl))] = np.array(source.chromatic_affine_between(authored, (cube, wl)), dtype=np.float64, copy=True)

    # One copy per live ROI object (rois_at hands out the same stored ROI for many cubes). The dict keeps the original alive next to its
    # copy: a temporary ROI that was freed could otherwise lend its id() to a different ROI and get the wrong copy.
    copies: dict[int, tuple[AreaRoi, AreaRoi]] = {}

    def copy_of(rois: Sequence[AreaRoi]) -> tuple[AreaRoi, ...]:
        return tuple(copies.setdefault(id(r), (r, copy.copy(r)))[1] for r in rois)

    shared_rois = () if timeline else copy_of(source.rois())
    rois_by_cube = {cube: copy_of(source.rois_at(cube)) for cube in chosen} if timeline else {}
    geometry, background, detection = source.geometry(), source.background(), source.detection()
    diameters = tuple(float(d) for d in source.default_reference_diameters())
    times = dict(source.frame_times_ms()) if source.frame_times_ms is not None else {}

    return ScanSource(
        cube_indices=lambda: chosen,
        wavelengths_for_cube=lambda cube: wavelengths[cube],
        is_spectral=lambda cube, wl: spectral[(cube, float(wl))],
        record_path=lambda cube, wl: paths[(cube, float(wl))],
        rois=lambda: shared_rois,
        rois_at=lambda cube: rois_by_cube[cube] if timeline else shared_rois,
        has_timeline=lambda: timeline,
        chromatic_affine=lambda cube, wl: affines[(cube, float(wl))],
        chromatic_affine_between=lambda a, b: warps[((int(a[0]), float(a[1])), (int(b[0]), float(b[1])))],
        resolve_mask=lambda cube, wl: masks[(cube, float(wl))],
        geometry=lambda: geometry,
        background=lambda: background,
        detection=lambda: detection,
        default_reference_diameters=lambda: diameters,
        all_cube_indices=lambda: dataset_cubes,
        frame_times_ms=lambda: times,
    )

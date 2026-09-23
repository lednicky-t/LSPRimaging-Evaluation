"""Pure function composing Geometry + Mask + Background into one processed
image (sketch §10: "ports processing/preprocess.py mostly as-is") - ports
``apply_preprocessing`` out of ``processing/preprocess.py`` verbatim, apart
from splitting its single ``settings: PreprocessingSettings`` parameter into
``geometry_settings``/``background_settings`` (see ``geometry/model.py``'s
docstring for why that split exists). Chromatic correction is not threaded
through here: the ported function never read any ``chromatic_*`` field, so
there was nothing to split out for it - chromatic warping happens elsewhere
(``ChromaticModule.affine_for``/``warp_mask``), not inside this pipeline.

**Real placeholder mismatch caught during the 2026-09-20 port**: the
scaffold's guessed function here was ``preprocess_image(raw_image, *,
geometry_settings, mask, chromatic_affine, background_model)`` - a name and
shape nothing in the app actually calls. The real, widely-used function is
``apply_preprocessing`` with the signature below (see e.g.
``gui/analysis_tasks.py``, ``gui/main_window.py`` on ``develop``/``main``).

No Qt import allowed in this file (AGENTS.md testing rule).
"""

from __future__ import annotations

import logging
import time

import numpy as np

from ..roi.model import AreaRoi, AreaRoiDetectionSettings
from .background.estimate import flatten_background
from .background.model import BackgroundSettings
from .chromatic.warp import warp_boolean_mask_affine
from .geometry.model import GeometrySettings
from .geometry.transform import (
    apply_spatial_mask,
    apply_spatial_preprocessing,
    rotation_fill_pixel_mask,
)
from .mask.model import MaskSettings

_LOGGER = logging.getLogger(__name__)


def resolve_external_mask(
    authored_mask: np.ndarray | None,
    geometry_settings: GeometrySettings,
    warp_affine: np.ndarray | None = None,
) -> np.ndarray | None:
    """Turn an ignore mask **as authored** (raw image space, per
    `MaskModule.resolve_mask_source`) into the processed-space mask that
    `apply_preprocessing` should be handed with
    ``external_mask_processed=True``.

    Two steps, and the order is the whole point (see the 2026-09-23 build
    log entry): crop/rotate/flip the mask exactly as the image itself will
    be transformed, *then* apply the chromatic warp - because the chromatic
    affine is expressed in **processed** image space, so warping a
    raw-space mask with it lands the mask somewhere meaningless whenever a
    crop or rotation is active.

    `warp_affine` is `ChromaticModule.affine_between(authored_frame, frame)`
    and is only needed when the mask was authored at a different frame than
    the one being rendered; `None` means no re-registration, which is the
    common case. Taken as a plain matrix rather than this module reaching
    into `ChromaticModule`, the same one-directional convention
    `roi/rasterize.py` follows.

    Lives here, rather than privately inside `analysis/tasks.py` where it
    started, so the Image panel renders against exactly the mask analysis
    computes against - the two agreeing by construction rather than by two
    call sites happening to stay in step."""
    if authored_mask is None:
        return None
    mask = apply_spatial_mask(np.asarray(authored_mask, dtype=bool), geometry_settings)
    if mask is None:
        return None
    if warp_affine is not None:
        mask = warp_boolean_mask_affine(mask, warp_affine)
    return mask


def apply_preprocessing(
    image: np.ndarray,
    geometry_settings: GeometrySettings,
    background_settings: BackgroundSettings,
    rois: list[AreaRoi] | None = None,
    mask_settings: AreaRoiDetectionSettings | None = None,
    external_mask: np.ndarray | None = None,
    external_mask_processed: bool = False,
    mask_state: MaskSettings | None = None,
    region: tuple[int, int, int, int] | None = None,
    skip_crop: bool = False,
    log_stage_timing: bool = False,
) -> np.ndarray:
    """`region` (x0, y0, x1, y1), if given, only affects what's *returned* —
    masking and the rotate/flip/crop step always run on the whole image
    (spatial alignment and, when background flattening is on, the background
    estimate itself both need full-image context regardless of region). When
    background flattening is on, `region` is passed through to
    flatten_background to skip materializing/upsampling values nobody reads;
    otherwise the full processed image is just sliced before returning.

    `skip_crop`, if set, applies rotation/flip as usual but skips the final
    crop slice — used for the live display while the crop/rotate tool is
    open, so the crop rectangle is always drawn over the same rotated/flipped
    canvas its coordinates are defined against (see the CLAUDE.md pitfall on
    `image_tools_enabled`: getting this canvas wrong is what causes crop
    settings to compound into an over-cropped image on session restore).

    `log_stage_timing`, if set, emits one DEBUG log line breaking the call
    down into mask/spatial-transform/flatten-background time (stays invisible
    in the console unless Debug mode is on - see
    workflow_log_controller.append_workflow_log_entry - but always reaches
    the session log file, matching every other DEBUG line). Opt-in and
    defaults off: this function also runs inside tight per-frame loops (batch
    sensorgram/absorbance calculations, patch-based ROI reads), where a log
    line per call would flood the log for no benefit - only the single-image
    display refresh path (_process_image_task) passes True.
    """
    stage_started_at = time.perf_counter() if log_stage_timing else 0.0

    # Apply mask to raw image before spatial preprocessing
    masked_image = image

    # Apply new mask system to raw image
    combined_mask = None
    if mask_state is not None:
        if mask_state.histogram_enabled and mask_state.histogram_mask is not None:
            if combined_mask is None:
                combined_mask = mask_state.histogram_mask.copy()
            else:
                combined_mask |= mask_state.histogram_mask

        if mask_state.figure_enabled and mask_state.figure_mask is not None:
            if combined_mask is None:
                combined_mask = mask_state.figure_mask.copy()
            else:
                combined_mask |= mask_state.figure_mask

    # Apply combined new masks
    if combined_mask is not None:
        masked_image = np.where(combined_mask.astype(bool), 0, masked_image)

    # Apply legacy external mask if provided, in raw image coordinates
    if external_mask is not None and not external_mask_processed:
        masked_image = np.where(external_mask.astype(bool), 0, masked_image)

    if log_stage_timing:
        mask_elapsed = time.perf_counter() - stage_started_at
        stage_started_at = time.perf_counter()

    processed = apply_spatial_preprocessing(masked_image, geometry_settings, skip_crop=skip_crop)

    # A pre-transformed external mask (caller already rotated/flipped/cropped
    # it to match `processed`, e.g. via apply_spatial_mask) can't be applied
    # before the spatial transform above like the raw-space case is - do it
    # here instead. Previously this branch did nothing: the mask was neither
    # zeroed into `processed` nor forwarded to flatten_background, so a
    # processed-space mask (the normal case - every caller resolves masks via
    # processed_space=True) silently had no effect on background flattening,
    # letting masked-out regions (e.g. rotation-fill black edges) still pull
    # on the local background average.
    processed_exclusion_mask = None
    if external_mask is not None and external_mask_processed:
        candidate = np.asarray(external_mask, dtype=bool)
        if candidate.shape == processed.shape[:2]:
            processed_exclusion_mask = candidate
            processed = np.where(processed_exclusion_mask, 0, processed)

    if log_stage_timing:
        spatial_elapsed = time.perf_counter() - stage_started_at

    if background_settings.flatten_background_enabled:
        if log_stage_timing:
            stage_started_at = time.perf_counter()
        # Rotation-fill pixels (see rotation_fill_pixel_mask) are excluded from
        # the background estimate unconditionally - unlike the mask/ROI
        # exclusions above, this isn't optional curation the
        # flatten_background_exclude_mask toggle should gate: those pixels
        # never had a real measurement, so letting them pull on the local
        # background average is never correct, toggle or not.
        rotation_mask = rotation_fill_pixel_mask(image.shape[:2], geometry_settings, skip_crop=skip_crop)
        processed = flatten_background(
            processed,
            sigma_px=float(background_settings.flatten_background_sigma_px),
            binning=max(int(getattr(background_settings, "flatten_background_binning", 2)), 1),
            rois=rois if background_settings.flatten_background_exclude_area_rois else None,
            mask_settings=mask_settings if background_settings.flatten_background_exclude_mask else None,
            external_mask=processed_exclusion_mask if background_settings.flatten_background_exclude_mask else None,
            rotation_fill_mask=rotation_mask,
            region=region,
            exclusion_dilation_px=int(getattr(background_settings, "flatten_background_exclusion_dilation_px", 0)),
        )
        if log_stage_timing:
            flatten_elapsed = time.perf_counter() - stage_started_at
            _LOGGER.debug(
                "Image process stages | mask=%.0fms spatial=%.0fms flatten=%.0fms total=%.0fms | rois=%d",
                mask_elapsed * 1000.0, spatial_elapsed * 1000.0, flatten_elapsed * 1000.0,
                (mask_elapsed + spatial_elapsed + flatten_elapsed) * 1000.0,
                len(rois) if rois else 0,
            )
        return processed

    if log_stage_timing:
        _LOGGER.debug(
            "Image process stages | mask=%.0fms spatial=%.0fms flatten=off total=%.0fms",
            mask_elapsed * 1000.0, spatial_elapsed * 1000.0, (mask_elapsed + spatial_elapsed) * 1000.0,
        )

    if region is not None:
        x0, y0, x1, y1 = region
        return processed[y0:y1, x0:x1]
    return processed

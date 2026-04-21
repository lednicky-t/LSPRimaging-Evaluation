from __future__ import annotations

import numpy as np

from lspr_imaging_app.domain.models import MaskSettings, SpotDetectionSettings
from lspr_imaging_app.processing.preprocess import apply_spatial_mask
from lspr_imaging_app.processing.spot_detection import ignored_pixel_mask


def combine_mask_layers(mask_state: MaskSettings | None, shape: tuple[int, int]) -> np.ndarray | None:
    if mask_state is None:
        return None
    layers: list[np.ndarray] = []
    if mask_state.histogram_enabled and mask_state.histogram_mask is not None:
        layers.append(mask_state.histogram_mask.astype(bool, copy=False))
    if mask_state.figure_enabled and mask_state.figure_mask is not None:
        layers.append(mask_state.figure_mask.astype(bool, copy=False))
    if not layers:
        return None
    combined = np.zeros(shape, dtype=bool)
    for layer in layers:
        combined |= layer
    return combined


def apply_mask_layers(image: np.ndarray, mask_state: MaskSettings | None) -> np.ndarray:
    combined = combine_mask_layers(mask_state, image.shape[:2])
    if combined is None:
        return image
    return np.where(combined, 0, image)


def transformed_mask(mask: np.ndarray | None, settings) -> np.ndarray | None:
    return apply_spatial_mask(mask, settings)


def ignored_mask_from_detection(
    image: np.ndarray,
    detection: SpotDetectionSettings,
    *,
    spots=None,
    external_mask=None,
) -> np.ndarray:
    return ignored_pixel_mask(image, detection, spots=spots, external_mask=external_mask)


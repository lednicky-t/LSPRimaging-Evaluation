from __future__ import annotations

from functools import lru_cache

import numpy as np

from lspr_imaging_app.domain.models import RoiDefinition


def _roi_cache_key(roi: RoiDefinition) -> tuple[object, ...]:
    return (
        roi.shape,
        round(float(roi.center_x), 3),
        round(float(roi.center_y), 3),
        round(float(roi.size_x), 3),
        round(float(roi.size_y), 3),
        round(float(roi.background_padding_px), 3),
        round(float(roi.background_width_px), 3),
    )


@lru_cache(maxsize=512)
def _cached_roi_masks(image_shape: tuple[int, int], roi_key: tuple[object, ...]) -> tuple[np.ndarray, np.ndarray]:
    shape, cx, cy, sx, sy, background_padding_px, background_width_px = roi_key
    yy, xx = np.indices(image_shape, dtype=np.float32)
    cx = float(cx)
    cy = float(cy)
    sx = max(float(sx), 2.0)
    sy = max(float(sy), 2.0)
    background_padding_px = float(background_padding_px)
    background_width_px = float(background_width_px)

    if shape == "rectangle":
        inner = (
            (np.abs(xx - cx) <= sx / 2.0)
            & (np.abs(yy - cy) <= sy / 2.0)
        )
        outer = (
            (np.abs(xx - cx) <= (sx / 2.0 + background_padding_px + background_width_px))
            & (np.abs(yy - cy) <= (sy / 2.0 + background_padding_px + background_width_px))
        )
        gap = (
            (np.abs(xx - cx) <= (sx / 2.0 + background_padding_px))
            & (np.abs(yy - cy) <= (sy / 2.0 + background_padding_px))
        )
    else:
        rx = sx / 2.0
        ry = sy / 2.0
        inner_metric = ((xx - cx) / max(rx, 1e-6)) ** 2 + ((yy - cy) / max(ry, 1e-6)) ** 2
        outer_metric = ((xx - cx) / max(rx + background_padding_px + background_width_px, 1e-6)) ** 2 + (
            (yy - cy) / max(ry + background_padding_px + background_width_px, 1e-6)
        ) ** 2
        gap_metric = ((xx - cx) / max(rx + background_padding_px, 1e-6)) ** 2 + (
            (yy - cy) / max(ry + background_padding_px, 1e-6)
        ) ** 2
        inner = inner_metric <= 1.0
        outer = outer_metric <= 1.0
        gap = gap_metric <= 1.0

    background = outer & ~gap
    return inner, background


def roi_masks(image_shape: tuple[int, int], roi: RoiDefinition) -> tuple[np.ndarray, np.ndarray]:
    return _cached_roi_masks(image_shape, _roi_cache_key(roi))


def region_means(image: np.ndarray, roi: RoiDefinition) -> tuple[float, float]:
    roi_mask, background_mask = roi_masks(image.shape, roi)
    roi_values = image[roi_mask]
    background_values = image[background_mask]
    if roi_values.size == 0:
        raise ValueError(f"ROI {roi.name} has no pixels.")
    if background_values.size == 0:
        raise ValueError(f"ROI {roi.name} background region has no pixels.")
    return float(np.mean(roi_values)), float(np.mean(background_values))


def roi_outline_points(roi: RoiDefinition, scale_x: float = 1.0, scale_y: float = 1.0, samples: int = 80) -> tuple[np.ndarray, np.ndarray]:
    cx = float(roi.center_x)
    cy = float(roi.center_y)
    sx = max(float(roi.size_x) * scale_x, 2.0)
    sy = max(float(roi.size_y) * scale_y, 2.0)

    if roi.shape == "rectangle":
        left = cx - sx / 2.0
        right = cx + sx / 2.0
        top = cy - sy / 2.0
        bottom = cy + sy / 2.0
        xs = np.asarray([left, right, right, left, left], dtype=np.float64)
        ys = np.asarray([top, top, bottom, bottom, top], dtype=np.float64)
        return xs, ys

    t = np.linspace(0.0, 2.0 * np.pi, max(samples, 24), endpoint=True)
    xs = cx + (sx / 2.0) * np.cos(t)
    ys = cy + (sy / 2.0) * np.sin(t)
    return xs.astype(np.float64), ys.astype(np.float64)


def background_outline_points(roi: RoiDefinition) -> tuple[tuple[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]:
    pad_scale_x = (roi.size_x + 2.0 * roi.background_padding_px) / max(roi.size_x, 1e-6)
    pad_scale_y = (roi.size_y + 2.0 * roi.background_padding_px) / max(roi.size_y, 1e-6)
    outer_scale_x = (roi.size_x + 2.0 * (roi.background_padding_px + roi.background_width_px)) / max(roi.size_x, 1e-6)
    outer_scale_y = (roi.size_y + 2.0 * (roi.background_padding_px + roi.background_width_px)) / max(roi.size_y, 1e-6)
    inner_bg = roi_outline_points(roi, scale_x=pad_scale_x, scale_y=pad_scale_y)
    outer_bg = roi_outline_points(roi, scale_x=outer_scale_x, scale_y=outer_scale_y)
    return inner_bg, outer_bg

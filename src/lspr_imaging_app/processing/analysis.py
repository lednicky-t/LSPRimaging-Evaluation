from __future__ import annotations

import numpy as np

from lspr_imaging_app.domain.models import FitResult, ImageDataset, RoiDefinition, RoiMetricSeries, RoiSpectrum
from lspr_imaging_app.io.dataset import dataset_record_map, load_image_array
from lspr_imaging_app.processing.roi import region_means


def absorbance_from_means(roi_mean: float, background_mean: float) -> float:
    roi_mean = max(float(roi_mean), 1e-9)
    background_mean = max(float(background_mean), 1e-9)
    return float(np.log10(background_mean / roi_mean))


def extract_roi_spectrum(dataset: ImageDataset, roi: RoiDefinition, spectral_cube_index: int) -> RoiSpectrum:
    wavelengths = []
    absorbance = []
    roi_means = []
    bg_means = []
    record_map = dataset_record_map(dataset)
    for wl in dataset.wavelengths_nm:
        record = record_map.get((int(spectral_cube_index), float(wl)))
        if record is None:
            continue
        image = load_image_array(str(record.path))
        roi_mean, bg_mean = region_means(image, roi)
        wavelengths.append(wl)
        roi_means.append(roi_mean)
        bg_means.append(bg_mean)
        absorbance.append(absorbance_from_means(roi_mean, bg_mean))
    return RoiSpectrum(
        roi_name=roi.name,
        spectral_cube_index=spectral_cube_index,
        wavelengths_nm=np.asarray(wavelengths, dtype=np.float64),
        absorbance=np.asarray(absorbance, dtype=np.float64),
        roi_mean=np.asarray(roi_means, dtype=np.float64),
        background_mean=np.asarray(bg_means, dtype=np.float64),
    )


def fit_absorbance_curve(
    wavelengths_nm,
    absorbance,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
    sample_count: int = 400,
) -> FitResult:
    x = np.asarray(wavelengths_nm, dtype=np.float64)
    y = np.asarray(absorbance, dtype=np.float64)
    valid_mask = np.isfinite(x) & np.isfinite(y)
    x = x[valid_mask]
    y = y[valid_mask]

    if wl_min is not None:
        mask = x >= float(wl_min)
        x = x[mask]
        y = y[mask]
    if wl_max is not None:
        mask = x <= float(wl_max)
        x = x[mask]
        y = y[mask]

    if x.size < 2:
        return FitResult(x.copy(), y.copy(), np.array([]), None, None, None)

    order = np.argsort(x)
    x = x[order]
    y = y[order]

    effective_order = min(max(int(poly_order), 1), max(int(x.size) - 1, 1))
    polynomial = np.polynomial.Polynomial.fit(x, y, effective_order).convert()
    x_min = float(np.min(x))
    x_max = float(np.max(x))

    if x_max <= x_min:
        fitted_x = np.asarray([x_min], dtype=np.float64)
    else:
        fitted_x = np.linspace(x_min, x_max, max(int(sample_count), int(x.size)))
    fitted_y = np.asarray(polynomial(fitted_x), dtype=np.float64)

    derivative = polynomial.deriv()
    critical_points = derivative.roots()
    critical_points = critical_points.real[np.abs(critical_points.imag) < 1.0e-8]
    critical_points = critical_points[(critical_points >= x_min) & (critical_points <= x_max)]
    candidate_x = np.concatenate(
        [
            np.asarray([x_min, x_max], dtype=np.float64),
            np.asarray(critical_points, dtype=np.float64),
        ]
    )
    candidate_y = np.asarray(polynomial(candidate_x), dtype=np.float64)
    peak_wavelength = None
    peak_absorbance = None
    finite_candidate_mask = np.isfinite(candidate_x) & np.isfinite(candidate_y)
    if np.any(finite_candidate_mask):
        candidate_x = candidate_x[finite_candidate_mask]
        candidate_y = candidate_y[finite_candidate_mask]
        peak_index = int(np.argmax(candidate_y))
        peak_wavelength = float(candidate_x[peak_index])
        peak_absorbance = float(candidate_y[peak_index])

    centroid = None
    integral = polynomial.integ()
    weighted_integral = (polynomial * np.polynomial.Polynomial([0.0, 1.0])).integ()
    area = float(integral(x_max) - integral(x_min))
    if np.isfinite(area) and abs(area) > 1.0e-12:
        weighted_area = float(weighted_integral(x_max) - weighted_integral(x_min))
        centroid_value = weighted_area / area
        if np.isfinite(centroid_value):
            centroid = float(np.clip(centroid_value, x_min, x_max))

    return FitResult(
        fitted_wavelengths_nm=np.asarray(fitted_x, dtype=np.float64),
        fitted_absorbance=np.asarray(fitted_y, dtype=np.float64),
        coefficients=np.asarray(polynomial.coef, dtype=np.float64),
        peak_wavelength_nm=peak_wavelength,
        centroid_nm=centroid,
        peak_absorbance=peak_absorbance,
    )


def fit_roi_spectrum(
    spectrum: RoiSpectrum,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> FitResult:
    return fit_absorbance_curve(
        spectrum.wavelengths_nm,
        spectrum.absorbance,
        poly_order=poly_order,
        wl_min=wl_min,
        wl_max=wl_max,
    )


def metric_value_from_fit(fit: FitResult, metric_key: str) -> tuple[float | None, float | None]:
    key = str(metric_key).strip().lower()
    if key == "maximum":
        return fit.peak_wavelength_nm, fit.peak_absorbance
    if key == "centroid":
        if fit.centroid_nm is None:
            return None, None
        if fit.fitted_wavelengths_nm.size and fit.fitted_absorbance.size:
            centroid_y = float(np.interp(fit.centroid_nm, fit.fitted_wavelengths_nm, fit.fitted_absorbance))
        else:
            centroid_y = None
        return fit.centroid_nm, centroid_y
    return None, None


def extract_metric_series(
    dataset: ImageDataset,
    roi: RoiDefinition,
    poly_order: int = 3,
    wl_min: float | None = None,
    wl_max: float | None = None,
) -> RoiMetricSeries:
    spectral_cubes = []
    peak_wl = []
    centroid = []
    peak_abs = []
    for spectral_cube_index in dataset.spectral_cube_indices:
        spectrum = extract_roi_spectrum(dataset, roi, spectral_cube_index)
        fit = fit_roi_spectrum(spectrum, poly_order=poly_order, wl_min=wl_min, wl_max=wl_max)
        spectral_cubes.append(spectral_cube_index)
        peak_wl.append(np.nan if fit.peak_wavelength_nm is None else fit.peak_wavelength_nm)
        centroid.append(np.nan if fit.centroid_nm is None else fit.centroid_nm)
        peak_abs.append(np.nan if fit.peak_absorbance is None else fit.peak_absorbance)
    return RoiMetricSeries(
        roi_name=roi.name,
        spectral_cube_indices=np.asarray(spectral_cubes, dtype=np.int32),
        peak_wavelength_nm=np.asarray(peak_wl, dtype=np.float64),
        centroid_nm=np.asarray(centroid, dtype=np.float64),
        peak_absorbance=np.asarray(peak_abs, dtype=np.float64),
    )


def export_roi_series_csv(dataset: ImageDataset, rois: list[RoiDefinition], destination) -> None:
    import csv

    rows = []
    for roi in rois:
        series = extract_metric_series(dataset, roi)
        for spectral_cube_index, peak_wl, centroid, peak_abs in zip(
            series.spectral_cube_indices,
            series.peak_wavelength_nm,
            series.centroid_nm,
            series.peak_absorbance,
        ):
            rows.append([roi.name, int(spectral_cube_index), peak_wl, centroid, peak_abs])

    with open(destination, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["roi", "spectral_cube_index", "peak_wavelength_nm", "centroid_nm", "peak_absorbance"])
        writer.writerows(rows)

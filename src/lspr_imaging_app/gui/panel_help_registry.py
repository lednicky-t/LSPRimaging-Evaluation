from __future__ import annotations

from dataclasses import dataclass

from lspr_imaging_app.gui.shortcut_registry import shortcuts_text_for_panel


@dataclass(frozen=True)
class PanelHelpEntry:
    title: str
    description: str
    shortcut_keys: tuple[str, ...] = ()

    def text(self) -> str:
        parts = [
            self.title,
            "What it does",
            self.description,
        ]
        if self.shortcut_keys:
            parts.append("")
            parts.append("Shortcuts")
            parts.extend(shortcuts_text_for_panel(key) for key in self.shortcut_keys)
        return "\n".join(parts)


PANEL_HELP: dict[str, PanelHelpEntry] = {
    "dataset": PanelHelpEntry(
        title="Dataset",
        description=(
            "Browse for or type a dataset folder, then load it. The icon next to the title shows whether the "
            "loaded data is a raw ImageStack or an OME-Zarr stack. Nested tabs below hold the dataset Summary, "
            "Reference selection, Export controls, and acquisition Metadata."
        ),
        shortcut_keys=("dataset",),
    ),
    "summary": PanelHelpEntry(
        title="Summary",
        description=(
            "Read-only overview of the loaded dataset: image/wavelength counts, size on disk, resolution, and, "
            "for an OME-Zarr stack, its chunking, compression, dtype, and the rotation/flip/crop it was exported with."
        ),
    ),
    "reference": PanelHelpEntry(
        title="Reference",
        description=(
            "Choose how the reference image is selected and stored in the processing profile: Auto picks the best "
            "wavelength in the current spectral cube; Manual stores the current spectral cube and wavelength."
        ),
    ),
    "export": PanelHelpEntry(
        title="Export",
        description=(
            "Write the current dataset to an OME-Zarr stack: chunk size, sharding (one image or one spectral cube "
            "per file), compression, and whether to skip excluded images. Progress shows here once export starts.\n"
            "Pixel data is always written as uint16 (matching the source 16-bit TIFF data) and pyramid levels are "
            "not written - neither is currently configurable."
        ),
    ),
    "metadata": PanelHelpEntry(
        title="Metadata",
        description=(
            "Camera/illumination setup and per-image acquisition time for this dataset - what links each "
            "spectral cube to when it was actually recorded. Loaded automatically when a native measurement "
            "file or a legacy measureing_times.csv/metaData.txt pair is found next to the dataset; the Import "
            "button reads any mix of those, identified by content rather than filename (multiple files at "
            "once are fine). Export writes the currently loaded metadata as a JSON file - re-importing that "
            "same file restores it exactly, including any edits made in Preview / edit. The line below the "
            "status shows the acquisition time (and pump-plan comment, if any) for whichever image is "
            "currently displayed."
        ),
    ),
    "chromatic": PanelHelpEntry(
        title="Chromatic correction",
        description=(
            "Align sampled wavelengths to the reference image, then edit or preview the reference points.\n"
            "1. Choose 3/5/7 spectral images and 5, 15, or 30 reference points.\n"
            "2. Press Edit to enter point-edit mode on the current sampled image.\n"
            "3. Use PageUp/PageDown for reference points and Shift+PageUp/PageDown for sampled images.\n"
            "4. Estimate chromatic transforms or clear saved chromatic transforms from the wand control.\n"
            "When linked, the all-points preview shows the transformed landmark positions in the current image space."
        ),
        shortcut_keys=("chromatic",),
    ),
    "mask": PanelHelpEntry(
        title="Mask",
        description=(
            "Build one editable mask. Histogram, relative, local-contrast, and morphology tools can preview pixels in blue "
            "and then add or subtract them from the stored mask."
        ),
        shortcut_keys=("main",),
    ),
    "transforms": PanelHelpEntry(
        title="Transforms",
        description=(
            "Control manual rotation, flipping, cropping, and measurement/calibration after chromatic correction has been set up."
        ),
        shortcut_keys=("measure",),
    ),
    "roi_editor": PanelHelpEntry(
        title="ROI editor",
        description=(
            "Control circular ROI detection, rectangle ROI editing, move/add/remove, grouping, and the array-based ordering tools."
        ),
        shortcut_keys=("roi_editor", "array_detect"),
    ),
    "array_detect": PanelHelpEntry(
        title="Array detect",
        description="Reorder detected ROIs into the expected rectangular array layout.",
        shortcut_keys=("array_detect",),
    ),
    "background": PanelHelpEntry(
        title="Background removal",
        description="Estimate a smooth background and subtract it before downstream processing.",
        shortcut_keys=("main",),
    ),
    "analysis": PanelHelpEntry(
        title="Analysis",
        description="Calculate spectra and sensorgrams for the selected ROIs.",
        shortcut_keys=("table",),
    ),
    "spectra_fitting": PanelHelpEntry(
        title="Spectra fitting",
        description=(
            "Choose the curve fit, its order, and the extraction metric used to compute each ROI's absorbance value "
            "from its spectrum.\n"
            "Formula: A = log10(I_rROI / I_sROI) - absorbance from the sample ROI's mean intensity (I_sROI) and the "
            "reference ROI's mean intensity (I_rROI).\n"
            "Metric: Maximum and Centroid both work with or without a fit. Fitting: None reads the metric straight "
            "off the raw absorbance spectrum - Maximum is the highest raw point, Centroid its intensity-weighted "
            "center wavelength. Poly fits a polynomial of the chosen Order to the absorbance spectrum first and "
            "reads the metric off that smoothed curve instead. Gauss is a placeholder for a future Gaussian fit - "
            "not implemented yet.\n"
            "Tips:\n"
            "- The current fitted result shows above the spectrum plot, not in this panel.\n"
            "- A higher polynomial order follows noise more closely; a lower order is smoother but can miss a sharp peak.\n"
            "- Use the Range row above (Spectra) to crop the wavelength window before fitting, e.g. to exclude a noisy edge."
        ),
    ),
    "statistics": PanelHelpEntry(
        title="Statistics",
        description="Averaging, smoothing, and axis-selection tools for post-processing computed spectra and sensorgrams. Not implemented yet.",
    ),
    "results_export": PanelHelpEntry(
        title="Results / Export",
        description="Sensorgram region and kinetics tools, and export of ROI values, spectra, and sensorgram metrics. Not implemented yet.",
    ),
    "logs": PanelHelpEntry(
        title="Logs",
        description=(
            "Shows runtime messages, cache hits, calculation timing, and errors.\n"
            "DEBUG = technical details; INFO = normal progress; SUCCESS = finished correctly;\n"
            "WARNING = something unusual but recoverable; ERROR / CRITICAL = failures.\n"
            "Use copy to export the text and auto-scroll to follow the newest entry."
        ),
        shortcut_keys=("main",),
    ),
}


def panel_help_text(panel_key: str) -> str:
    entry = PANEL_HELP.get(panel_key, PANEL_HELP["analysis"])
    return entry.text()

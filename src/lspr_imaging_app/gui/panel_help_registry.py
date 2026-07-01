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
            "Load a dataset, choose the reference mode, and move through frames and wavelengths.\n"
            "The summary block shows whether the current data is a raw ImageStack or a Stack to Zarr.\n"
            "Reference mode chooses how the reference image is selected and stored in the processing profile."
        ),
        shortcut_keys=("dataset",),
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
    "image_tools": PanelHelpEntry(
        title="Image tools",
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

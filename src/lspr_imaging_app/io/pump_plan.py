"""Find the pump-plan CSV that belongs to a dataset and attach the experiment steps to its acquisition metadata.

The plan file (`<run name>.csv`, written by the pump-control software; reader: `lspr_io.read_pump_plan_csv`) holds
what the pump and valves were told to do per step. The measured steps themselves come from the notes in
`measureing_times.csv` (`lspr_core.build_imaging_plan` joins the two, see its module docstring). Without a plan
file the steps are still built from the notes, just without device settings.
"""

from __future__ import annotations

import logging
from pathlib import Path

from lspr_core import ImagingAcquisitionMetadata, build_imaging_plan, end_of_data_unix_ms
from lspr_io import is_pump_plan_csv, read_pump_plan_csv

logger = logging.getLogger(__name__)

# Same reach as the legacy `measureing_times.csv` search (`legacy_metadata.find_legacy_metadata_files`): the files
# can sit 1-3 folders above the images / OME-Zarr folder the dataset was opened from.
_MAX_LEVELS_UP = 3
_PLAN_SUFFIXES = {".csv", ".txt"}


def find_pump_plan_csv(dataset_folder: Path) -> Path | None:
    """The pump-plan CSV for *dataset_folder*, searched in the folder and a few ancestors. In one folder, a file
    named like the folder wins; otherwise a single plan file is used; several plan files with no clear winner are
    left alone (and logged) instead of picking one at random."""
    candidate = dataset_folder
    for _ in range(_MAX_LEVELS_UP + 1):
        if candidate.is_dir():
            found = sorted(
                entry
                for entry in candidate.iterdir()
                if entry.is_file() and entry.suffix.lower() in _PLAN_SUFFIXES and is_pump_plan_csv(entry)
            )
            named_like_folder = [entry for entry in found if entry.stem.casefold() == candidate.name.casefold()]
            if len(named_like_folder) == 1:
                return named_like_folder[0]
            if len(found) == 1:
                return found[0]
            if len(found) > 1:
                logger.warning(
                    "Several pump-plan files in %s and none is named like the folder, so none is used: %s",
                    candidate,
                    ", ".join(entry.name for entry in found),
                )
                return None
        if candidate.parent == candidate:
            break
        candidate = candidate.parent
    return None


def attach_pump_plan(
    metadata: ImagingAcquisitionMetadata, plan_csv: Path | None = None
) -> tuple[ImagingAcquisitionMetadata, list[str]]:
    """*metadata* with its `plan` (re)built, plus short messages for the user.

    With *plan_csv*: the file is read and joined to the measured steps (raises ``ValueError`` / ``OSError`` when
    it cannot be read; the caller decides whether that stops an import or is only logged). Without it, a missing
    plan is built from the measured notes alone and an existing one is kept. The end of the last step needs the
    last image time, so pass metadata that still has its `image_timings` (`rehydrated_acquisition_metadata`).
    """
    planned = None
    read_warnings: list[str] = []
    if plan_csv is not None:
        result = read_pump_plan_csv(plan_csv)
        planned, read_warnings = result.plan, result.warnings
    elif metadata.plan is not None:
        return metadata, []

    plan = build_imaging_plan(
        metadata.comment_events,
        data_end_unix_ms=end_of_data_unix_ms(metadata.image_timings),
        planned=planned,
        source_file=plan_csv.name if plan_csv is not None else None,
        read_warnings=read_warnings,
    )
    if plan is None:
        return metadata, []

    notes: list[str] = []
    if plan.source_file is not None:
        notes.append(f"Experiment plan: {len(plan.steps)} step(s), device settings from {plan.source_file}.")
    elif plan.steps:
        notes.append(f"Experiment plan: {len(plan.steps)} step(s) from the measured notes (no device settings).")
    notes.extend(f"Plan warning: {warning}" for warning in plan.warnings)
    return metadata.model_copy(update={"plan": plan}), notes

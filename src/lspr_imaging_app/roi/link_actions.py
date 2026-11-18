"""Link and unlink the selected ROIs (phase 3 of ``docs/roi_shapes_and_linking_plan_2026-11-18.md``). No Qt; the toolbox is passed in.

What the user does: select sample ROIs and reference ROIs (on the image or in the table) and press **Link** (Ctrl+L): every selected sample is linked to
every selected reference. **Unlink** (Ctrl+K) takes links apart: with samples and references selected, only the links between those; with only samples
selected, all their links; with only references selected, those references from every sample that uses them. Each is one undo step and says in one line
what it did (or why it did nothing), for the status bar. The rules of what may be linked (`domain.roi_links.link_refusal`) and the commands themselves
(`RoiToolbox.link_references` / `unlink_references`) are the toolbox's; this module only decides *which ROIs* a selection means.
"""

from __future__ import annotations

from collections.abc import Iterable


def split_by_role(toolbox, roi_ids: Iterable[int]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """``(samples, references)`` among ``roi_ids``, each ascending; an id the toolbox does not know is left out."""
    known = {roi.area_roi_id: roi for roi in toolbox.rois()}
    chosen = sorted({int(i) for i in roi_ids if int(i) in known})
    references = tuple(i for i in chosen if known[i].role == "reference")
    return tuple(i for i in chosen if i not in references), references


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _already_linked(toolbox, samples: tuple[int, ...], references: tuple[int, ...]) -> bool:
    return all(r in toolbox.references_of(s) for s in samples for r in references)


def can_link(toolbox, roi_ids: Iterable[int]) -> bool:
    """The selection holds a sample and a reference that are not all linked to each other yet (what the Link entry is enabled by)."""
    samples, references = split_by_role(toolbox, roi_ids)
    return bool(samples and references) and not _already_linked(toolbox, samples, references)


def can_unlink(toolbox, roi_ids: Iterable[int]) -> bool:
    """The selection holds something to take apart (what the Unlink entry is enabled by)."""
    samples, references = split_by_role(toolbox, roi_ids)
    if samples and references:
        return any(r in toolbox.references_of(s) for s in samples for r in references)
    if samples:
        return any(toolbox.references_of(s) for s in samples)
    return any(toolbox.samples_of(r) for r in references)


def link_selected(toolbox, roi_ids: Iterable[int]) -> str:
    """Link every selected sample to every selected reference; the message for the status line. `ValueError` (a refusal, nothing changed) when the
    selection lacks a sample or a reference."""
    samples, references = split_by_role(toolbox, roi_ids)
    if not samples or not references:
        raise ValueError("Linking needs at least one sample ROI and one reference ROI selected.")
    if _already_linked(toolbox, samples, references):
        return "The selected samples are already linked to the selected references."
    toolbox.link_references(samples, references)
    return f"Linked {_plural(len(samples), 'sample ROI')} to {_plural(len(references), 'reference ROI')}."


def unlink_selected(toolbox, roi_ids: Iterable[int]) -> str:
    """Take apart the links the selection means (module docstring); the message for the status line. `ValueError` when nothing is selected."""
    samples, references = split_by_role(toolbox, roi_ids)
    if not samples and not references:
        raise ValueError("Select the ROIs to unlink first.")
    if samples and references:
        if not can_unlink(toolbox, roi_ids):
            return "The selected samples are not linked to the selected references."
        toolbox.unlink_references(samples, references)
        return f"Unlinked {_plural(len(samples), 'sample ROI')} from {_plural(len(references), 'reference ROI')}."
    if samples:
        if not can_unlink(toolbox, roi_ids):
            return "The selected samples have no linked references."
        toolbox.unlink_references(samples)
        return f"Removed the linked references of {_plural(len(samples), 'sample ROI')}."
    users = tuple(sorted({s for r in references for s in toolbox.samples_of(r)}))
    if not users:
        return "No sample is linked to the selected references."
    toolbox.unlink_references(users, references)
    return f"Unlinked {_plural(len(references), 'reference ROI')} from {_plural(len(users), 'sample ROI')}."

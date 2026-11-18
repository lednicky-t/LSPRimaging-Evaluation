"""Sample / reference roles and links between ROIs: the rules, as pure functions (no Qt, no app imports).

Plan and decisions: ``docs/roi_shapes_and_linking_plan_2026-11-18.md``.

Vocabulary
----------
* A ROI has a **role** (`AreaRoi.role`): ``"sample"`` or ``"reference"``.
* A **sample** is an analysis unit. Its reference is, in this order:
  1. the **linked** reference ROIs (`AreaRoi.reference_roi_ids`, one or more), when it has any;
  2. else its **built-in** reference (the ring around a circle), when it has one;
  3. else it has **no reference** and its results are missing (said so, never filled in from anything else).
* A **reference** ROI is one region and nothing else: no ring of its own, no links of its own, not an analysis unit.
  Any number of samples may link to it, including none.
* Several references linked to one sample are combined as **one region, pooled by pixel** (maintainer, 2026-11-18): the
  same number as if their pixels were one region. That combination happens on the stored scan numbers (analysis), not here.

Links live on the sample (`reference_roi_ids`), so they travel with the ROI through the session file, undo snapshots and the
export's session carry. `RoiToolbox` keeps them consistent through renumbering and deletion; `cleaned_links` repairs a
loaded session that is not.
"""

from __future__ import annotations

from collections.abc import Iterable

from .roi_model import AreaRoi

ROLE_SAMPLE = "sample"
ROLE_REFERENCE = "reference"
ROI_ROLES = (ROLE_SAMPLE, ROLE_REFERENCE)

SOURCE_LINKED = "linked"
SOURCE_BUILTIN = "builtin"
SOURCE_NONE = "none"
SOURCE_IS_REFERENCE = "is_reference"


def is_reference(roi: AreaRoi) -> bool:
    return roi.role == ROLE_REFERENCE


def is_sample(roi: AreaRoi) -> bool:
    return roi.role != ROLE_REFERENCE


def has_builtin_reference(roi: AreaRoi) -> bool:
    """True when the ROI's own geometry includes a reference region (its border).

    A ROI without a border of its own follows the shared default border (``AreaRoi.border is None``), which is a border, so only an
    explicitly switched-off border (``NO_BORDER``) means "none"."""
    return roi.border is None or roi.border.enabled


def reference_source(roi: AreaRoi) -> str:
    """Where this ROI's reference comes from: ``"linked"``, ``"builtin"``, ``"none"`` (a sample without any reference: its
    results are missing), or ``"is_reference"`` (the ROI is itself a reference)."""
    if is_reference(roi):
        return SOURCE_IS_REFERENCE
    if roi.reference_roi_ids:
        return SOURCE_LINKED
    if has_builtin_reference(roi):
        return SOURCE_BUILTIN
    return SOURCE_NONE


def samples_linked_to(rois: Iterable[AreaRoi], reference_id: int) -> tuple[int, ...]:
    """Ids of the samples that link to ``reference_id``, ascending."""
    rid = int(reference_id)
    return tuple(sorted(roi.area_roi_id for roi in rois if rid in roi.reference_roi_ids))


def normalized_ids(ids: Iterable[int]) -> tuple[int, ...]:
    """Ids as stored on a ROI: ints, no repeats, ascending."""
    return tuple(sorted({int(i) for i in ids}))


def link_refusal(rois_by_id: dict[int, AreaRoi], sample_ids: Iterable[int], reference_ids: Iterable[int]) -> str | None:
    """Why linking these samples to these references is refused, or ``None`` when it is allowed.

    Refused: an empty side, an unknown id, a "sample" that is a reference ROI, a "reference" that is a sample ROI. The
    message is meant for the status line."""
    samples, references = normalized_ids(sample_ids), normalized_ids(reference_ids)
    if not samples or not references:
        return "Linking needs at least one sample ROI and one reference ROI."
    unknown = [i for i in (*samples, *references) if i not in rois_by_id]
    if unknown:
        return f"No ROI with id {', '.join(map(str, sorted(set(unknown))))}."
    wrong_samples = [i for i in samples if is_reference(rois_by_id[i])]
    if wrong_samples:
        return f"ROI {', '.join(map(str, wrong_samples))} is a reference; only a sample can be linked to references."
    wrong_refs = [i for i in references if not is_reference(rois_by_id[i])]
    if wrong_refs:
        return f"ROI {', '.join(map(str, wrong_refs))} is a sample; make it a reference before linking samples to it."
    return None


def cleaned_links(rois: Iterable[AreaRoi]) -> list[str]:
    """Repair the roles and links of a set of ROIs **in place** (a loaded session) and return one line per repair.

    An unknown role becomes ``"sample"``; a reference ROI's own links are dropped; a link to a ROI that does not exist or is
    not a reference is dropped; links are put in stored form (ascending, no repeats). A consistent set is left untouched and
    returns an empty list."""
    rois = list(rois)
    by_id = {roi.area_roi_id: roi for roi in rois}
    problems: list[str] = []
    for roi in rois:
        if roi.role not in ROI_ROLES:
            problems.append(f"ROI {roi.area_roi_id}: unknown role {roi.role!r}, made a sample")
            roi.role = ROLE_SAMPLE
    for roi in rois:
        if not roi.reference_roi_ids:
            if not isinstance(roi.reference_roi_ids, tuple):
                roi.reference_roi_ids = ()
            continue
        if is_reference(roi):
            problems.append(f"ROI {roi.area_roi_id}: a reference cannot have links of its own, dropped {list(roi.reference_roi_ids)}")
            roi.reference_roi_ids = ()
            continue
        stored = normalized_ids(roi.reference_roi_ids)
        kept = tuple(i for i in stored if i in by_id and is_reference(by_id[i]))
        dropped = [i for i in stored if i not in kept]
        if dropped:
            problems.append(f"ROI {roi.area_roi_id}: dropped links to {dropped} (missing or not a reference)")
        roi.reference_roi_ids = kept
    return problems

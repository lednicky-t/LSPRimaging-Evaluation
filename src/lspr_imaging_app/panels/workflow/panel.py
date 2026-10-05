"""``WorkflowPanel`` (sketch §7 "Workflow shell", §10;
``docs/rewrite_gui_shell_design_2026-09.md`` §4, §4a).

A thin navigation/status host - hosts the stage sections (Dataset -> Image
tools -> ROI editor -> Analysis -> Outputs, with Spectra/Sensorgram as pure
downstream *display* consumers, per sketch §1 - Outputs is a fifth
*Workflow* stage, not a display panel, holding settings for how results are
visualized/formatted before export), shows which stage is active, and
reports status text for the main window's status bar - and otherwise owns no
scientific state. Replaces ``MainWindow``'s role as a god object; should
stay small enough that removing it and rewiring the modules directly would
be a mechanical exercise, not a redesign.

**Not the five display panels.** Earlier scaffolding had this class host
Image/Histogram/ROI-table/Spectra/Sensorgram as its own tabs - that was
placeholder wiring, not the design: per the GUI shell design doc, those five
are each their own dock widget, and this panel is docked alongside them
(left, per the stable app's own precedent), not their container.

**Visual structure copied from the stable app 2026-09-24 (design doc §4a),
content still not.** An earlier version of this file used a ``QTabWidget``
with one page per stage - discovered, on trying to match the stable app's
actual look, to not match it: the stable app's equivalent
(``gui/layout_builder.py:1411-1429``) builds a ``QTabWidget`` with exactly
*one* tab and then calls ``tabBar().hide()`` - there are no real stage tabs
there at all, just one continuously scrollable page holding all 5 top-level
``CollapsibleSection``s stacked vertically, each independently expandable.
This file now matches that: no ``QTabWidget``, one scroll area, 5 top-level
sections.

**One deliberate deviation from the source, at the maintainer's explicit
request**: the source lets multiple top-level sections sit expanded at once
(no real exclusivity, despite section titles' plain accordion look - see
design doc §4a's finding that the source's own pin/"accordion" language is
vestigial). Here, exactly one top-level section is expanded at all times -
a real single-open accordion (``WorkflowPanel._on_section_toggled``) -
which is what makes "current stage" well-defined enough to drive
``stage_changed`` (and, through it, ``layout_presets.py``'s
auto-apply-preset-on-stage-change feature) at all. Nested child sections
within a stage are unaffected - multiple of those can still be open
together, exactly as in the source.

Every section's body is still a plain "not built yet" placeholder - real
settings forms are separate future work, and the maintainer expects this
tree's exact shape to keep changing as they land.
"""

from __future__ import annotations

import logging
from enum import Enum, auto

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from lspr_ui import get_active_theme

from ...dataset import DatasetModule
from ...image_tools import BackgroundModule, GeometryModule
from ...selection import ReferenceFrameModule, SelectionModule
from ...storage.session_coordinator import SessionCoordinator
from ..ui_state import UiStateStore
from .background_removal import BackgroundRemovalSection
from .collapsible_section import CollapsibleSection
from .dataset_experimental_plan import ExperimentalPlanSection
from .dataset_export import DatasetExportSection
from .dataset_folder_row import DatasetFolderRow
from .dataset_summary import DatasetSummarySection
from .reference_frame_row import ReferenceFrameRow
from .session_picker_row import SessionPickerRow

logger = logging.getLogger(__name__)


class WorkflowStage(Enum):
    DATASET = auto()
    IMAGE_TOOLS = auto()
    ROI_SELECTION = auto()
    ANALYSIS = auto()
    OUTPUTS = auto()


def _section_placeholder(name: str) -> QWidget:
    label = QLabel(f"{name} - not built yet.")
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {get_active_theme().text_muted}; padding: 4px 2px;")
    return label


def _subsection_key(stage: WorkflowStage, title: str) -> str:
    """Stable identifier for a nested section's persisted expand/collapse
    state - `AppSettings.expanded_subsections` is keyed by this. Combines
    the stage so two stages can each have a section with the same title
    without colliding (none currently do, but nothing enforces uniqueness
    across stages, only within one - see each `_build_*_section`)."""
    return f"{stage.name}:{title}"


def _nested_title_color() -> str:
    """Dimmed title color for a section nested under a top-level stage
    section - matches the source's own ``_nested_title_color`` exactly
    (``gui/layout_builder.py``), so nesting depth reads the same way."""
    return get_active_theme().text_dim


def _nested_children(parent: QWidget, *sections: CollapsibleSection) -> QWidget:
    """Groups child sections under their parent - **no left-indent**
    (2026-09-25, maintainer's explicit call: the dimmed title color from
    ``_nested_title_color`` is distinction enough on its own, and every
    px of width matters against the Workflow panel's fixed-width budget -
    see ``test_lspri_workflow_panel_width_budget.py``). The source's
    ``_nested_section_group`` uses a 16px left margin for the same
    purpose; deliberately not ported here."""
    outer = QWidget(parent)
    layout = QVBoxLayout(outer)
    layout.setContentsMargins(0, 2, 0, 2)
    layout.setSpacing(4)
    for section in sections:
        layout.addWidget(section)
    return outer


def _build_dataset_section(
    parent: QWidget,
    dataset: DatasetModule,
    selection: SelectionModule,
    reference_frame: ReferenceFrameModule,
    session_coordinator: SessionCoordinator,
    geometry: GeometryModule | None = None,
    ui_state: UiStateStore | None = None,
) -> tuple[CollapsibleSection, list[tuple[str, CollapsibleSection]]]:
    """Ported from the source's ``dataset_section`` + its top row (folder
    field + browse/explorer icons, *outside* the nested sections) + its
    nested Summary/Reference/Export/Metadata children
    (``gui/layout_builder.py``) - restructured 2026-09-25 per the
    maintainer's explicit spec, no longer a straight port:

    - **No standalone Reference section.** Replaced by a compact
      "Define reference frame:" row (``reference_frame_row.py``) sitting
      alongside the folder row, outside the nested accordion - real
      Auto/Manual icon toggles and a live ``[Ref.frame: Cube #, WL #]``
      readout, backed by the new ``ReferenceFrameModule`` (see that
      module's own docstring for the module-boundary/scope reasoning).
    - **"Metadata" renamed "Experimental plan" and moved first** among the
      nested sections (``dataset_experimental_plan.py``, superseding
      ``dataset_metadata.py``) - a file-path + Import/Export icon row, the
      existing read-only summary, and a live current-frame comment/step
      preview linked to ``SelectionModule``.
    - **Summary and Export unchanged** from the 2026-09-25 rebuild earlier
      this session (real title-row mini-summary, conditional OME-Zarr
      block, real OME-Zarr export)."""
    folder_row = DatasetFolderRow(dataset, parent)
    session_picker_row = SessionPickerRow(session_coordinator, parent)
    reference_frame_row = ReferenceFrameRow(reference_frame, selection, parent)

    summary_content = DatasetSummarySection(dataset, parent)
    summary_section = CollapsibleSection(
        "Summary",
        summary_content,
        expanded=True,
        title_color=_nested_title_color(),
        header_extra=summary_content.header_stats_label,
        parent=parent,
    )

    experimental_plan_content = ExperimentalPlanSection(dataset, selection, parent)
    experimental_plan_section = CollapsibleSection(
        "Experimental plan",
        experimental_plan_content,
        expanded=False,
        title_color=_nested_title_color(),
        header_extra=experimental_plan_content.header_stats_label,
        parent=parent,
    )
    export_section = CollapsibleSection(
        "Export", DatasetExportSection(dataset, parent, geometry=geometry, ui_state=ui_state), expanded=False, title_color=_nested_title_color(), parent=parent
    )
    children = _nested_children(parent, experimental_plan_section, summary_section, export_section)
    subsections = [
        ("Experimental plan", experimental_plan_section),
        ("Summary", summary_section),
        ("Export", export_section),
    ]

    dataset_inner = QWidget(parent)
    dataset_inner_layout = QVBoxLayout(dataset_inner)
    dataset_inner_layout.setContentsMargins(0, 0, 0, 0)
    dataset_inner_layout.setSpacing(4)
    dataset_inner_layout.addWidget(folder_row)
    dataset_inner_layout.addWidget(session_picker_row)
    dataset_inner_layout.addWidget(reference_frame_row)
    dataset_inner_layout.addWidget(children)

    # Starts expanded - the accordion's fallback initial active stage when
    # there's no saved `active_workflow_stage` to restore yet (first-ever
    # launch, or a settings file predating that field). See
    # WorkflowPanel.__init__'s `initial_stage` handling.
    top_section = CollapsibleSection(
        "Dataset:",
        dataset_inner,
        expanded=True,
        header_extra=summary_content.dataset_header_stats_label,
        parent=parent,
    )
    return top_section, subsections


def _build_image_tools_section(
    parent: QWidget,
    background: BackgroundModule,
) -> tuple[CollapsibleSection, list[tuple[str, CollapsibleSection]]]:
    """Ported from the source's ``image_tools_section`` + its nested
    Transforms/Mask/Chromatic correction/Background removal children.

    **Transforms and Mask removed 2026-10-02** (maintainer request: "remove
    the Image tools and Mask sections from the Workflow panel, as they are
    fully in the Image panel") - both are now fully covered by the Image
    panel's own ribbon tabs ("Image tools" and "Mask", `panels/image/
    panel.py`/`tool_ribbon.py`): Transforms by a second `TransformsSection`
    instance there (built 2026-09-30), Mask by the "Edit" tool picker's
    Histogram-selection/Morphology panels plus the "Automatic edit" Clear/
    Load/Save group (both built 2026-10-02, `mask_edit_panels.py`/
    `mask_file_actions.py`). Only Chromatic correction and Background
    removal remain here - neither has an Image-panel-ribbon home yet.

    **Background removal is real** (2026-09-24, ``background_removal.py``) -
    the first section to use the apply toggle for real (``BackgroundModule``'s
    whole settings surface is one command). Chromatic correction is still a
    placeholder: per the design doc §3 icon-placement split, its real
    controls are meant to live on the Image panel's own toolbar, not built
    yet."""
    background_removal_content = BackgroundRemovalSection(background, parent)
    background_removal_section = CollapsibleSection(
        "Background removal",
        background_removal_content,
        expanded=True,
        applied=background.settings().flatten_background_enabled,
        apply_tooltip="Apply or skip background removal from the processing pipeline.",
        title_color=_nested_title_color(),
        parent=parent,
    )
    # Two-way: the header's apply toggle drives the module (a click always
    # pushes every current form value, not just `enabled` - matches
    # BackgroundModule's one-call settings API); an external settings
    # change (e.g. session restore) syncs the header back, signals blocked
    # so that sync doesn't re-trigger a push right back at the module -
    # same "set_applied via blockSignals" pattern the source's own
    # `_set_section_applied` uses (`gui/main_window.py:5233`).
    background_removal_section.apply_changed.connect(background_removal_content.set_enabled)

    def _sync_apply_toggle(_change: object) -> None:
        blocked = background_removal_section.blockSignals(True)
        try:
            background_removal_section.set_applied(background.settings().flatten_background_enabled)
        finally:
            background_removal_section.blockSignals(blocked)

    background.background_model_changed.connect(_sync_apply_toggle)

    chromatic_section = CollapsibleSection(
        "Chromatic correction",
        _section_placeholder("Chromatic correction"),
        expanded=False,
        title_color=_nested_title_color(),
        parent=parent,
    )
    children = _nested_children(parent, chromatic_section, background_removal_section)
    subsections = [
        ("Chromatic correction", chromatic_section),
        ("Background removal", background_removal_section),
    ]
    top_section = CollapsibleSection("Image tools:", children, expanded=False, parent=parent)
    return top_section, subsections


def _build_roi_selection_section(parent: QWidget) -> tuple[CollapsibleSection, list[tuple[str, CollapsibleSection]]]:
    """Ported from the source's ``roi_editor_section`` - just "Circles"
    today, since Rectangles/Freehand were removed dead placeholders (see
    design doc §4a). A single-child accordion is arguably pointless on its
    own; kept faithful to the source for now since the maintainer expects
    this tree to be revisited once ROI Selection's real controls land."""
    circles_section = CollapsibleSection(
        "Circles",
        _section_placeholder("Circle ROI detection/editing"),
        expanded=True,
        title_color=_nested_title_color(),
        parent=parent,
    )
    children = _nested_children(parent, circles_section)
    top_section = CollapsibleSection("ROI editor", children, expanded=False, parent=parent)
    return top_section, [("Circles", circles_section)]


def _build_analysis_section(parent: QWidget) -> tuple[CollapsibleSection, list[tuple[str, CollapsibleSection]]]:
    """Ported from the source's ``analysis_section`` + its nested ROI's
    math/Metric trace/Statistics children (the range/scope controls that
    sat above them there, and the Run/Stop/Live-preview header controls,
    are real interactive controls tied to ``AnalysisEngine`` - left for
    when this section gets wired to one, not ported as inert
    placeholders)."""
    roi_math_section = CollapsibleSection(
        "ROI's math", _section_placeholder("ROI's math"), expanded=True, title_color=_nested_title_color(), parent=parent
    )
    metric_trace_section = CollapsibleSection(
        "Metric trace", _section_placeholder("Metric trace"), expanded=True, title_color=_nested_title_color(), parent=parent
    )
    statistics_section = CollapsibleSection(
        "Statistics", _section_placeholder("Statistics"), expanded=False, title_color=_nested_title_color(), parent=parent
    )
    children = _nested_children(parent, roi_math_section, metric_trace_section, statistics_section)
    subsections = [
        ("ROI's math", roi_math_section),
        ("Metric trace", metric_trace_section),
        ("Statistics", statistics_section),
    ]
    top_section = CollapsibleSection("Analysis", children, expanded=False, parent=parent)
    return top_section, subsections


def _build_outputs_section(parent: QWidget) -> tuple[CollapsibleSection, list[tuple[str, CollapsibleSection]]]:
    """Outputs stage: flat, no nested children - per design doc §4a, this
    is new (not a straight port): settings for how results are
    visualized/formatted before export. The source's closest analogue
    ("Results / Export": export/open-folder/compact/upgrade buttons) was
    also flat, but is only a starting point for this stage's eventual
    scope, not its final content."""
    top_section = CollapsibleSection("Outputs", _section_placeholder("Outputs"), expanded=False, parent=parent)
    return top_section, []


class WorkflowPanel(QWidget):
    """Hosts the stage sections. Owns no scientific state - it wires
    already-constructed modules' settings UI together, nothing more.

    Takes each domain module it actually has real content for -
    ``dataset`` today, more to follow as later stages get built "one by
    one" (per the maintainer's own framing, 2026-09-24). A stage with no
    module passed yet still renders as a placeholder, same as before this
    module started taking any arguments at all."""

    stage_changed = pyqtSignal(WorkflowStage)
    # A nested (non-top-level) section's expand/collapse toggled - carries
    # its `_subsection_key(stage, title)` and the new state. Unlike
    # `stage_changed`, several of these can be open at once (no accordion
    # exclusivity for nested sections - see module docstring), so each
    # toggle is reported independently rather than as one "current" value.
    subsection_expanded_changed = pyqtSignal(str, bool)
    # State/performance text only (no hover-hint text, per the design doc) -
    # the main window connects this to its QStatusBar.
    status_requested = pyqtSignal(str)

    def __init__(
        self,
        dataset: DatasetModule,
        background: BackgroundModule,
        selection: SelectionModule,
        reference_frame: ReferenceFrameModule,
        session_coordinator: SessionCoordinator,
        initial_stage: WorkflowStage | None = None,
        initial_subsections: dict[str, bool] | None = None,
        parent: QWidget | None = None,
        geometry: GeometryModule | None = None,
        ui_state: UiStateStore | None = None,
    ) -> None:
        super().__init__(parent)
        # Order fixes the on-screen stacking order.
        built = [
            (
                WorkflowStage.DATASET,
                *_build_dataset_section(self, dataset, selection, reference_frame, session_coordinator, geometry, ui_state),
            ),
            (
                WorkflowStage.IMAGE_TOOLS,
                *_build_image_tools_section(self, background),
            ),
            (WorkflowStage.ROI_SELECTION, *_build_roi_selection_section(self)),
            (WorkflowStage.ANALYSIS, *_build_analysis_section(self)),
            (WorkflowStage.OUTPUTS, *_build_outputs_section(self)),
        ]
        self._sections: list[tuple[WorkflowStage, CollapsibleSection]] = [
            (stage, section) for stage, section, _subsections in built
        ]
        self._subsections: list[tuple[str, CollapsibleSection]] = [
            (_subsection_key(stage, title), section)
            for stage, _section, subsections in built
            for title, section in subsections
        ]

        if initial_stage is not None:
            # Restores the stage the user last had open (2026-09-28 settings
            # layer, `AppSettings.active_workflow_stage`) over each section's
            # own hardcoded default above. Set directly, not via
            # `_on_section_toggled`'s cascade, because the `expanded_changed`
            # -> `_on_section_toggled` wiring below hasn't happened yet at
            # this point in `__init__` - so this seeding can't itself emit a
            # spurious `stage_changed` (nothing is connected to it yet) and
            # doesn't need the cascade to be reentrant-safe during
            # construction. Each `CollapsibleSection` still updates its own
            # chevron/visibility immediately, since `set_expanded` always
            # does that regardless of what's listening on the outside.
            for stage, section in self._sections:
                section.set_expanded(stage is initial_stage)

        if initial_subsections:
            # Same idea, one level down (2026-09-29 settings layer,
            # `AppSettings.expanded_subsections`) - a key missing from the
            # dict (first launch, or a section that predates this field)
            # just leaves that section on its own hardcoded default, same
            # as `initial_stage=None` above. No accordion cascade to worry
            # about here since nested sections don't exclude each other.
            for key, section in self._subsections:
                if key in initial_subsections:
                    section.set_expanded(bool(initial_subsections[key]))

        page = QWidget(self)
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        page_layout.setSpacing(4)
        for _stage, section in self._sections:
            page_layout.addWidget(section)
        page_layout.addStretch(1)

        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        for stage, section in self._sections:
            section.expanded_changed.connect(
                lambda expanded, st=stage, sec=section: self._on_section_toggled(st, sec, expanded)
            )
        for key, section in self._subsections:
            section.expanded_changed.connect(
                lambda expanded, k=key: self.subsection_expanded_changed.emit(k, expanded)
            )

    def _on_section_toggled(self, stage: WorkflowStage, section: CollapsibleSection, expanded: bool) -> None:
        """Real single-open accordion across the 5 top-level stage sections
        (maintainer's explicit request, 2026-09-24 - see module docstring).
        Expanding one collapses every other; collapsing the only open one
        re-opens it instead, so exactly one is open at all times and
        ``stage_changed`` always reflects a real, unambiguous "current
        stage"."""
        if expanded:
            for _other_stage, other in self._sections:
                if other is not section and other.is_expanded():
                    other.set_expanded(False)
            self.stage_changed.emit(stage)
        elif not any(sec.is_expanded() for _st, sec in self._sections):
            section.set_expanded(True)

    def set_status(self, message: str) -> None:
        self.status_requested.emit(message)

"""The experiment steps as the user sees and edits them (2026-10-26; design: ``docs/experimental_plan_bar_design_2026-10-26.md``).

The dataset's own steps (``ImagingAcquisitionMetadata.plan``, built from the measured notes) are never written. What the user changes on the Sensorgram's
plan bar (a dragged boundary, a retyped note, a colour) is an overlay kept in the session: ``state.py`` (the saved shape), ``edits.py`` (applying it and the
edit operations), ``label_rule.py`` (which part of a note is shown), ``axis_map.py`` (time <-> the plot's x axis), ``module.py`` (the Qt owner).

This file imports nothing on purpose: ``storage/session.py`` imports ``plan.state`` and must stay Qt-free.
"""

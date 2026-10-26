from __future__ import annotations

WORKSPACE_SCHEMA_VERSION = 1
# 2 (unversioned baseline -> 2): see storage/workspace.py history.
# 3: the `analysis_cache` field (a serialized copy of the in-RAM analysis
# result caches) was removed - at full dataset scale it was a multi-GB JSON
# blob rewritten wholesale on every autosave. The HDF5 measurement-export
# backup (measurement_backup.h5) is now the sole disk-side cache; RAM cache
# size is a Preference instead (see analysis_pipeline_redesign.md \S4b/4e).
# Pure field removal - readers simply stop looking for the key, whether an
# older file still has it or not (no migration/gating needed).
PROCESSING_PROFILE_VERSION = 3
ROI_EXPORT_VERSION = 1
# 2 (2026-10-26): the optional `plan` field - the experiment's steps (`lspr_core.ImagingPlan`: label, start / end on
# the measurement clock, the plan file's pump / valve settings). Additive: a version 1 file has no `plan` and loads
# unchanged (the plan is rebuilt from its comment events). A version 1 reader ignores `plan` (and would drop it if
# it saved the metadata again); this version refuses a file newer than itself instead of guessing.
ACQUISITION_METADATA_VERSION = 2

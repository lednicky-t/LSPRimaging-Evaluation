# Contributing

Thanks for helping improve LSPRimaging.

## Working Style

- Keep changes small and focused.
- Prefer one feature, fix, or migration per branch.
- Update documentation in the same branch when user-facing behavior changes.
- Do not silently break old file formats unless the change is explicitly documented and versioned.

## Branching

Recommended branch names:

- `feature/roi-editor`
- `feature/roi-json-schema`
- `fix/spot-table-import`
- `docs/versioning-plan`

## Versioning

The project should treat these as separate concerns:

- app version
- schema version
- export/import format version

If a saved file format changes, include a version field and a migration path.

## Before Opening a PR

- Run the relevant checks or smoke tests.
- Verify imports/exports if the change touches persistence.
- Confirm any UI rename matches the updated docs and shortcuts.
- Add or update compatibility tests if formats changed.

## Pull Request Expectations

- Describe the user-visible change.
- Call out any backward compatibility risk.
- List file formats, schemas, or data models touched.
- Mention documentation updates.

## Code Review Mindset

- Prefer clear migrations over implicit behavior changes.
- Preserve old readers while a new format is being introduced.
- Add tests for every importer, exporter, and compatibility layer.

## Good Defaults

- Use ASCII unless the file already uses Unicode.
- Keep compatibility helpers isolated from the primary model where possible.
- Favor explicit version fields over guessing format variants from content.


# Image-panel test crash: status as of 2026-10-11

Short status note. Full history: the 2026-10-02 entry in `rewrite_build_log_2026-09.md` and
`whole_app_audit_2026-10-07.md`.

## Symptom

`tests/integration/test_lspri_rewrite_image_panel.py` sometimes ends in a Windows access violation
(Python's fault-handler dump, not a normal pytest failure). On 2026-10-11 it crashed once, then passed
twice (91 of 91). The cube-display / Workflow-panel changes made that day were not the cause: they are
display-only label changes and did not touch the Image panel's threads.

## What is known

- **Probabilistic, not deterministic.** The same file passes and crashes on different runs. One clean
  run or one crash is not evidence about a given file combination. Do not bisect by file combination.
- **Same family as the PyQt6-sip crash-on-close**: a background thread interacting with Qt C++ objects
  during teardown.
- **Fixed (2026-10-02):** `panels/image/render.py` `ImageRenderer.stop()` now joins its thread
  (`join(timeout=5.0)`), closing a use-after-free where `rendered.emit()` could fire on an already
  destroyed panel. Also fixed in `packages/lspr_ui/.../ui_helpers.py`: a deferred `QTimer.singleShot`
  touching a possibly deleted spinbox. These are real fixes but evidently not the only cause.
- The crash only affects the test process. It has not been seen in the running app.

## Decision (maintainer, 2026-10-11)

Not investigating further now. Documented and parked.

## If it is picked up again

1. Run `test_lspri_rewrite_image_panel.py` **alone**, 15-20 separate launches, with
   `python -X faulthandler`, saving each crash's stack. This measures the hit rate and shows which
   widget or thread was being touched. Same method as the PyQt6-sip investigation.
2. Check `Get-WinEvent` and `Get-Process python*` for stale processes first (old `python.exe` processes
   caused a false hang on 2026-10-02).
3. Run LSPRi tests one pytest process per file (all files in one process stalls).
4. Offscreen Qt on Windows has no fonts; tests that measure text width must call
   `tests/_qt_fonts.py` `load_system_fonts()`.

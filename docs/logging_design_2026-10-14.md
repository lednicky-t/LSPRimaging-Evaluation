# Logging and diagnostics design (LSPRi Evaluation rewrite) - 2026-10-14

Maintainer request: a log / console panel under Help, and a full logging design that is
efficient, useful for diagnosis, easy to analyse by tools (machine-readable) and easy to read
(human), with timings, applied in as many places as practical.

## 1. Principles

1. **Build on stdlib `logging`, do not replace it.** Every existing `logger.debug/info/...` call
   (37 files) is captured with zero changes. Structured events and timings are an *opt-in layer*
   on top (`applog.log_event`, `applog.timed`).
2. **Cheap when off, bounded when on.** Hot paths gate on `isEnabledFor`; a disabled `timed()`
   is a no-op object. Disk I/O never happens on the calling thread (queue + writer thread). The
   GUI never receives one signal per record: it *pulls* batches on a 100 ms timer.
3. **One record, two renderings.** A record is the same data for the human table in the panel and
   the JSON Lines file (`LogEntry`). Nothing is parsed back out of text.
4. **Aggregate, never spam.** Per-frame / per-cube / per-run granularity at DEBUG; never per
   pixel or per ROI (existing performance rule). The Timings tab aggregates for you.
5. **Panels display; modules log.** The Log panel owns no state beyond filters; the data lives
   in `applog` (ring buffer, file).
6. **Crashes leave evidence.** Uncaught exceptions (main + worker threads), unraisable
   exceptions, Qt warnings and native faults (`faulthandler`) all reach the log. (Addresses the
   "exit code 1 on close with no traceback" and PyQt6-sip crash notes.)

## 2. Architecture (`src/lspr_imaging_app/applog/`)

| Module | Qt? | Role |
|---|---|---|
| `entry.py` | no | `LogEntry` (immutable record: seq, wall time, monotonic time, level, logger, message, thread, event, fields, dur_ms, span ids, exception text) and `entry_from_record`. |
| `events.py` | no | `log_event(logger, "domain.action", level, **fields)`, `timed(...)` context manager / decorator (emits `dur_ms`, nests via `contextvars` span ids), `emit_timing`. |
| `handlers.py` | no | `RingBufferHandler` (bounded deque, pull API `entries_since(seq)`), `JsonlFormatter`, `build_file_pipeline` (QueueHandler -> QueueListener -> rotating JSONL file), human `ConsoleFormatter`. |
| `aggregate.py` | no | `TimingAggregator`: count / mean / p50 / p95 / max / total per event over bounded samples. |
| `filters.py` | no | `EntryFilter`: level, text, logger-prefix exclusions, "timed only", "since mark". |
| `export.py` | no | entries -> JSONL / CSV / plain text. |
| `setup.py` | no | `configure_logging()` (idempotent): ring + file + console, noisy-library levels, session id, old-file cleanup, exception / Qt / faulthandler hooks. Called once from `main()`; tests never call it. |
| `__main__.py` | no | `python -m lspr_imaging_app.applog summary|grep FILE.jsonl` for analysing a saved log without the app. |
| `panels/log/` | yes | the Log panel (Section 4) + `LogBridge` timer that pulls from the ring. |

### Record schema (JSON Lines, `lspri-log/1`)

First line of each file is a header: `{"schema":"lspri-log/1","app":...,"version":...,"session":...,"pid":...,"python":...,"platform":...,"started":...}`.
Every other line is one record:

```json
{"t":1760443201.123,"mono":812.3345,"lvl":"DEBUG","log":"lspr_imaging_app.panels.image.render","msg":"render.frame cube=0 wl=550 load_ms=3.2 prep_ms=11.8","thr":"ImageRenderer","ev":"render.frame","f":{"cube":0,"wl":550.0,"load_ms":3.2,"prep_ms":11.8},"dur_ms":15.0,"span":42,"parent":41,"sess":"20261014-101500-4242"}
```

`ev`, `f`, `dur_ms`, `span`, `parent`, `exc` appear only when present. `t` = wall clock (epoch s),
`mono` = `perf_counter` (use it for intervals; it does not jump). Files:
`<app>/logs/lspri-<session>.jsonl` (rotating 20 MB x 5) and `lspri-<session>.fault.log`
(`faulthandler`, native crashes). The newest 20 sessions are kept.

### Conventions (also in `CLAUDE.md`)

- Logger = `logging.getLogger(__name__)`.
- Levels: **ERROR** = something failed that the user should know; **WARNING** = recovered / degraded;
  **INFO** = user-level milestones (dataset loaded, analysis started/finished, session saved, task finished, play summary);
  **DEBUG** = per-call timings and internals, aggregated per outer unit of work.
- Event names `domain.action` (`render.frame`, `dataset.load`, `dataset.plane_read`, `reference.find`,
  `task.finished`, `playback.summary`, `ui.mark`); field names carry units (`_ms`, `_px`, `_mb`).
- Never log inside a per-pixel / per-ROI loop; never pass an expensive call as a log argument.
- Heavier tools stay on demand: `py-spy` (sampling) first, `cProfile` after stage timing names the stage.

## 3. Performance budget

- Record creation: stdlib `LogRecord` (~5-10 us). Ring append + file enqueue: ~20 us total
  (measured in `test_lspri_applog_core.py`, bound asserted loosely).
- Disabled `timed()` / `log_event()`: one `isEnabledFor` check.
- GUI cost: one 100 ms timer; per tick O(new entries); the table model appends in one batch.
- The capture level (panel toolbar) sets the *root logger level*, so lowering it to INFO removes
  DEBUG record creation everywhere, not just the display.

## 4. The Log panel (Help -> Log Console, Ctrl+Shift+L)

```
Screen purpose: look at what the app is doing, find slow stages, copy/export evidence for a bug report.
Main user actions: filter by level / text / logger, follow or pause, drop a marker, read details of
  one entry, see timing statistics per event, clear, copy, save, open the log folder.
Layout:
- Top: toolbar (Level, search box, Follow, Mark, Clear, Copy, Save, Open folder, Capture level).
- Centre: tabs "Log" (table of entries + a detail pane below) and "Timings" (table: event, count,
  mean, p50, p95, max, total ms; double-click an event filters the Log tab to it; Reset).
- Bottom: status line "Showing N of M | capture DEBUG | <log file path>".
Important states: following (auto-scroll) / paused; filters active; ring full (oldest rows drop);
  no records yet.
Validation rules: none (free text search; invalid input cannot occur).
Error messages: save/copy failures go to the status line and the log itself.
```

It is a normal `PanelContainer` dock, created floating and hidden, **not** in View -> Panels, the
Show/Hide-all commands or the layout presets (those take explicit dicts of docks). Its window state
is restored with the others.

## 5. What is instrumented in this change

`@instrumented` (every module command), `ImageRenderer` frames, plane reads from disk (ms; cache hits are not logged since
2026-10-14: 3 per Play frame, 46 % of a 10 min log), `gc.summary` (one record per 5 s of garbage-collector activity:
which threads ran it, longest pause; `applog/gc_trace.py`),
playback windows and summary, Find reference, dataset load, task finished (TaskIndicator), app
start / quit, plus all existing plain records. Further call sites: add `timed()` / `log_event()`
following the conventions above; no other change is needed for them to reach the panel and the file.

## 6. Not done / later

- A per-record "copy as JSON" and a time-range selector in the panel.
- A cross-session comparison tool (the JSONL files already support it with `jq` / pandas).
- Remote / launcher forwarding: the Suite Launcher's Console panel still reads the console
  stream (`LSPR_CONSOLE_LOG=1` keeps a DEBUG console handler exactly as the old app did).

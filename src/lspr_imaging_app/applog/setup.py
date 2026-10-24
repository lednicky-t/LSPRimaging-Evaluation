"""``configure_logging`` - wires the whole logging system once per process (2026-10-14).

Called from `app_rewrite.main()` only (tests build windows without it, so they
never get hooks or files). Idempotent: a second call returns the same
`LogSystem`.

What it sets up, in this order:

1. the session id, the log folder (`<app>/logs`, git-ignored), and cleanup of old
   sessions (newest `KEEP_SESSIONS` kept);
2. the root logger at the *capture level* (DEBUG by default; the Log panel can
   change it at run time - it really gates record creation everywhere);
3. handlers: `RingBufferHandler` (panel), the background JSONL file, and a console
   stream (DEBUG when ``LSPR_CONSOLE_LOG`` is set - the Suite Launcher's Console
   panel reads it, as with the old app - else ``LSPR_CONSOLE_LOG_LEVEL``, default WARNING);
4. noisy third-party loggers turned down to WARNING;
5. hooks so failures leave evidence: ``sys.excepthook``, ``threading.excepthook``,
   ``sys.unraisablehook``, Qt's message handler, ``warnings`` and ``faulthandler``
   (native crashes go to ``<session>.fault.log``).

**Behaviour note.** PyQt6 aborts the process when a slot raises and
``sys.excepthook`` is still the default. Installing our hook (as the old app did)
means such an exception is logged and the app keeps running.
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import platform
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .entry import SCHEMA, set_session_id
from .gc_trace import GcTrace
from .handlers import CONSOLE_DATE_FORMAT, CONSOLE_FORMAT, RingBufferHandler, build_file_pipeline

KEEP_SESSIONS = 20
NOISY_LOGGERS = ("PIL", "matplotlib", "numcodecs", "asyncio", "zarr", "urllib3", "numba", "fsspec", "blib2to3")
UNHANDLED_LOGGER = "lspr_imaging_app.unhandled"

_system: "LogSystem | None" = None


@dataclass
class LogSystem:
    ring: RingBufferHandler
    log_path: Path
    fault_path: Path
    session_id: str
    capture_level: int = logging.DEBUG
    _listener: Any = None
    _handlers: list[logging.Handler] = field(default_factory=list)
    _fault_file: Any = None
    _restore: list[Callable[[], None]] = field(default_factory=list)
    _closed: bool = False
    gc_trace: GcTrace | None = None
    """Where the cyclic garbage collector runs (`gc_trace.py`); the app calls `gc_trace.flush()` from a GUI timer."""

    @property
    def log_dir(self) -> Path:
        return self.log_path.parent

    def set_capture_level(self, level: int) -> None:
        """Gate record creation everywhere (root logger level)."""
        self.capture_level = int(level)
        logging.getLogger().setLevel(self.capture_level)

    def shutdown(self) -> None:
        """Drain the file queue, remove handlers and hooks. Safe to call twice."""
        global _system
        if self._closed:
            return
        self._closed = True
        root = logging.getLogger()
        for handler in self._handlers:
            root.removeHandler(handler)
        if self._listener is not None:
            self._listener.stop()  # drains the queue
        for handler in self._handlers:
            handler.close()
        for restore in reversed(self._restore):
            try:
                restore()
            except Exception:  # noqa: BLE001 - shutting down
                pass
        if self._fault_file is not None:
            try:
                faulthandler.disable()
                self._fault_file.close()
                if self.fault_path.exists() and self.fault_path.stat().st_size == 0:
                    self.fault_path.unlink()  # nothing crashed: do not leave an empty file per session
            except OSError:
                pass
        if _system is self:
            _system = None


def current() -> LogSystem | None:
    return _system


def default_log_dir() -> Path:
    """`<apps/LSPRi/eva>/logs` (the same folder the old app used)."""
    return Path(__file__).resolve().parents[3] / "logs"


def _new_session_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"


def _cleanup_old_sessions(log_dir: Path, keep: int) -> None:
    sessions = sorted(log_dir.glob("lspri-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in sessions[keep:]:
        stem = old.name[: -len(".jsonl")]
        for victim in [old, *log_dir.glob(f"{old.name}.*"), log_dir / f"{stem}.fault.log"]:
            try:
                victim.unlink()
            except OSError:
                pass


def configure_logging(
    log_dir: Path | None = None,
    *,
    app_version: str = "",
    capture_level: int = logging.DEBUG,
    install_hooks: bool = True,
    console: bool | None = None,
) -> LogSystem:
    global _system
    if _system is not None:
        return _system
    log_dir = Path(log_dir) if log_dir is not None else default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_old_sessions(log_dir, KEEP_SESSIONS - 1)  # the new session makes it KEEP_SESSIONS

    session = _new_session_id()
    set_session_id(session)
    log_path = log_dir / f"lspri-{session}.jsonl"
    fault_path = log_dir / f"lspri-{session}.fault.log"

    root = logging.getLogger()
    root.setLevel(capture_level)
    ring = RingBufferHandler()
    header = {
        "schema": SCHEMA, "app": "lspr_imaging_app", "version": app_version, "session": session, "pid": os.getpid(),
        "python": platform.python_version(), "platform": platform.platform(), "started": time.time(),
    }
    console_level = None
    if os.environ.get("LSPR_CONSOLE_LOG", "").strip().lower() in {"1", "true", "yes", "on"}:
        console_level = logging.DEBUG
    elif console is not False:
        name = os.environ.get("LSPR_CONSOLE_LOG_LEVEL", "WARNING").strip().upper()
        console_level = logging.getLevelName(name) if isinstance(logging.getLevelName(name), int) else logging.WARNING
    if console is False:
        console_level = None
    stream: logging.Handler | None = None
    if console_level is not None:
        stream = logging.StreamHandler()
        stream.setLevel(console_level)
        stream.setFormatter(logging.Formatter(CONSOLE_FORMAT, CONSOLE_DATE_FORMAT))
    # The console stream is run by the file pipeline's listener thread, not attached to the root logger: the GUI thread
    # only puts the record on the queue.
    queue_handler, listener, file_handler = build_file_pipeline(log_path, header, *([stream] if stream is not None else []))
    handlers: list[logging.Handler] = [ring, queue_handler]
    system = LogSystem(
        ring=ring, log_path=log_path, fault_path=fault_path, session_id=session, capture_level=capture_level,
        _listener=listener, _handlers=handlers,
    )
    system._handlers.append(file_handler)  # closed at shutdown, but not attached to the root (the listener owns it)
    if stream is not None:
        system._handlers.append(stream)  # likewise
    for handler in (h for h in handlers if h is not file_handler and h is not stream):
        root.addHandler(handler)

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

    if install_hooks:
        _install_hooks(system)
    _system = system
    atexit.register(system.shutdown)
    logging.getLogger(__name__).info(
        "Logging started: session %s, file %s", session, log_path,
        extra={"lspr_event": "app.logging", "lspr_fields": {"file": str(log_path), "capture": logging.getLevelName(capture_level)}},
    )
    return system


BENIGN_QT_WARNINGS = ("QFont::setPointSize: Point size <= 0",)
"""Qt warnings that change nothing: Qt itself ignores the call (the font keeps its size) and the app has nothing to fix. They are still
recorded, at DEBUG with their origin, but they no longer show as WARNINGS in the console / Log panel. 2026-10-24: "QFont::setPointSize: Point
size <= 0 (-1)" appeared about once per session while the pointer was over the ribbon; its origin (logged) is Qt's own event loop, not a Python
call, and no tooltip, hover or focus change of any widget in the Image panel reproduces it headless (probe in the build log). Add a message here
only after looking at its origin fields."""


def qt_log_level(level: int, message: str) -> int:
    """The level a Qt message is logged at: its own, except the known-harmless ones in :data:`BENIGN_QT_WARNINGS` (DEBUG)."""
    if level >= logging.WARNING and message.startswith(BENIGN_QT_WARNINGS):
        return logging.DEBUG
    return level


def _qt_message_origin(context: Any) -> dict[str, Any]:
    """Where a Qt warning came from, as far as it can be told: Qt's own context (usually empty in a release build), the Python stack of the thread
    that logged it (shows the Python call that led into Qt; just the event loop when Qt itself raised it), and on the GUI thread the widget under
    the mouse pointer, the focused widget and an open popup (a tooltip or menu opening is the usual native source). Never raises."""
    fields: dict[str, Any] = {}
    try:
        frames = traceback.extract_stack()[:-2][-6:]  # not this function and the handler
        fields["stack"] = " < ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in reversed(frames))
        for name in ("file", "function", "line"):
            value = getattr(context, name, None)
            if value:
                fields[f"qt_{name}"] = value
        if threading.current_thread() is threading.main_thread():
            from PyQt6.QtGui import QCursor
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                for key, widget in (("popup", QApplication.activePopupWidget()), ("focus", QApplication.focusWidget()),
                                    ("under_cursor", QApplication.widgetAt(QCursor.pos()))):
                    if widget is not None:
                        fields[key] = f"{type(widget).__name__}({widget.objectName()})"
    except Exception:  # noqa: BLE001 - a diagnostic must never make a Qt message handler fail
        pass
    return fields


def _install_hooks(system: LogSystem) -> None:
    unhandled = logging.getLogger(UNHANDLED_LOGGER)

    previous_excepthook = sys.excepthook

    def excepthook(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            previous_excepthook(exc_type, exc_value, exc_traceback)
            return
        unhandled.critical("Unhandled exception", exc_info=(exc_type, exc_value, exc_traceback))
        previous_excepthook(exc_type, exc_value, exc_traceback)

    sys.excepthook = excepthook
    system._restore.append(lambda: setattr(sys, "excepthook", previous_excepthook))

    previous_thread_hook = threading.excepthook

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "?"
        unhandled.error(
            "Unhandled exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )
        previous_thread_hook(args)

    threading.excepthook = thread_hook
    system._restore.append(lambda: setattr(threading, "excepthook", previous_thread_hook))

    previous_unraisable = sys.unraisablehook

    def unraisable_hook(unraisable: Any) -> None:
        unhandled.error(
            "Unraisable exception: %s", unraisable.err_msg or "",
            exc_info=(unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
        )
        previous_unraisable(unraisable)

    sys.unraisablehook = unraisable_hook
    system._restore.append(lambda: setattr(sys, "unraisablehook", previous_unraisable))

    logging.captureWarnings(True)
    system._restore.append(lambda: logging.captureWarnings(False))

    try:
        system._fault_file = open(system.fault_path, "w", encoding="utf-8")  # noqa: SIM115 - kept open for faulthandler
        faulthandler.enable(file=system._fault_file, all_threads=True)
    except Exception:  # noqa: BLE001 - diagnostics must not stop the app from starting
        logging.getLogger(__name__).warning("faulthandler could not be enabled", exc_info=True)

    system.gc_trace = GcTrace()
    system.gc_trace.install()
    system._restore.append(system.gc_trace.uninstall)

    try:
        from PyQt6.QtCore import QtMsgType, qInstallMessageHandler
    except Exception:  # noqa: BLE001 - Qt not importable: nothing to hook
        return
    qt_logger = logging.getLogger("qt")
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def qt_handler(msg_type, context, message) -> None:
        raw_level = levels.get(msg_type, logging.WARNING)
        level = qt_log_level(raw_level, message)
        if raw_level < logging.WARNING:
            qt_logger.log(level, "%s", message)
            return
        # A Qt warning says what, not who (a release Qt build gives no source line): add what we can see of where from (2026-10-24: a
        # "QFont::setPointSize: Point size <= 0" appeared once with nothing to trace it by)
        qt_logger.log(level, "%s", message, extra={"lspr_event": "qt.message", "lspr_fields": _qt_message_origin(context)})

    previous_qt = qInstallMessageHandler(qt_handler)
    system._restore.append(lambda: qInstallMessageHandler(previous_qt))

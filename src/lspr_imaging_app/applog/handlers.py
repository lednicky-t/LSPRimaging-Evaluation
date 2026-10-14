"""Log handlers (2026-10-14): the in-memory ring the Log panel reads, and the
background-thread JSON Lines file writer. See `docs/logging_design_2026-10-14.md`.

**Ring buffer** (`RingBufferHandler`): a bounded deque of `LogEntry`. Appending is
O(1) under the handler's own lock. Readers *pull*: `entries_since(seq)` returns
what arrived after the last sequence number they saw, so the GUI needs one timer
and no per-record signal, and cannot be flooded by a chatty thread.

**File** (`build_file_pipeline`): the calling thread only copies the record and
puts it on a queue; a `QueueListener` thread formats the JSON line and writes it.
A slow disk can therefore never slow the code that logs. The file rotates by size
and every file starts with a schema header line.
"""

from __future__ import annotations

import copy
import json
import logging
import logging.handlers
import queue
from collections import deque
from pathlib import Path
from typing import Any

from .aggregate import TimingAggregator, TimingRow
from .entry import LogEntry, entry_from_record

DEFAULT_CAPACITY = 50_000
FILE_MAX_BYTES = 20 * 1024 * 1024
FILE_BACKUPS = 5


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        super().__init__(logging.NOTSET)
        self._buffer: deque[LogEntry] = deque(maxlen=capacity)
        self._seq = 0
        self._timings = TimingAggregator()

    @property
    def capacity(self) -> int:
        return self._buffer.maxlen or 0

    def emit(self, record: logging.LogRecord) -> None:  # runs under self.lock (Handler.handle)
        try:
            self._seq += 1
            entry = entry_from_record(record, self._seq)
            self._buffer.append(entry)
            if entry.dur_ms is not None and entry.event:
                self._timings.add(entry.event, entry.dur_ms)
        except Exception:  # noqa: BLE001 - logging must never raise into the caller
            self.handleError(record)

    # -- reading (any thread) ---------------------------------------------------------

    def latest_seq(self) -> int:
        return self._seq

    def __len__(self) -> int:
        return len(self._buffer)

    def entries_since(self, seq: int) -> list[LogEntry]:
        """Entries that arrived after sequence number *seq* (oldest first)."""
        with self.lock:
            if not self._buffer or self._buffer[-1].seq <= seq:
                return []
            newer: list[LogEntry] = []
            for entry in reversed(self._buffer):
                if entry.seq <= seq:
                    break
                newer.append(entry)
        newer.reverse()
        return newer

    def snapshot(self) -> list[LogEntry]:
        with self.lock:
            return list(self._buffer)

    def snapshot_with_seq(self) -> tuple[list[LogEntry], int]:
        """(all stored entries, the sequence number they are current to) - taken
        together so a reader can continue with `entries_since(seq)` and miss nothing."""
        with self.lock:
            return list(self._buffer), self._seq

    def clear(self) -> None:
        """Forget the stored entries (the sequence counter keeps counting)."""
        with self.lock:
            self._buffer.clear()

    def timing_rows(self) -> list[TimingRow]:
        with self.lock:
            return self._timings.rows()

    def reset_timings(self) -> None:
        with self.lock:
            self._timings.reset()


class JsonlFormatter(logging.Formatter):
    """One compact JSON object per record (schema in the design doc)."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(entry_from_record(record).to_dict(), default=repr, separators=(",", ":"), ensure_ascii=False)


class _CheapQueueHandler(logging.handlers.QueueHandler):
    """`QueueHandler` that does not format the traceback or the final line on the
    calling thread: it freezes the message (arguments may change later) and leaves
    the rest to the writer thread."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        frozen = copy.copy(record)
        frozen.msg = record.getMessage()
        frozen.args = None
        return frozen


class _HeaderRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Writes the schema header line at the top of every file it opens (also after a rotation)."""

    def __init__(self, path: Path, header_line: str, **kwargs: Any) -> None:
        self._header_line = header_line
        super().__init__(path, encoding="utf-8", **kwargs)

    def _open(self):  # type: ignore[override]
        stream = super()._open()
        try:
            if stream.tell() == 0:
                stream.write(self._header_line + "\n")
                stream.flush()
        except OSError:
            pass
        return stream


def build_file_pipeline(
    path: Path, header: dict[str, Any]
) -> tuple[logging.Handler, logging.handlers.QueueListener, logging.Handler]:
    """(handler to attach to the root logger, started listener, the file handler).
    Stop the listener (`listener.stop()`) to drain the queue before exit."""
    file_handler = _HeaderRotatingFileHandler(
        path, json.dumps(header, default=repr, separators=(",", ":")), maxBytes=FILE_MAX_BYTES, backupCount=FILE_BACKUPS
    )
    file_handler.setFormatter(JsonlFormatter())
    records: queue.SimpleQueue = queue.SimpleQueue()
    listener = logging.handlers.QueueListener(records, file_handler, respect_handler_level=False)
    listener.start()
    return _CheapQueueHandler(records), listener, file_handler


CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
CONSOLE_DATE_FORMAT = "%H:%M:%S"

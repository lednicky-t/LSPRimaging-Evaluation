"""Structured events and timings on top of stdlib logging (2026-10-14).

- ``log_event(logger, "domain.action", level, **fields)``: one record carrying an
  event name and typed fields (the message text is ``domain.action k=v ...`` for
  humans).
- ``timed(logger, "domain.action", **fields)``: a context manager that measures
  its block and logs ``dur_ms``; nested ``timed`` blocks in the same thread
  record ``span`` / ``parent`` ids so the file can be read as a timing tree.
- ``emit_timing(...)``: for a duration you already measured yourself.
- ``timed_call("event")``: decorator form.

**Cost.** Everything starts with ``logger.isEnabledFor(level)``; a disabled
``timed`` takes no clock reading and returns a shared do-nothing object. With
logging on, the cost is one ``perf_counter`` pair plus one ordinary log record.

Field values should be JSON-friendly (numbers, strings, bools, None); anything
else is stored with ``repr`` by the file formatter.
"""

from __future__ import annotations

import functools
import itertools
import logging
import time
from contextvars import ContextVar
from typing import Any, Callable, TypeVar

_span_ids = itertools.count(1)
_current_span: ContextVar[int | None] = ContextVar("lspr_current_span", default=None)

_F = TypeVar("_F", bound=Callable[..., Any])


def _short(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)


def format_message(event: str, fields: dict[str, Any] | None, dur_ms: float | None = None) -> str:
    parts = [event]
    if dur_ms is not None:
        parts.append(f"dur_ms={dur_ms:.2f}")
    if fields:
        parts.extend(f"{key}={_short(value)}" for key, value in fields.items())
    return " ".join(parts)


def _emit(
    logger: logging.Logger,
    level: int,
    event: str,
    fields: dict[str, Any] | None,
    dur_ms: float | None,
    span: int | None,
    parent: int | None,
) -> None:
    logger.log(
        level,
        format_message(event, fields, dur_ms),
        extra={
            "lspr_event": event,
            "lspr_fields": fields,
            "lspr_dur_ms": dur_ms,
            "lspr_span": span,
            "lspr_parent": parent,
            "lspr_mono": time.perf_counter(),
        },
        stacklevel=3,
    )


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """One structured record (no duration)."""
    if logger.isEnabledFor(level):
        _emit(logger, level, event, fields or None, None, None, _current_span.get())


def emit_timing(
    logger: logging.Logger, event: str, dur_ms: float, level: int = logging.DEBUG, **fields: Any
) -> None:
    """A timing you measured yourself (``dur_ms`` in milliseconds)."""
    if logger.isEnabledFor(level):
        _emit(logger, level, event, fields or None, float(dur_ms), None, _current_span.get())


class _NoTimer:
    """What `timed` returns when its level is disabled: does nothing, costs nothing."""

    __slots__ = ()

    def __enter__(self) -> "_NoTimer":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def set(self, **_fields: Any) -> None:
        return None


_NO_TIMER = _NoTimer()


class _Timer:
    __slots__ = ("_logger", "_event", "_level", "_fields", "_t0", "_span", "_parent", "_token")

    def __init__(self, logger: logging.Logger, event: str, level: int, fields: dict[str, Any]) -> None:
        self._logger, self._event, self._level, self._fields = logger, event, level, fields

    def __enter__(self) -> "_Timer":
        self._span = next(_span_ids)
        self._parent = _current_span.get()
        self._token = _current_span.set(self._span)
        self._t0 = time.perf_counter()
        return self

    def set(self, **fields: Any) -> None:
        """Add fields known only after the work ran (sizes, counts, outcome)."""
        self._fields.update(fields)

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> None:
        dur_ms = (time.perf_counter() - self._t0) * 1000.0
        _current_span.reset(self._token)
        if exc_type is not None:
            self._fields["error"] = getattr(exc_type, "__name__", str(exc_type))
        _emit(self._logger, self._level, self._event, self._fields or None, dur_ms, self._span, self._parent)


def timed(logger: logging.Logger, event: str, level: int = logging.DEBUG, **fields: Any) -> _Timer | _NoTimer:
    """``with timed(log, "dataset.load", folder=name) as t: ...; t.set(planes=n)``."""
    if not logger.isEnabledFor(level):
        return _NO_TIMER
    return _Timer(logger, event, level, dict(fields))


def timed_call(event: str, level: int = logging.DEBUG) -> Callable[[_F], _F]:
    """Decorator: time every call of the function as *event* (logger = the function's module)."""

    def decorator(func: _F) -> _F:
        logger = logging.getLogger(func.__module__)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with timed(logger, event, level):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator

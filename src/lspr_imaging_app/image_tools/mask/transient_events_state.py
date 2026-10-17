"""The saved shape of a scan's event list (2026-10-17): plain dataclasses and their JSON payload, no Qt. `TransientEventsModule`
(`transient_events.py`) owns the live list and hands this out for the session file (`storage/session.py`, schema 1.4, key
``"transient_events"``). Plan: `docs/auto_transient_scan_and_mask_plan_2026-10-16.md`.

An event is only a **pointer**: the scan marks suspicious frames, it never touches a mask. What is kept per event is what the
rows, the tooltips and the later Auto mask step need: where and when (frame, centroid, box), how big and how deep, which passage
it belongs to, and, for a particle, its contour as a small boolean array (packed bits, a few hundred bytes), so masking does not
have to read the frames again. The scan's own settings are kept with the list (`ScanInfo`), so a list can always be told apart
from one made with other settings.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

import numpy as np

from .transient_detection import KIND_PARTICLE, ContourMask, TransientEvent

logger = logging.getLogger(__name__)

SCAN_ALGORITHM_VERSION = 1  # bump when the detection maths changes in a way that makes old lists not comparable


@dataclass(frozen=True)
class EventRecord:
    """One event in the list, with a stable id (the list's own numbering) and whether the user dismissed it."""

    id: int
    event: TransientEvent
    dismissed: bool = False


@dataclass(frozen=True)
class ScanInfo:
    """How a list was made: the algorithm version, when, with which settings, and what the scan covered."""

    algorithm_version: int = SCAN_ALGORITHM_VERSION
    created_utc: str = ""
    settings: dict = field(default_factory=dict)  # plain numbers and flags: sigma factor, stride, bin factor, ...
    frames_in_range: int = 0
    frames_screened: int = 0
    seconds: float = 0.0
    unreadable_frames: int = 0


@dataclass(frozen=True)
class TransientEventsState:
    info: ScanInfo | None = None
    records: tuple[EventRecord, ...] = ()


# -- payload ---------------------------------------------------------------------------------------------------------


def _encode_contour(contour: ContourMask) -> dict:
    packed = np.packbits(np.asarray(contour.mask, dtype=bool).ravel(order="C"))
    return {
        "x0": int(contour.x0),
        "y0": int(contour.y0),
        "shape": [int(dim) for dim in contour.mask.shape],
        "data": base64.b64encode(packed.tobytes()).decode("ascii"),
    }


def _decode_contour(payload: object) -> ContourMask | None:
    if not isinstance(payload, dict):
        return None
    try:
        height, width = (int(dim) for dim in payload["shape"])
        packed = np.frombuffer(base64.b64decode(str(payload["data"]).encode("ascii")), dtype=np.uint8)
        bits = np.unpackbits(packed, count=height * width).astype(bool).reshape((height, width))
        return ContourMask(x0=int(payload["x0"]), y0=int(payload["y0"]), mask=bits)
    except Exception:
        logger.warning("Ignoring an undecodable contour in the saved scan events", exc_info=True)
        return None


def to_payload(state: TransientEventsState) -> dict:
    """The JSON-ready dict of `state` (only particles keep their contour: nothing masks the other kinds)."""
    info = state.info
    return {
        "info": None
        if info is None
        else {
            "algorithm_version": int(info.algorithm_version),
            "created_utc": str(info.created_utc),
            "settings": dict(info.settings),
            "frames_in_range": int(info.frames_in_range),
            "frames_screened": int(info.frames_screened),
            "seconds": float(info.seconds),
            "unreadable_frames": int(info.unreadable_frames),
        },
        "events": [
            {
                "id": int(record.id),
                "cube": int(record.event.frame[0]),
                "wavelength_nm": float(record.event.frame[1]),
                "kind": str(record.event.kind),
                "area_px": int(record.event.area_px),
                "x": float(record.event.centroid_xy[0]),
                "y": float(record.event.centroid_xy[1]),
                "bbox": [int(v) for v in record.event.bbox_xyxy],
                "depth": float(record.event.depth),
                "z": float(record.event.z),
                "frame_sigma": float(record.event.frame_sigma),
                "passage": record.event.passage_id,
                "step_px": record.event.step_px,
                "inferred": bool(record.event.inferred),
                "dismissed": bool(record.dismissed),
                "contour": _encode_contour(record.event.contour)
                if record.event.kind == KIND_PARTICLE and record.event.contour is not None
                else None,
            }
            for record in state.records
        ],
    }


def from_payload(payload: object) -> TransientEventsState:
    """The state in `payload` (a missing or unreadable block is an empty list, an unreadable event is skipped and logged)."""
    if not isinstance(payload, dict):
        return TransientEventsState()
    raw_info = payload.get("info")
    info = None
    if isinstance(raw_info, dict):
        info = ScanInfo(
            algorithm_version=int(raw_info.get("algorithm_version", SCAN_ALGORITHM_VERSION)),
            created_utc=str(raw_info.get("created_utc", "")),
            settings=dict(raw_info.get("settings") or {}),
            frames_in_range=int(raw_info.get("frames_in_range", 0)),
            frames_screened=int(raw_info.get("frames_screened", 0)),
            seconds=float(raw_info.get("seconds", 0.0)),
            unreadable_frames=int(raw_info.get("unreadable_frames", 0)),
        )
    records: list[EventRecord] = []
    for raw in payload.get("events") or []:
        try:
            x0, y0, x1, y1 = (int(v) for v in raw["bbox"])
            step = raw.get("step_px")
            passage = raw.get("passage")
            event = TransientEvent(
                frame=(int(raw["cube"]), float(raw["wavelength_nm"])),
                kind=str(raw["kind"]),
                area_px=int(raw["area_px"]),
                centroid_xy=(float(raw["x"]), float(raw["y"])),
                bbox_xyxy=(x0, y0, x1, y1),
                depth=float(raw["depth"]),
                z=float(raw["z"]),
                contour=_decode_contour(raw.get("contour")),
                frame_sigma=float(raw.get("frame_sigma", 0.0)),
                passage_id=None if passage is None else int(passage),
                step_px=None if step is None else float(step),
                inferred=bool(raw.get("inferred", False)),
            )
            records.append(EventRecord(id=int(raw["id"]), event=event, dismissed=bool(raw.get("dismissed", False))))
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping an unreadable event in the saved scan events", exc_info=True)
    return TransientEventsState(info=info, records=tuple(records))

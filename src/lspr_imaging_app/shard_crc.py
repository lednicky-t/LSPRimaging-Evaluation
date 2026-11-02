"""CRC32C (Castagnoli), the checksum of a zarr v3 shard's index.

Moved here from `io/_zarr_export_worker.py` on 2026-11-02 (layering): the export worker writes the checksum and the plane readers
(`plane_reader.py`, which sit below `io`) check it. Uses the `crc32c` package when it is installed (~30 us per index) and a pure-Python
table loop otherwise (~110-180 ms per index). Standard library only, so a worker process loads it for nothing.
"""

from __future__ import annotations


def _build_crc32c_table() -> list[int]:
    poly = 0x82F63B78
    table: list[int] = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC32C_TABLE = _build_crc32c_table()


def _crc32c(data: bytes) -> int:
    """CRC32C (Castagnoli) checksum — required by the zarr v3 shard index format."""
    try:
        import crc32c as _lib  # type: ignore[import-untyped]
        return _lib.crc32c(data)
    except ImportError:
        pass
    crc = 0xFFFFFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFFFFFF

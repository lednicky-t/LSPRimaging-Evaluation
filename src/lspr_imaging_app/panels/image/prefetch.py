"""Which planes the Image panel reads ahead, and in what order.

Moving the wavelength slider to a plane that is not in `dataset.io`'s plane
cache costs a disk read (62-92 ms per 900x1300 TIFF plane, measured 2026-10-11)
before any processing starts. After a frame is shown, `ImageRenderer.prefetch`
reads the planes returned here into that cache while the render thread is idle
(see its docstring for the threading rules), so the next slider moves hit the
cache (0.1 ms). Pure ordering logic: no Qt, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence


def prefetch_order(
    current_cube: int,
    current_wavelength: float,
    cubes: Sequence[int],
    wavelengths_in_cube: Sequence[float],
    *,
    neighbour_cubes: int = 2,
) -> list[tuple[int, float]]:
    """The planes to read ahead, most likely first: the viewed cube's other
    wavelengths nearest to the current one, then the current wavelength in the
    `neighbour_cubes` cubes on each side (cube slider moves)."""
    ordered = sorted(wavelengths_in_cube, key=lambda wl: abs(float(wl) - float(current_wavelength)))
    keys = [(int(current_cube), float(wl)) for wl in ordered if float(wl) != float(current_wavelength)]
    cube_list = list(cubes)
    if int(current_cube) in cube_list:
        position = cube_list.index(int(current_cube))
        for step in range(1, neighbour_cubes + 1):
            for neighbour in (position + step, position - step):
                if 0 <= neighbour < len(cube_list):
                    keys.append((int(cube_list[neighbour]), float(current_wavelength)))
    return keys

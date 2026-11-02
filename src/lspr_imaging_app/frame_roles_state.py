"""The saved shape of every frame role (``FrameRolesModule``), as one plain value.

Kept in its own file so ``storage/session.py`` can import it without pulling in
the module (and Qt). One object holds all four roles' state - dark, ignored and
reference - so reading it from a session and writing it back is one code path
(2026-10-13, maintainer request to unify the reference with the other roles).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameRolesState:
    dark_wavelength_nm: float | None = None
    """The dark-background wavelength. `None` = no dark frame."""
    dark_user_set: bool = False
    """False = never chosen by the user, so a dataset with a 0 nm frame
    auto-assigns it. True = the user chose (or deliberately cleared) the dark;
    that choice is restored exactly, including "no dark"."""
    ignored_wavelengths: tuple[float, ...] = ()
    """Wavelengths ignored in every cube."""
    ignored_cubes: tuple[int, ...] = ()
    """Spectral cubes ignored whole."""
    reference_mode: str = "auto"
    """"auto" (best-contrast wavelength per cube) or "manual" (one stored frame).
    Auto's per-cube result is not saved; it is recomputed."""
    reference_cube: int | None = None
    reference_wavelength_nm: float | None = None
    """The manual reference frame. Kept while the mode is "auto", so switching
    back to manual finds it again."""

    @classmethod
    def from_payload(cls, raw: object) -> "FrameRolesState":
        """Rebuild from a session file's ``"frames"`` block. A missing or
        unreadable block gives the defaults (the right reading of a session
        written before roles existed)."""
        if not isinstance(raw, dict):
            return cls()
        try:
            dark = raw.get("dark_wavelength_nm")
            cube = raw.get("reference_cube")
            ref_wl = raw.get("reference_wavelength_nm")
            mode = str(raw.get("reference_mode", "auto"))
            return cls(
                dark_wavelength_nm=None if dark is None else float(dark),
                dark_user_set=bool(raw.get("dark_user_set", False)),
                ignored_wavelengths=tuple(sorted(float(w) for w in raw.get("ignored_wavelengths", ()))),
                ignored_cubes=tuple(sorted(int(c) for c in raw.get("ignored_cubes", ()))),
                reference_mode=mode if mode in ("auto", "manual") else "auto",
                reference_cube=None if cube is None else int(cube),
                reference_wavelength_nm=None if ref_wl is None else float(ref_wl),
            )
        except (TypeError, ValueError):
            return cls()

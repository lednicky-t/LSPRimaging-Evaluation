from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(slots=True, frozen=True)
class ImageExclusionRule:
    """A processing-exclusion rule for one (spectral_cube_index, wavelength_nm) scope.

    `None` on either field means "any value" -- both set excludes one specific
    image, `spectral_cube_index=None` excludes a wavelength across every cube,
    `wavelength_nm=None` excludes a whole cube. The scope tuple is the rule's
    identity: only one rule can exist per exact scope.
    """

    spectral_cube_index: int | None
    wavelength_nm: float | None
    note: str = ""
    created_at_utc: str = ""


def rule_matches(rule: ImageExclusionRule, spectral_cube_index: int, wavelength_nm: float) -> bool:
    if rule.spectral_cube_index is not None and int(rule.spectral_cube_index) != int(spectral_cube_index):
        return False
    if rule.wavelength_nm is not None and float(rule.wavelength_nm) != float(wavelength_nm):
        return False
    return True


def is_excluded(rules: Sequence[ImageExclusionRule], spectral_cube_index: int, wavelength_nm: float) -> bool:
    return any(rule_matches(rule, spectral_cube_index, wavelength_nm) for rule in rules)


def is_cube_fully_excluded(rules: Sequence[ImageExclusionRule], spectral_cube_index: int) -> bool:
    """True if a rule excludes this entire cube (wavelength_nm is a wildcard)."""
    return any(
        rule.wavelength_nm is None and (rule.spectral_cube_index is None or int(rule.spectral_cube_index) == int(spectral_cube_index))
        for rule in rules
    )


def rule_for_scope(
    rules: Sequence[ImageExclusionRule], spectral_cube_index: int | None, wavelength_nm: float | None
) -> ImageExclusionRule | None:
    """Find the existing rule with this exact scope, if any (for removal/replace)."""
    for rule in rules:
        if rule.spectral_cube_index == spectral_cube_index and rule.wavelength_nm == wavelength_nm:
            return rule
    return None


def upsert_rule(
    rules: list[ImageExclusionRule],
    spectral_cube_index: int | None,
    wavelength_nm: float | None,
    *,
    note: str = "",
    created_at_utc: str = "",
) -> None:
    """Add a rule for this scope, replacing any existing rule with the same scope."""
    remove_rule(rules, spectral_cube_index, wavelength_nm)
    rules.append(
        ImageExclusionRule(
            spectral_cube_index=spectral_cube_index,
            wavelength_nm=wavelength_nm,
            note=note,
            created_at_utc=created_at_utc,
        )
    )


def remove_rule(rules: list[ImageExclusionRule], spectral_cube_index: int | None, wavelength_nm: float | None) -> None:
    rules[:] = [
        rule
        for rule in rules
        if not (rule.spectral_cube_index == spectral_cube_index and rule.wavelength_nm == wavelength_nm)
    ]

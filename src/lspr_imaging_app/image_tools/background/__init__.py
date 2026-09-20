"""Background-correction sub-module (sketch §7 "Image Tools" > Background, §10)."""

from .model import BackgroundSettings
from .module import BackgroundModule

__all__ = ["BackgroundSettings", "BackgroundModule"]

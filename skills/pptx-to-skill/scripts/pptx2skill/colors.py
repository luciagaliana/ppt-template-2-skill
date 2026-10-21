"""Resolve DrawingML color elements (a:srgbClr, a:schemeClr, ...) to sRGB."""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .ooxml import local_name, num, percent

COLOR_TAGS = frozenset({"srgbClr", "schemeClr", "sysClr", "prstClr", "hslClr", "scrgbClr"})

SYSTEM_COLORS = {"window": "FFFFFF", "windowText": "000000"}

# Preset colors seen in real files; an unknown name resolves to None.
PRESET_COLORS = {
    "black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000",
    "blue": "0000FF", "yellow": "FFFF00", "cyan": "00FFFF", "magenta": "FF00FF",
    "gray": "808080", "grey": "808080", "dkGray": "A9A9A9", "ltGray": "D3D3D3",
    "orange": "FFA500", "purple": "800080", "navy": "000080", "silver": "C0C0C0",
}


@dataclass(frozen=True)
class Color:
    r: float  # sRGB channels, 0..1
    g: float
    b: float
    alpha: float = 1.0

    @classmethod
    def from_hex(cls, value: str) -> Color:
        value = value.lstrip("#")
        return cls(*(int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)))

    @property
    def hex(self) -> str:
        return "#" + "".join(f"{round(_clamp(c) * 255):02X}" for c in (self.r, self.g, self.b))


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(c: float) -> float:
    c = _clamp(c)
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def _apply_modifiers(color: Color, el: ET.Element) -> Color:
    """Apply the color's child modifiers (lumMod, tint, alpha, ...) in document order."""
    r, g, b, alpha = color.r, color.g, color.b, color.alpha
    for mod in el:
        name = local_name(mod)
        value = percent(mod.get("val"), 0.0)
        if name == "alpha":
            alpha = value
        elif name == "alphaMod":
            alpha *= value
        elif name == "alphaOff":
            alpha += value
        elif name in ("lumMod", "lumOff", "satMod", "satOff", "hueMod", "hueOff"):
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            if name == "lumMod":
                l *= value
            elif name == "lumOff":
                l += value
            elif name == "satMod":
                s *= value
            elif name == "satOff":
                s += value
            elif name == "hueMod":
                h = (h * value) % 1.0
            else:  # hueOff is an angle in 60000ths of a degree
                h = (h + num(mod.get("val")) / 60_000 / 360) % 1.0
            r, g, b = colorsys.hls_to_rgb(h, _clamp(l), _clamp(s))
        elif name == "tint":  # towards white, in linear RGB
            r, g, b = (_to_srgb(1 - (1 - _to_linear(c)) * value) for c in (r, g, b))
        elif name == "shade":  # towards black, in linear RGB
            r, g, b = (_to_srgb(_to_linear(c) * value) for c in (r, g, b))
        elif name == "inv":
            r, g, b = 1 - r, 1 - g, 1 - b
        elif name == "gray":
            r = g = b = 0.299 * r + 0.587 * g + 0.114 * b
    return Color(_clamp(r), _clamp(g), _clamp(b), _clamp(alpha))


class ColorContext:
    """Theme colors as one slide sees them, through its color map (tx1 -> dk1, ...)."""

    def __init__(self, theme: dict[str, str], clr_map: dict[str, str]):
        self.theme = theme
        self.clr_map = clr_map
        self.scheme_hits = 0  # colors that came from the theme

    def resolve(self, el: ET.Element) -> Color | None:
        kind = local_name(el)
        base: Color | None = None
        if kind == "srgbClr":
            base = Color.from_hex(el.get("val", "000000"))
        elif kind == "schemeClr":
            key = el.get("val", "")
            value = self.theme.get(self.clr_map.get(key, key))
            if value:
                self.scheme_hits += 1
                base = Color.from_hex(value)
        elif kind == "sysClr":
            base = Color.from_hex(el.get("lastClr") or SYSTEM_COLORS.get(el.get("val", ""), "000000"))
        elif kind == "prstClr":
            value = PRESET_COLORS.get(el.get("val", ""))
            base = Color.from_hex(value) if value else None
        elif kind == "hslClr":
            hue = num(el.get("hue")) / 60_000 / 360
            base = Color(*colorsys.hls_to_rgb(hue, percent(el.get("lum"), 0.0), percent(el.get("sat"), 0.0)))
        elif kind == "scrgbClr":
            base = Color(*(_to_srgb(percent(el.get(k), 0.0)) for k in ("r", "g", "b")))
        return _apply_modifiers(base, el) if base is not None else None

    def fill_color(self, parent: ET.Element | None) -> Color | None:
        """The color inside a fill-like element (a:solidFill, a:gs, a:fillRef, ...)."""
        if parent is None:
            return None
        for child in parent:
            if local_name(child) in COLOR_TAGS:
                return self.resolve(child)
        return None


def luminance(hex_value: str) -> float:
    c = Color.from_hex(hex_value)
    return 0.2126 * _to_linear(c.r) + 0.7152 * _to_linear(c.g) + 0.0722 * _to_linear(c.b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two hex colors (1 to 21)."""
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + 0.05) / (dark + 0.05)

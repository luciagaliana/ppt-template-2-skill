"""Fonts: typeface names -> CSS family and weight; embedded fonts (.fntdata) -> TTF files."""

from __future__ import annotations

import io
import re
import struct
from dataclasses import dataclass, field

from fontTools.ttLib import TTFont

from .ooxml import NS, Package, q

# Compound names first, so "Semi-Bold" is not read as "Bold".
_WEIGHT_SUFFIXES = [
    (r"extra[\s-]?light|ultra[\s-]?light", 200),
    (r"semi[\s-]?bold|demi[\s-]?bold", 600),
    (r"extra[\s-]?bold|ultra[\s-]?bold", 800),
    (r"thin|hairline", 100),
    (r"light", 300),
    (r"regular|normal|book", 400),
    (r"medium", 500),
    (r"bold", 700),
    (r"black|heavy", 900),
]

# Characters new slides will need. Embedded fonts are often subsets, so these get checked.
NEEDED_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    "áéíóúüñÁÉÍÓÚÜÑ¿¡.,;:!?'\"()-–—%€$&@/+"
)

_EOT_MAGIC = 0x504C
_EOT_COMPRESSED = 0x4  # MicroType Express
_EOT_XOR = 0x10000000


class UnsupportedFont(ValueError):
    pass


def split_typeface(typeface: str) -> tuple[str, int | None, bool]:
    """'Open Sauce Semi-Bold' -> ('Open Sauce', 600, False). Weight is None when the name has none."""
    name, italic = typeface.strip(), False
    match = re.search(r"[\s-]+(italic|oblique)$", name, re.IGNORECASE)
    if match:
        name, italic = name[: match.start()], True
    for pattern, weight in _WEIGHT_SUFFIXES:
        match = re.search(rf"[\s-]+(?:{pattern})$", name, re.IGNORECASE)
        if match and match.start() > 0:
            return name[: match.start()], weight, italic
    return name, None, italic


def _sfnt_kind(data: bytes) -> str | None:
    head = data[:4]
    if head in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if head == b"OTTO":
        return "otf"
    return None


def sfnt_from_fntdata(data: bytes) -> tuple[bytes, str]:
    """(font bytes, 'ttf' | 'otf') from an embedded font part: a plain font or an EOT wrapper."""
    kind = _sfnt_kind(data)
    if kind:
        return data, kind
    if len(data) < 36:
        raise UnsupportedFont("file too small")
    eot_size, font_size, _version, flags = struct.unpack_from("<4I", data)
    (magic,) = struct.unpack_from("<H", data, 34)
    if magic != _EOT_MAGIC or eot_size > len(data) or font_size > eot_size:
        raise UnsupportedFont("unknown font format")
    if flags & _EOT_COMPRESSED:
        raise UnsupportedFont("compressed EOT (MicroType Express) is not supported")
    font = data[eot_size - font_size : eot_size]  # the font data is the last EOT field
    if flags & _EOT_XOR:
        font = bytes(b ^ 0x50 for b in font)
    kind = _sfnt_kind(font)
    if not kind:
        raise UnsupportedFont("EOT without TrueType/OpenType data")
    return font, kind


@dataclass
class FontFace:
    """One embedded font file, as CSS @font-face will declare it."""

    typeface: str  # the name slides use, e.g. "Open Sauce Heavy"
    family: str  # the CSS family, e.g. "Open Sauce"
    weight: int
    italic: bool
    data: bytes = field(repr=False)
    ext: str = "ttf"
    codepoints: frozenset[int] = field(default_factory=frozenset, repr=False)

    @property
    def missing_chars(self) -> str:
        return "".join(c for c in NEEDED_CHARS if ord(c) not in self.codepoints)


def font_file_name(face: FontFace) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", face.family.lower()).strip("-") or "font"
    return f"assets/fonts/{slug}-{face.weight}{'-italic' if face.italic else ''}.{face.ext}"


def _weight_class(font: TTFont) -> int | None:
    if "OS/2" not in font:
        return None
    weight = font["OS/2"].usWeightClass
    if 1 <= weight <= 9:  # some old fonts use a 1-9 scale
        weight *= 100
    return weight if 1 <= weight <= 1000 else None


def _is_italic(font: TTFont) -> bool:
    if "OS/2" in font:
        return bool(font["OS/2"].fsSelection & 1)
    return bool(font["head"].macStyle & 2)


def load_embedded_fonts(pkg: Package, presentation: str) -> tuple[list[FontFace], list[str]]:
    """Every font file embedded in the presentation, plus warnings for the ones that cannot be read."""
    rels = pkg.rels(presentation)
    faces: list[FontFace] = []
    warnings: list[str] = []
    for entry in pkg.xml(presentation).iterfind("p:embeddedFontLst/p:embeddedFont", NS):
        font_el = entry.find("p:font", NS)
        typeface = font_el.get("typeface", "") if font_el is not None else ""
        family, name_weight, name_italic = split_typeface(typeface)
        for slot in ("regular", "bold", "italic", "boldItalic"):
            el = entry.find(f"p:{slot}", NS)
            rel = rels.get(el.get(q("r:id"), "")) if el is not None else None
            if rel is None or rel.external:
                continue
            try:
                data, ext = sfnt_from_fntdata(pkg.read(rel.target))
                font = TTFont(io.BytesIO(data))
                codepoints = frozenset(font.getBestCmap() or {})
            except Exception as exc:  # one unreadable font must not stop the extraction
                warnings.append(f"Embedded font '{typeface}' ({slot}) skipped: {exc}")
                continue
            if slot in ("bold", "boldItalic"):
                weight = _weight_class(font) or 700
            else:  # the name decides: it is what the slides refer to
                weight = name_weight or _weight_class(font) or 400
            italic = name_italic or slot in ("italic", "boldItalic") or _is_italic(font)
            faces.append(FontFace(typeface, family, weight, italic, data, ext, codepoints))
    return faces, warnings

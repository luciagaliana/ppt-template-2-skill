"""Extract a template's design system from what its slides actually use.

Canva and many internet templates leave the theme at Office defaults and style every
shape by hand, so colors, fonts and text styles are counted where the slides use them.
The theme is reported too, flagged as unused when no slide refers to it.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, NamedTuple
from xml.etree import ElementTree as ET

from .colors import Color, ColorContext
from .fonts import FontFace, font_file_name, load_embedded_fonts
from .ooxml import EMU_PER_PX, NS, Package, local_name, num, percent, q, xml_bool

THEME_COLOR_NAMES = (
    "dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3",
    "accent4", "accent5", "accent6", "hlink", "folHlink",
)
DEFAULT_CLR_MAP = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
TITLE_PLACEHOLDERS = {"title", "ctrTitle"}
OTHER_PLACEHOLDERS = {"dt", "ftr", "sldNum", "hdr"}
FILL_TAGS = {"noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"}
LINE_FILL_TAGS = {"noFill", "solidFill", "gradFill", "pattFill"}
EFFECTS = {
    "outerShdw": "shadow", "prstShdw": "shadow", "innerShdw": "inner shadow", "glow": "glow",
    "softEdge": "soft edge", "reflection": "reflection", "blur": "blur",
}
WEIGHT_NAMES = {
    100: "thin", 200: "extralight", 300: "light", 400: "regular", 500: "medium",
    600: "semibold", 700: "bold", 800: "extrabold", 900: "black",
}
SINGLE_LINE = 1.2  # PowerPoint's single line spacing, roughly; the exact value depends on the font
OPAQUE = 0.5  # fills at least this opaque count as the surface text sits on
_SVG_COLOR = re.compile(r"(?:fill|stroke|stop-color)\s*[:=]\s*[\"']?\s*#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")


class TextStyle(NamedTuple):
    family: str
    weight: int
    italic: bool
    size_px: float
    line_height: float  # multiple of the font size
    letter_spacing_em: float
    caps: str  # "none" | "all" | "small"


@dataclass
class StyleUsage:
    style: TextStyle
    runs: int = 0
    chars: int = 0
    slides: set[int] = field(default_factory=set)
    colors: Counter = field(default_factory=Counter)
    surfaces: Counter = field(default_factory=Counter)  # what the text sits on
    align: Counter = field(default_factory=Counter)
    sample: str = ""


@dataclass
class TypeStyle:
    """Exact text styles that only differ a little in size, merged into one.

    Canva fits every title to its box, so one title style shows up at many sizes.
    The most used member sets the size, line height and letter spacing.
    """

    members: list[StyleUsage]

    @property
    def main(self) -> StyleUsage:
        return max(self.members, key=lambda u: (u.runs, u.chars))

    @property
    def size_px(self) -> float:
        return self.main.style.size_px

    @property
    def runs(self) -> int:
        return sum(u.runs for u in self.members)

    @property
    def chars(self) -> int:
        return sum(u.chars for u in self.members)

    def total(self, attr: str) -> Counter:
        out: Counter = Counter()
        for u in self.members:
            out.update(getattr(u, attr))
        return out


@dataclass
class ColorUsage:
    hex: str
    slides: set[int] = field(default_factory=set)
    roles: Counter = field(default_factory=Counter)
    area: float = 0.0  # area covered, in slides (1.0 = one whole slide)
    chars: int = 0  # characters of text in this color


@dataclass
class GradientUsage:
    css: str
    count: int = 0
    slides: set[int] = field(default_factory=set)


@dataclass
class Master:
    theme_name: str = ""
    theme_colors: dict[str, str] = field(default_factory=dict)
    major_font: str = "Calibri"
    minor_font: str = "Calibri"
    clr_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CLR_MAP))
    title_style: ET.Element | None = None
    body_style: ET.Element | None = None
    other_style: ET.Element | None = None


@dataclass
class SlideContext:
    index: int
    part: str
    colors: ColorContext
    master: Master
    layout_part: str | None = None
    master_part: str | None = None
    background: str | None = None  # "#RRGGBB", "image", "gradient", "pattern" or None
    surfaces: list[tuple[tuple, str]] = field(default_factory=list)  # (box, "#RRGGBB" | "image"), in paint order
    edge_boxes: list[tuple] = field(default_factory=list)  # text boxes and lines, which sit on the margins
    largest_text: TextStyle | None = None
    elements: Counter = field(default_factory=Counter)
    features: Counter = field(default_factory=Counter)


@dataclass
class Design:
    tokens: dict  # the design system, JSON-ready
    inventory: dict  # what the slides contain, slide by slide
    faces: list[FontFace]  # embedded fonts to write next to theme.css
    warnings: list[str]


@dataclass(frozen=True)
class Transform:
    """Maps shape coordinates (EMU) to slide coordinates; nested groups compose."""

    sx: float = 1.0
    sy: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def box(self, xfrm: ET.Element | None) -> tuple[float, float, float, float] | None:
        off = xfrm.find("a:off", NS) if xfrm is not None else None
        ext = xfrm.find("a:ext", NS) if xfrm is not None else None
        if off is None or ext is None:
            return None
        return (
            num(off.get("x")) * self.sx + self.tx,
            num(off.get("y")) * self.sy + self.ty,
            num(ext.get("cx")) * self.sx,
            num(ext.get("cy")) * self.sy,
        )

    def into_group(self, xfrm: ET.Element | None) -> Transform:
        if xfrm is None:
            return self
        off, ext, ch_off, ch_ext = (xfrm.find(f"a:{n}", NS) for n in ("off", "ext", "chOff", "chExt"))
        if off is None or ext is None or ch_off is None or ch_ext is None:
            return self
        gsx = num(ext.get("cx")) / num(ch_ext.get("cx")) if num(ch_ext.get("cx")) else 1.0
        gsy = num(ext.get("cy")) / num(ch_ext.get("cy")) if num(ch_ext.get("cy")) else 1.0
        gtx = num(off.get("x")) - num(ch_off.get("x")) * gsx
        gty = num(off.get("y")) - num(ch_off.get("y")) * gsy
        return Transform(self.sx * gsx, self.sy * gsy, self.sx * gtx + self.tx, self.sy * gty + self.ty)


def walk(tree: ET.Element, transform: Transform) -> Iterator[tuple[str, ET.Element, tuple | None]]:
    """Yield (kind, element, box on the slide in EMU) for every shape, in paint order, entering groups."""
    for el in tree:
        kind = local_name(el)
        if kind == "AlternateContent":
            branch = el.find("mc:Fallback", NS)
            if branch is None:
                branch = el.find("mc:Choice", NS)
            if branch is not None:
                yield from walk(branch, transform)
        elif kind == "grpSp":
            xfrm = el.find("p:grpSpPr/a:xfrm", NS)
            yield kind, el, transform.box(xfrm)
            yield from walk(el, transform.into_group(xfrm))
        elif kind in ("sp", "pic", "cxnSp"):
            yield kind, el, transform.box(el.find("p:spPr/a:xfrm", NS))
        elif kind == "graphicFrame":
            yield kind, el, transform.box(el.find("p:xfrm", NS))
        elif kind == "contentPart":
            yield kind, el, None


def first_child(parent: ET.Element | None, tags: set[str]) -> ET.Element | None:
    if parent is None:
        return None
    return next((child for child in parent if local_name(child) in tags), None)


def level_lookup(containers: list[ET.Element], lvl: int, path: str, attr: str | None = None):
    """First match of `path` under a:lvl{lvl}pPr in the containers, most specific first.

    With `attr`, returns the attribute value of the first match that has it.
    """
    for container in containers:
        level = container.find(f"a:lvl{lvl}pPr", NS)
        if level is None:
            continue
        el = level.find(path, NS) if path else level
        if el is None:
            continue
        if attr is None:
            return el
        if el.get(attr) is not None:
            return el.get(attr)
    return None


def _geometry(sp_pr: ET.Element | None) -> str | None:
    if sp_pr is None:
        return None
    if sp_pr.find("a:custGeom", NS) is not None:
        return "custom"
    preset = sp_pr.find("a:prstGeom", NS)
    return preset.get("prst") if preset is not None else None


def _typeface(el: ET.Element | None, default: str) -> str:
    return (el.get("typeface") or default) if el is not None else default


def _expand_hex(value: str) -> str:
    value = value.upper()
    return "".join(c * 2 for c in value) if len(value) == 3 else value


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _css_number(value: float) -> str:
    return format(round(value, 2), "g")


def _css_color(color: Color) -> str:
    if color.alpha >= 0.999:
        return color.hex
    return f"{color.hex}{round(color.alpha * 255):02X}"


def _round4(px: float) -> int:
    return int(round(px / 4) * 4)


class Collector:
    def __init__(self, path: Path):
        self.source = Path(path).name
        self.pkg = Package(path)
        self.presentation = self.pkg.presentation_part()
        pres = self.pkg.xml(self.presentation)
        size = pres.find("p:sldSz", NS)
        self.width = num(size.get("cx") if size is not None else None, 9_144_000)
        self.height = num(size.get("cy") if size is not None else None, 6_858_000)
        self.default_style = pres.find("p:defaultTextStyle", NS)
        self.faces, self.warnings = load_embedded_fonts(self.pkg, self.presentation)
        self.faces_by_typeface: dict[str, list[FontFace]] = {}
        for face in self.faces:
            self.faces_by_typeface.setdefault(face.typeface, []).append(face)
        self.masters: dict[str, Master] = {}
        self.colors: dict[str, ColorUsage] = {}
        self.gradients: dict[str, GradientUsage] = {}
        self.styles: dict[TextStyle, StyleUsage] = {}
        self.style_classes: dict[TextStyle, str] = {}  # exact style -> its CSS class, once design() ran
        self.fonts_used: Counter = Counter()  # (family, weight, italic) -> characters
        self.text_on: Counter = Counter()  # (surface, text color) -> characters
        self.largest_text: list[TextStyle] = []  # per slide, the style of its biggest text
        self.left_edges: Counter = Counter()  # text box edges, in px rounded to 4
        self.right_edges: Counter = Counter()
        self.top_edges: Counter = Counter()  # per slide, the highest text box
        self.elements: Counter = Counter()
        self.features: Counter = Counter()
        self.slides: list[dict] = []
        self.theme_color_hits = 0
        self.theme_font_hits = 0
        self._svg_colors: dict[str, list[str]] = {}

    def run(self) -> Design:
        for index, part in enumerate(self.pkg.slide_parts(self.presentation), start=1):
            self.slide(index, part)
        return self.design()

    # Slides ---------------------------------------------------------------

    def context(self, index: int, part: str) -> tuple[SlideContext, str | None, str | None]:
        """A fresh context for one slide, plus its layout and master part names."""
        layout = self.pkg.related(part, "slideLayout")
        master_part = self.pkg.related(layout, "slideMaster") if layout else None
        master = self.master(master_part)
        ctx = ColorContext(master.theme_colors, self.color_map([part, layout], master))
        return SlideContext(index, part, ctx, master, layout, master_part), layout, master_part

    def slide(self, index: int, part: str) -> None:
        s, layout, master_part = self.context(index, part)
        ctx = s.colors
        s.background = self.background(s, [part, layout, master_part])
        tree = self.pkg.xml(part).find("p:cSld/p:spTree", NS)
        if tree is not None:
            for kind, el, box in walk(tree, Transform()):
                self.element(s, kind, el, box)
        self.margins(s.edge_boxes)
        if s.largest_text is not None:
            self.largest_text.append(s.largest_text)
        self.theme_color_hits += ctx.scheme_hits
        self.elements.update(s.elements)
        self.features.update(s.features)
        self.slides.append({
            "index": index,
            "part": part,
            "layout": layout,
            "background": s.background,
            "elements": dict(s.elements.most_common()),
            "features": dict(s.features.most_common()),
        })

    def margins(self, boxes: list[tuple]) -> None:
        """Count the slide's outermost text and line edges; elements that bleed off the slide do not count."""
        slide_width = self.width / EMU_PER_PX
        lefts, rights, tops = [], [], []
        for box in boxes:
            x, y, w, _ = (v / EMU_PER_PX for v in box)
            if x > 4:
                lefts.append(x)
            if slide_width - (x + w) > 4:
                rights.append(slide_width - (x + w))
            if y > 4:
                tops.append(y)
        for edges, values in ((self.left_edges, lefts), (self.right_edges, rights), (self.top_edges, tops)):
            if values:
                edges[_round4(min(values))] += 1

    def master(self, part: str | None) -> Master:
        if part is None:
            return Master()
        if part not in self.masters:
            root = self.pkg.xml(part)
            master = Master()
            theme_part = self.pkg.related(part, "theme")
            if theme_part:
                theme = self.pkg.xml(theme_part)
                master.theme_name = theme.get("name", "")
                plain = ColorContext({}, {})
                scheme = theme.find("a:themeElements/a:clrScheme", NS)
                for name in THEME_COLOR_NAMES:
                    color = plain.fill_color(scheme.find(f"a:{name}", NS) if scheme is not None else None)
                    if color is not None:
                        master.theme_colors[name] = color.hex
                fonts = theme.find("a:themeElements/a:fontScheme", NS)
                if fonts is not None:
                    master.major_font = _typeface(fonts.find("a:majorFont/a:latin", NS), master.major_font)
                    master.minor_font = _typeface(fonts.find("a:minorFont/a:latin", NS), master.minor_font)
            clr_map = root.find("p:clrMap", NS)
            if clr_map is not None:
                master.clr_map = dict(clr_map.attrib)
            master.title_style = root.find("p:txStyles/p:titleStyle", NS)
            master.body_style = root.find("p:txStyles/p:bodyStyle", NS)
            master.other_style = root.find("p:txStyles/p:otherStyle", NS)
            self.masters[part] = master
        return self.masters[part]

    def color_map(self, parts: list[str | None], master: Master) -> dict[str, str]:
        """The slide's color map: an override on the slide or its layout, else the master's."""
        for part in parts:
            if part:
                override = self.pkg.xml(part).find("p:clrMapOvr/a:overrideClrMapping", NS)
                if override is not None:
                    return dict(override.attrib)
        return master.clr_map

    def background(self, s: SlideContext, parts: list[str | None]) -> str | None:
        """The first background found on the slide, its layout or its master."""
        for part in parts:
            if not part:
                continue
            bg = self.pkg.xml(part).find("p:cSld/p:bg", NS)
            if bg is None:
                continue
            fill = first_child(bg.find("p:bgPr", NS), FILL_TAGS)
            if fill is not None:
                kind = local_name(fill)
                if kind == "solidFill":
                    color = s.colors.fill_color(fill)
                    self.add_color(color, s.index, "background", area=1.0)
                    return color.hex if color is not None else None
                if kind == "gradFill":
                    s.features["gradient background"] += 1
                    _, surface = self.fill(s, fill, 1.0, "background")
                    return surface or "gradient"
                if kind == "blipFill":
                    s.features["image background"] += 1
                    return "image"
                if kind == "pattFill":
                    return "pattern"
                return None
            ref = bg.find("p:bgRef", NS)
            if ref is not None:  # a theme background style, painted with this color
                color = s.colors.fill_color(ref)
                self.add_color(color, s.index, "background", area=1.0)
                return color.hex if color is not None else None
        return None

    # Shapes ---------------------------------------------------------------

    def element(self, s: SlideContext, kind: str, el: ET.Element, box) -> int:
        """Record one shape. Returns its number of text characters."""
        area = self.area(box)
        if kind == "grpSp":
            s.elements["group"] += 1
            self.transform_features(s, el.find("p:grpSpPr/a:xfrm", NS))
            return 0
        if kind == "contentPart":
            s.elements["ink"] += 1
            return 0
        if kind == "graphicFrame":
            data = el.find("a:graphic/a:graphicData", NS)
            uri = data.get("uri", "") if data is not None else ""
            name = {"table": "table", "chart": "chart", "diagram": "smartart"}.get(
                uri.rpartition("/")[2], "embedded object"
            )
            s.elements[name] += 1
            chars = self.table(s, data.find("a:tbl", NS), box) if name == "table" else 0
            if chars and box is not None:
                s.edge_boxes.append(box)
            return chars

        sp_pr = el.find("p:spPr", NS)
        style = el.find("p:style", NS)
        self.transform_features(s, sp_pr.find("a:xfrm", NS) if sp_pr is not None else None)
        self.effects(s, sp_pr, style)
        image = self.image(s, el, sp_pr, area)
        paints, surface = (True, "image") if image else self.shape_fill(s, sp_pr, style, area)
        outlined = self.outline(s, sp_pr, style)
        chars = 0
        body = el.find("p:txBody", NS)
        if body is not None:
            font_ref = style.find("a:fontRef", NS) if style is not None else None
            under = surface or self.surface_at(s, box)
            chars = self.text(s, body, self.text_containers(el, s), s.colors.fill_color(font_ref), under)
            self.body_features(s, body)
        if surface and box is not None:
            s.surfaces.append((box, surface))

        geometry = _geometry(sp_pr)
        is_line = kind == "cxnSp" or geometry == "line" or "Connector" in (geometry or "")
        if box is not None and (chars or is_line):
            s.edge_boxes.append(box)
        if is_line:
            s.elements["line"] += 1
        elif image:
            s.elements[image] += 1
        elif chars:
            s.elements["text in shape" if paints or outlined else "text"] += 1
        elif not paints and not outlined:
            s.elements["empty (invisible)"] += 1
        elif geometry == "custom":
            s.elements["freeform"] += 1
        elif geometry:
            s.elements[f"shape: {geometry}"] += 1
        else:
            s.elements["other"] += 1
        return chars

    def area(self, box) -> float:
        """Fraction of the slide a box covers, clipped to the slide."""
        if box is None:
            return 0.0
        x, y, w, h = box
        width = max(0.0, min(x + w, self.width) - max(x, 0.0))
        height = max(0.0, min(y + h, self.height) - max(y, 0.0))
        return width * height / (self.width * self.height)

    def surface_at(self, s: SlideContext, box) -> str | None:
        """What is painted under the center of `box`: the last opaque shape there, else the background."""
        if box is not None:
            cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
            for (x, y, w, h), surface in reversed(s.surfaces):
                if x <= cx <= x + w and y <= cy <= y + h:
                    return surface
        return s.background

    def transform_features(self, s: SlideContext, xfrm: ET.Element | None) -> None:
        if xfrm is None:
            return
        if num(xfrm.get("rot")) % 21_600_000:  # 60000ths of a degree
            s.features["rotation"] += 1
        if xml_bool(xfrm.get("flipH")) or xml_bool(xfrm.get("flipV")):
            s.features["flip"] += 1

    def effects(self, s: SlideContext, sp_pr: ET.Element | None, style: ET.Element | None) -> None:
        effect_list = sp_pr.find("a:effectLst", NS) if sp_pr is not None else None
        if effect_list is not None:
            for effect in effect_list:
                s.features[EFFECTS.get(local_name(effect), local_name(effect))] += 1
        elif style is not None:
            ref = style.find("a:effectRef", NS)
            if ref is not None and ref.get("idx", "0") != "0":
                s.features["theme effect"] += 1
        if sp_pr is not None and (sp_pr.find("a:scene3d", NS) is not None or sp_pr.find("a:sp3d", NS) is not None):
            s.features["3d"] += 1

    def image(self, s: SlideContext, el: ET.Element, sp_pr: ET.Element | None, area: float) -> str | None:
        """Record a picture (p:pic, or a shape filled with one) and return its element kind."""
        blip_fill = el.find("p:blipFill", NS)
        if blip_fill is None and sp_pr is not None:
            blip_fill = sp_pr.find("a:blipFill", NS)
        blip = blip_fill.find("a:blip", NS) if blip_fill is not None else None
        if blip is None:
            return None
        alpha = blip.find("a:alphaModFix", NS)
        if alpha is not None and percent(alpha.get("amt")) < 1:
            s.features["transparency"] += 1
        if blip.find("a:duotone", NS) is not None or blip.find("a:clrChange", NS) is not None:
            s.features["recolored image"] += 1
        crop = blip_fill.find("a:srcRect", NS)
        if crop is not None and any(num(v) for v in crop.attrib.values()):
            s.features["cropped image"] += 1
        svg = blip.find("a:extLst/a:ext/asvg:svgBlip", NS)
        if svg is None:
            return "image"
        rel = self.pkg.rels(s.part).get(svg.get(q("r:embed"), ""))
        if rel is not None and not rel.external:
            for value in self.svg_colors(rel.target):
                self.add_color(Color.from_hex(value), s.index, "icon", area=area)
        return "svg image"

    def svg_colors(self, part: str) -> list[str]:
        """Colors an SVG paints with, ignoring the default fill on its root element."""
        if part not in self._svg_colors:
            text = self.pkg.read(part).decode("utf-8", "replace")
            start = text.find("<svg")
            body = text[text.find(">", start) + 1 :] if start >= 0 else text
            values = _SVG_COLOR.findall(body) or _SVG_COLOR.findall(text)
            self._svg_colors[part] = list(dict.fromkeys(_expand_hex(v) for v in values))
        return self._svg_colors[part]

    def shape_fill(self, s: SlideContext, sp_pr, style, area: float) -> tuple[bool, str | None]:
        """Record the shape's fill. Returns (paints anything, opaque surface color or None)."""
        fill = first_child(sp_pr, FILL_TAGS)
        if fill is not None:
            return self.fill(s, fill, area, "fill")
        ref = style.find("a:fillRef", NS) if style is not None else None
        if ref is not None and ref.get("idx", "0") != "0":
            color = s.colors.fill_color(ref)
            self.add_color(color, s.index, "fill", area=area)
            return color is not None, color.hex if color is not None else None
        return False, None

    def outline(self, s: SlideContext, sp_pr, style) -> bool:
        """Record the shape's outline color. Returns whether the outline is visible."""
        line = sp_pr.find("a:ln", NS) if sp_pr is not None else None
        fill = first_child(line, LINE_FILL_TAGS)
        if fill is not None:
            return self.fill(s, fill, 0.0, "line")[0]
        ref = style.find("a:lnRef", NS) if style is not None else None
        if ref is not None and ref.get("idx", "0") != "0":
            color = s.colors.fill_color(ref)
            self.add_color(color, s.index, "line")
            return color is not None
        return False

    def fill(self, s: SlideContext, el: ET.Element, area: float, role: str) -> tuple[bool, str | None]:
        """Record the colors of a fill element. Returns (paints anything, opaque surface color or None)."""
        kind = local_name(el)
        if kind == "solidFill":
            color = s.colors.fill_color(el)
            self.add_color(color, s.index, role, area=area)
            if color is None:
                return False, None
            if color.alpha < 1:
                s.features["transparency"] += 1
            return True, color.hex if color.alpha >= OPAQUE else None
        if kind == "gradFill":
            s.features["gradient"] += 1
            stops = []
            for stop in el.findall("a:gsLst/a:gs", NS):
                color = s.colors.fill_color(stop)
                if color is not None:
                    self.add_color(color, s.index, "gradient", area=area / 2)
                    stops.append((percent(stop.get("pos"), 0.0), color))
            if not stops:
                return False, None
            stops.sort(key=lambda st: st[0])
            self.add_gradient(s.index, el, stops)
            first = stops[0][1]
            return True, first.hex if first.alpha >= OPAQUE else None
        if kind == "pattFill":
            s.features["pattern fill"] += 1
            colors = [s.colors.fill_color(el.find(sub, NS)) for sub in ("a:fgClr", "a:bgClr")]
            for color in colors:
                self.add_color(color, s.index, role, area=area / 2)
            return True, colors[0].hex if colors[0] is not None else None
        return kind == "blipFill", None

    def add_gradient(self, index: int, el: ET.Element, stops: list[tuple[float, Color]]) -> None:
        """Keep the gradient as CSS: DrawingML angles start pointing right, CSS ones pointing up."""
        css_stops = ", ".join(f"{_css_color(color)} {_css_number(pos * 100)}%" for pos, color in stops)
        linear = el.find("a:lin", NS)
        if el.find("a:path", NS) is not None:
            css = f"radial-gradient(circle, {css_stops})"
        else:
            angle = (num(linear.get("ang")) / 60_000 if linear is not None else 0) + 90
            css = f"linear-gradient({_css_number(angle % 360)}deg, {css_stops})"
        usage = self.gradients.setdefault(css, GradientUsage(css))
        usage.count += 1
        usage.slides.add(index)

    def table(self, s: SlideContext, tbl: ET.Element | None, box) -> int:
        if tbl is None:
            return 0
        cells = tbl.findall("a:tr/a:tc", NS)
        chars = 0
        for cell in cells:
            surface = None
            fill = first_child(cell.find("a:tcPr", NS), FILL_TAGS)
            if fill is not None:
                _, surface = self.fill(s, fill, self.area(box) / len(cells), "fill")
            body = cell.find("a:txBody", NS)
            if body is not None:
                own = body.find("a:lstStyle", NS)
                containers = [c for c in (own, s.master.other_style, self.default_style) if c is not None]
                chars += self.text(s, body, containers, None, surface or self.surface_at(s, box))
        return chars

    # Text -----------------------------------------------------------------

    def placeholder_sources(self, el: ET.Element, layout_part: str | None, master_part: str | None) -> list[tuple[str, ET.Element]]:
        """(part, shape) of the layout and master placeholders a slide placeholder inherits from, layout first.

        On the layout, a placeholder with an idx matches the same idx; on the master, the same type.
        """
        ph = el.find("*/p:nvPr/p:ph", NS)
        if ph is None:
            return []
        idx, ph_type = ph.get("idx"), ph.get("type", "obj")
        out = []
        for part in (layout_part, master_part):
            tree = self.pkg.xml(part).find("p:cSld/p:spTree", NS) if part else None
            for candidate in tree.iter() if tree is not None else ():
                if local_name(candidate) != "sp":
                    continue
                other = candidate.find("p:nvSpPr/p:nvPr/p:ph", NS)
                if other is None:
                    continue
                same_idx = idx is not None and other.get("idx") == idx
                other_type = other.get("type", "obj")
                same_type = other_type == ph_type or {other_type, ph_type} == TITLE_PLACEHOLDERS
                if same_idx or (idx is None and same_type) or (part == master_part and same_type):
                    out.append((part, candidate))
        return out

    def text_containers(self, el: ET.Element, s: SlideContext) -> list[ET.Element]:
        """Where a shape's paragraphs look up their level styles, most specific first.

        Placeholders inherit from the matching layout placeholder, then the master one, before
        the master's text styles: Google Slides puts a template's real text styles there.
        """
        own = el.find("p:txBody/a:lstStyle", NS)
        inherited, seen = [], set()
        for part, source in self.placeholder_sources(el, s.layout_part, s.master_part):
            if part not in seen:  # the first match of each part is the one PowerPoint uses
                seen.add(part)
                inherited.append(source.find("p:txBody/a:lstStyle", NS))
        ph = el.find("p:nvSpPr/p:nvPr/p:ph", NS)
        ph_type = None if ph is None else ph.get("type", "obj")
        if ph_type in TITLE_PLACEHOLDERS:
            shared = s.master.title_style
        elif ph_type is None or ph_type in OTHER_PLACEHOLDERS:
            shared = s.master.other_style
        else:
            shared = s.master.body_style
        return [c for c in (own, *inherited, shared, self.default_style) if c is not None]

    def text(self, s: SlideContext, body: ET.Element, containers, shape_color, surface: str | None) -> int:
        """Record every run of a text body. Returns its number of characters."""
        total = 0
        for para in body.iterfind("a:p", NS):
            ppr = para.find("a:pPr", NS)
            lvl = min(max(int(num(ppr.get("lvl") if ppr is not None else None)) + 1, 1), 9)
            align = (ppr.get("algn") if ppr is not None else None) or level_lookup(containers, lvl, "", "algn") or "l"
            for run in para:
                if local_name(run) not in ("r", "fld"):
                    continue
                text = "".join(t.text or "" for t in run.iterfind("a:t", NS)).strip()
                if not text:
                    continue
                style, color = self.run_style(s, run.find("a:rPr", NS), ppr, lvl, containers, shape_color)
                self.add_text(s, style, color, text, align, surface)
                total += len(text)
        return total

    def run_style(self, s: SlideContext, rpr, ppr, lvl: int, containers, shape_color) -> tuple[TextStyle, Color | None]:
        def attr(name: str) -> str | None:
            if rpr is not None and rpr.get(name) is not None:
                return rpr.get(name)
            return level_lookup(containers, lvl, "a:defRPr", name)

        def child(path: str) -> ET.Element | None:
            el = rpr.find(path, NS) if rpr is not None else None
            return el if el is not None else level_lookup(containers, lvl, f"a:defRPr/{path}")

        size_pt = num(attr("sz"), 1800) / 100
        typeface = self.typeface(_typeface(child("a:latin"), "+mn-lt"), s.master)
        family, weight, italic = self.face(typeface, xml_bool(attr("b")), xml_bool(attr("i")))
        spacing_pt = num(attr("spc")) / 100
        fill = child("a:solidFill")
        color = s.colors.fill_color(fill) if fill is not None else shape_color
        if color is None:
            color = s.colors.resolve(ET.Element(q("a:schemeClr"), val="tx1"))
        style = TextStyle(
            family=family,
            weight=weight,
            italic=italic,
            # Precise values: a 0.1% wider line can already break one word earlier.
            size_px=round(size_pt * 96 / 72, 2),
            line_height=round(self.line_height(ppr, lvl, containers, size_pt), 3),
            letter_spacing_em=round(spacing_pt / size_pt, 4) + 0.0 if size_pt else 0.0,
            caps=attr("cap") or "none",
        )
        return style, color

    def typeface(self, name: str, master: Master) -> str:
        """Resolve theme font references (+mj-lt, +mn-lt, ...) to a typeface name."""
        if name.startswith("+mj"):
            self.theme_font_hits += 1
            return master.major_font
        if name.startswith("+mn"):
            self.theme_font_hits += 1
            return master.minor_font
        return name

    def face(self, typeface: str, bold: bool, italic: bool) -> tuple[str, int, bool]:
        """CSS (family, weight, italic) that renders `typeface` with the run's bold/italic flags."""
        faces = self.faces_by_typeface.get(typeface)
        if not faces:
            return typeface, 700 if bold else 400, italic
        candidates = [f for f in faces if f.italic == italic] or faces
        face = max(candidates, key=lambda f: f.weight) if bold else min(candidates, key=lambda f: f.weight)
        # Canva flags heavy faces as bold as well: their own weight already says it.
        weight = 700 if bold and face.weight < 600 else face.weight
        return face.family, weight, italic or face.italic

    def line_height(self, ppr, lvl: int, containers, size_pt: float) -> float:
        spacing = ppr.find("a:lnSpc", NS) if ppr is not None else None
        if spacing is None:
            spacing = level_lookup(containers, lvl, "a:lnSpc")
        if spacing is not None:
            points = spacing.find("a:spcPts", NS)
            if points is not None and size_pt:
                return num(points.get("val")) / 100 / size_pt
            pct = spacing.find("a:spcPct", NS)
            if pct is not None:
                return percent(pct.get("val")) * SINGLE_LINE
        return SINGLE_LINE

    def add_text(self, s: SlideContext, style: TextStyle, color, text: str, align: str, surface: str | None) -> None:
        usage = self.styles.setdefault(style, StyleUsage(style))
        usage.runs += 1
        usage.chars += len(text)
        usage.slides.add(s.index)
        usage.align[align] += len(text)
        if color is not None:
            usage.colors[color.hex] += len(text)
            if surface:
                self.text_on[(surface, color.hex)] += len(text)
        if surface:
            usage.surfaces[surface] += len(text)
        if len(usage.sample) < 24 and len(text) > len(usage.sample):
            usage.sample = _clip(text, 80)
        if s.largest_text is None or style.size_px > s.largest_text.size_px:
            s.largest_text = style
        self.fonts_used[(style.family, style.weight, style.italic)] += len(text)
        self.add_color(color, s.index, "text", chars=len(text))

    def body_features(self, s: SlideContext, body: ET.Element) -> None:
        pr = body.find("a:bodyPr", NS)
        if pr is not None:
            if pr.find("a:normAutofit", NS) is not None:
                s.features["autofit: shrink text"] += 1
            if pr.find("a:spAutoFit", NS) is not None:
                s.features["autofit: resize shape"] += 1
            if pr.get("vert", "horz") != "horz":
                s.features["vertical text"] += 1
            if num(pr.get("numCol"), 1) > 1:
                s.features["text columns"] += 1
        if body.find(".//a:buChar", NS) is not None or body.find(".//a:buAutoNum", NS) is not None:
            s.features["bullets"] += 1
        if body.find(".//a:hlinkClick", NS) is not None:
            s.features["hyperlink"] += 1

    def add_color(self, color: Color | None, index: int, role: str, area: float = 0.0, chars: int = 0) -> None:
        if color is None:
            return
        usage = self.colors.setdefault(color.hex, ColorUsage(color.hex))
        usage.slides.add(index)
        usage.roles[role] += 1
        usage.area += area
        usage.chars += chars

    # Result ---------------------------------------------------------------

    def design(self) -> Design:
        palette = sorted(self.colors.values(), key=lambda c: (-len(c.slides), -(c.area + c.chars / 1000), c.hex))
        colors = [
            {
                "var": f"--color-{i}",
                "hex": c.hex,
                "slides": sorted(c.slides),
                "roles": dict(c.roles.most_common()),
                "area": round(c.area, 3),
                "chars": c.chars,
            }
            for i, c in enumerate(palette, start=1)
        ]
        gradients = [
            {"var": f"--gradient-{i}", "css": g.css, "count": g.count, "slides": sorted(g.slides)}
            for i, g in enumerate(sorted(self.gradients.values(), key=lambda g: (-len(g.slides), -g.count)), start=1)
        ]
        surfaces = self.surfaces()
        faces = self.unique_faces()
        styles = self.text_styles()
        fonts = self.fonts(faces)
        font_vars = self.font_vars(fonts)
        tokens = {
            "source": self.source,
            "slide": {
                "width": round(self.width / EMU_PER_PX),
                "height": round(self.height / EMU_PER_PX),
                "count": len(self.slides),
            },
            "colors": colors,
            "color_roles": self.color_roles(colors, surfaces),
            "surfaces": surfaces,
            "gradients": gradients,
            "fonts": fonts,
            "font_vars": font_vars,
            "font_roles": self.font_roles(styles, font_vars),
            "text_styles": styles,
            "spacing": self.spacing(),
            "theme": self.theme_summary(),
        }
        inventory = {
            "elements": dict(self.elements.most_common()),
            "features": dict(self.features.most_common()),
            "margins_px": {  # per slide, the outermost text/line edge: value -> number of slides
                "left": dict(self.left_edges.most_common()),
                "right": dict(self.right_edges.most_common()),
                "top": dict(self.top_edges.most_common()),
            },
            "slides": self.slides,
        }
        return Design(tokens, inventory, faces, self.warnings + self.font_warnings(fonts))

    def unique_faces(self) -> list[FontFace]:
        faces: dict[tuple, FontFace] = {}
        for face in self.faces:
            key = (face.family, face.weight, face.italic)
            if key in faces:
                self.warnings.append(
                    f"Embedded fonts '{faces[key].typeface}' and '{face.typeface}' both map to "
                    f"{face.family} {face.weight}; keeping the first one."
                )
            else:
                faces[key] = face
        return list(faces.values())

    def surfaces(self) -> list[dict]:
        """Each color text sits on, with the text color used on it: the template's light/dark pairs."""
        by_surface: dict[str, Counter] = defaultdict(Counter)
        for (surface, text), chars in self.text_on.items():
            by_surface[surface][text] += chars
        ranked = sorted(by_surface.items(), key=lambda item: -sum(item[1].values()))
        return [
            {"background": surface, "text": texts.most_common(1)[0][0], "chars": sum(texts.values()),
             "other_text": [hex_ for hex_, _ in texts.most_common()[1:]]}
            for surface, texts in ranked
            if surface.startswith("#")
        ]

    def color_roles(self, colors: list[dict], surfaces: list[dict]) -> dict[str, str]:
        """Name colors by what they do: slide background, surfaces with their text, then accents."""
        var_of = {c["hex"]: c["var"] for c in colors}
        roles: dict[str, str] = {}
        backgrounds = sorted((c for c in colors if c["roles"].get("background")), key=lambda c: -c["roles"]["background"])
        if backgrounds:
            roles["background"] = backgrounds[0]["var"]
        for i, surface in enumerate(surfaces[:3], start=1):
            roles[f"surface-{i}"] = var_of[surface["background"]]
            roles[f"on-surface-{i}"] = var_of[surface["text"]]
        taken = set(roles.values())
        accents = [c for c in colors if c["var"] not in taken and (c["area"] > 0 or "icon" in c["roles"] or "line" in c["roles"])]
        for i, c in enumerate(accents[:6], start=1):
            roles[f"accent-{i}"] = c["var"]
        return roles

    def text_styles(self) -> list[dict]:
        clusters = _cluster_styles(list(self.styles.values()))
        cluster_of = {u.style: c for c in clusters for u in c.members}
        votes = Counter(id(cluster_of[style]) for style in self.largest_text)
        names = _style_names(clusters, votes)
        styles = []
        for c in sorted(clusters, key=lambda c: (-c.size_px, -c.chars)):
            main = c.main.style
            sizes = [u.style.size_px for u in c.members]
            colors, surfaces, align = c.total("colors"), c.total("surfaces"), c.total("align")
            for u in c.members:
                self.style_classes[u.style] = f"text-{names[id(c)]}"
            styles.append({
                "class": f"text-{names[id(c)]}",
                "family": main.family,
                "weight": main.weight,
                "italic": main.italic,
                "size_px": main.size_px,
                "size_range_px": [min(sizes), max(sizes)],
                "line_height": main.line_height,
                "letter_spacing_em": main.letter_spacing_em,
                "caps": main.caps,
                "color": colors.most_common(1)[0][0] if colors else None,
                "surface": next((s for s, _ in surfaces.most_common() if s.startswith("#")), None),
                "align": align.most_common(1)[0][0] if align else "l",
                "runs": sum(u.runs for u in c.members),
                "chars": c.chars,
                "slides": sorted(set().union(*(u.slides for u in c.members))),
                "sample": c.main.sample,
            })
        return styles

    def fonts(self, faces: list[FontFace]) -> list[dict]:
        files = {(f.family, f.weight, f.italic): f for f in faces}
        fonts = []
        for key in sorted(set(self.fonts_used) | set(files)):
            family, weight, italic = key
            face = files.get(key)
            fonts.append({
                "family": family,
                "weight": weight,
                "italic": italic,
                "chars": self.fonts_used.get(key, 0),
                "typeface": face.typeface if face is not None else None,
                "file": font_file_name(face) if face is not None else None,
                "missing_chars": face.missing_chars if face is not None else None,
            })
        return fonts

    def font_vars(self, fonts: list[dict]) -> dict[str, str]:
        """CSS variable per font family, most used first."""
        chars: Counter = Counter()
        for font in fonts:
            chars[font["family"]] += font["chars"]
        return {family: f"--font-{i}" for i, (family, _) in enumerate(chars.most_common(), start=1)}

    def font_roles(self, styles: list[dict], font_vars: dict[str, str]) -> dict[str, str]:
        """Heading font: the largest text style's. Body font: the one of the style with the most text."""
        if not styles:
            return {}
        body = max(styles, key=lambda st: st["chars"])
        return {"heading": font_vars[styles[0]["family"]], "body": font_vars[body["family"]]}

    def spacing(self) -> dict[str, int]:
        """Margins the slides repeat: the most common outermost edge, when at least 3 slides share it."""
        out = {}
        for name, edges in (("margin-left", self.left_edges), ("margin-right", self.right_edges), ("margin-top", self.top_edges)):
            if edges:
                value, count = edges.most_common(1)[0]
                if count >= 3:
                    out[name] = value
        return out

    def font_warnings(self, fonts: list[dict]) -> list[str]:
        warnings = []
        for font in fonts:
            name = f"{font['family']} {font['weight']}{' italic' if font['italic'] else ''}"
            if font["file"] is None and font["chars"]:
                warnings.append(f"Font '{name}' is used but not embedded: browsers fall back to another font unless it is installed.")
            elif font["missing_chars"]:
                warnings.append(
                    f"Embedded '{font['typeface']}' ({name}) only has the characters the template uses; "
                    f"missing: {font['missing_chars']}"
                )
        return warnings

    def theme_summary(self) -> dict:
        master = next(iter(self.masters.values()), Master())
        return {
            "name": master.theme_name,
            "used_by_slides": self.theme_color_hits > 0 or self.theme_font_hits > 0,
            "colors": master.theme_colors,
            "fonts": {"major": master.major_font, "minor": master.minor_font},
        }


def _size_step(size_px: float) -> float:
    """How far apart two sizes of one style can be: wide for display text, which
    templates fit to its box; narrow for body text, whose sizes are set on purpose."""
    return 1.4 if size_px >= 80 else 1.15


def _cluster_styles(usages: list[StyleUsage]) -> list[TypeStyle]:
    """Merge styles of the same face whose sizes are close (see `_size_step`)."""
    faces: dict[tuple, list[StyleUsage]] = defaultdict(list)
    for u in usages:
        faces[(u.style.family, u.style.weight, u.style.italic, u.style.caps)].append(u)
    clusters = []
    for members in faces.values():
        members.sort(key=lambda u: -u.style.size_px)
        current = [members[0]]
        for u in members[1:]:
            largest = current[0].style.size_px
            if largest / u.style.size_px <= _size_step(largest):
                current.append(u)
            else:
                clusters.append(TypeStyle(current))
                current = [u]
        clusters.append(TypeStyle(current))
    return clusters


def _size_groups(clusters: list[TypeStyle], step: float = 1.25) -> list[list[TypeStyle]]:
    """Split clusters, largest first, wherever the next size is more than `step` times smaller."""
    groups: list[list[TypeStyle]] = []
    for c in sorted(clusters, key=lambda c: -c.size_px):
        if groups and groups[-1][-1].size_px / c.size_px <= step:
            groups[-1].append(c)
        else:
            groups.append([c])
    return groups


def _style_names(clusters: list[TypeStyle], title_votes: Counter) -> dict[int, str]:
    """Name text styles by role: display, title, h2, h3, ..., body, small, caption.

    The body style has the most text. The title style is the biggest text on the most
    slides. Bigger styles are display; those between title and body are headings.
    In each level the most used style keeps the plain name; the others add their weight.
    """
    if not clusters:
        return {}
    body = max(clusters, key=lambda c: c.chars)
    low, high = body.size_px / 1.15, body.size_px * 1.15
    bigger = [c for c in clusters if c.size_px > high]
    title = max(bigger, key=lambda c: (title_votes[id(c)], c.size_px)) if bigger else None
    title_max = max(u.style.size_px for u in title.members) if title else 0.0

    levels: dict[str, list[TypeStyle]] = defaultdict(list)
    display, headings, small = [], [], []
    for c in clusters:
        if c is body or low <= c.size_px <= high:
            levels["body"].append(c)
        elif c.size_px < low:
            small.append(c)
        elif c is title or (title and title.size_px / 1.25 <= c.size_px <= title_max * 1.05):
            levels["title"].append(c)
        elif title and c.size_px > title_max * 1.05:
            display.append(c)
        else:
            headings.append(c)
    for i, group in enumerate(_size_groups(display)):
        levels["display" if i == 0 else f"display-{i + 1}"] = group
    for i, group in enumerate(_size_groups(headings)):
        levels[f"h{i + 2}"] = group
    for i, group in enumerate(_size_groups(small)):
        levels[("small", "caption")[i] if i < 2 else f"caption-{i}"] = group

    names: dict[int, str] = {}
    for level, group in levels.items():
        group.sort(key=lambda c: (c is not body, -c.runs, -c.chars))
        taken = {level}
        names[id(group[0])] = level
        for c in group[1:]:
            weight = c.main.style.weight
            if weight != group[0].main.style.weight:
                name = f"{level}-{WEIGHT_NAMES.get(weight, weight)}"
            else:  # same weight, another size: larger or smaller than the level's main style
                name = f"{level}-{'lg' if c.size_px > group[0].size_px else 'sm'}"
            number = 2
            stem = name
            while name in taken:
                name = f"{stem}-{number}"
                number += 1
            taken.add(name)
            names[id(c)] = name
    return names


def extract_design(path: Path) -> Design:
    return Collector(path).run()


def load_template(path: Path) -> tuple[Collector, Design]:
    """The design system plus the collector that read it, which the slide converter reuses."""
    collector = Collector(path)
    return collector, collector.run()

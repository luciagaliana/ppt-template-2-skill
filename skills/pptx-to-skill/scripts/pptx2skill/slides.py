"""Convert slides to HTML layouts: one absolutely positioned element per shape, in paint order.

Positions are CSS px on the slide canvas (1px = 9525 EMU). Groups become nested
<div class="group"> elements whose children use the group's coordinate space, so
rotations and flips compose the way PowerPoint composes them. Text keeps the design
system's classes (text-title, text-body, ...) and only writes inline what differs.
"""

from __future__ import annotations

import hashlib
import html
import itertools
import math
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from .colors import Color
from .design import (
    FILL_TAGS, LINE_FILL_TAGS, Collector, Design, Transform, first_child, level_lookup, walk,
)
from .geometry import (
    DASHES, LINE_PRESETS, SvgPath, custom_paths, fmt, is_rectangle, preset_path, rounded_rect_path,
    rounded_rect_radius,
)
from .ooxml import EMU_PER_PX, NS, local_name, num, percent, q, xml_bool

DEFAULT_INSETS = (91440, 45720, 91440, 45720)  # left, top, right, bottom, in EMU
DEFAULT_LINE_WIDTH = 9525
SINGLE_LINE_EM = 1.2  # space before/after given in % of a line: one line is about 1.2em
ALIGN = {"ctr": "center", "r": "right", "just": "justify", "dist": "justify"}
ANCHOR = {"ctr": "center", "b": "flex-end"}
LINE_CAPS = {"rnd": "round", "sq": "square", "flat": "butt"}
WEB_IMAGES = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp"}
PHOTO_TYPES = {"jpg", "jpeg"}
ICON_AREA = 0.05  # images smaller than this share of the slide count as icons
BRAND_SLIDES = 3  # an image or text repeated at the same place on this many slides is the brand
AUTO_NUMBERS = {
    "arabicPeriod": "{n}.", "arabicParenR": "{n})", "arabicParenBoth": "({n})", "arabicPlain": "{n}",
    "alphaLcPeriod": "{a}.", "alphaUcPeriod": "{A}.", "alphaLcParenR": "{a})", "alphaUcParenR": "{A})",
    "romanLcPeriod": "{i}.", "romanUcPeriod": "{I}.",
}
GRAPHIC_KINDS = {"table": "table", "chart": "chart", "diagram": "SmartArt"}


@dataclass
class Box:
    """An element's box inside its container, in px, with its rotation and flips."""

    left: float
    top: float
    width: float
    height: float
    rotation: float = 0.0  # degrees, clockwise
    flip_h: bool = False
    flip_v: bool = False

    def css(self, *, height: bool = True, flips: bool = True) -> list[str]:
        out = [f"left:{fmt(self.left)}px", f"top:{fmt(self.top)}px", f"width:{fmt(self.width)}px"]
        if height:
            out.append(f"height:{fmt(self.height)}px")
        transform = []
        if self.rotation:
            transform.append(f"rotate({fmt(self.rotation)}deg)")
        if flips and (self.flip_h or self.flip_v):
            transform.append(f"scale({-1 if self.flip_h else 1},{-1 if self.flip_v else 1})")
        if transform:
            out.append("transform:" + " ".join(transform))
        return out


@dataclass(frozen=True)
class Frame:
    """A container's coordinate space: the slide itself, or the inside of a group."""

    sx: float = 1.0  # scale of child coordinates (group ext / chExt)
    sy: float = 1.0
    ox: float = 0.0  # child coordinate origin (chOff), in EMU
    oy: float = 0.0
    left: float = 0.0  # where the container sits on the slide, in px
    top: float = 0.0
    turned: bool = False  # some enclosing group is rotated or flipped: slide positions are approximate

    def box(self, xfrm: ET.Element | None) -> Box | None:
        off = xfrm.find("a:off", NS) if xfrm is not None else None
        ext = xfrm.find("a:ext", NS) if xfrm is not None else None
        if off is None or ext is None:
            return None
        return Box(
            (num(off.get("x")) - self.ox) * self.sx / EMU_PER_PX,
            (num(off.get("y")) - self.oy) * self.sy / EMU_PER_PX,
            num(ext.get("cx")) * self.sx / EMU_PER_PX,
            num(ext.get("cy")) * self.sy / EMU_PER_PX,
            num(xfrm.get("rot")) / 60_000,
            xml_bool(xfrm.get("flipH")),
            xml_bool(xfrm.get("flipV")),
        )

    def inside(self, xfrm: ET.Element, box: Box) -> Frame:
        ext, ch_off, ch_ext = (xfrm.find(f"a:{n}", NS) for n in ("ext", "chOff", "chExt"))
        sx, sy, ox, oy = self.sx, self.sy, 0.0, 0.0
        if ext is not None and ch_ext is not None:
            if num(ch_ext.get("cx")):
                sx = self.sx * num(ext.get("cx")) / num(ch_ext.get("cx"))
            if num(ch_ext.get("cy")):
                sy = self.sy * num(ext.get("cy")) / num(ch_ext.get("cy"))
        if ch_off is not None:
            ox, oy = num(ch_off.get("x")), num(ch_off.get("y"))
        turned = self.turned or bool(box.rotation) or box.flip_h or box.flip_v
        return Frame(sx, sy, ox, oy, self.left + box.left, self.top + box.top, turned)

    def on_slide(self, box: Box) -> tuple[float, float, float, float]:
        """The box in slide px (ignoring rotations)."""
        return self.left + box.left, self.top + box.top, box.width, box.height


@dataclass
class Slot:
    """Something the user replaces: a text, a photo, an icon or the logo."""

    key: str  # placeholder in the HTML until the slot gets its name
    kind: str  # "text" | "photo" | "icon" | "logo"
    base: str  # name stem: the text style ("title", "body") or the kind
    left: float  # on the slide, in px
    top: float
    width: float
    height: float
    style: str | None = None
    text: str = ""
    name: str = ""
    family: str = ""  # first paragraph's font, size and line height (for calibration)
    size_px: float = 0.0
    line_height: float = 0.0
    turned: bool = False


@dataclass
class CornerCandidate:
    """A rounded rectangle whose corner radius can be measured on the reference image."""

    key: str  # "<part>#<shape id>"
    slide: int
    box: tuple[float, float, float, float]  # on the slide, px
    radius: float  # as the .pptx draws it, px
    color: str  # outline color when it has one, else fill color
    outline: bool
    fills: list[str] = field(default_factory=list)  # fill colors inside the outline (gradient: every stop)
    stroke: float = 0.0  # outline width, px


@dataclass
class Layout:
    index: int
    html: str  # the <section class="slide"> markup
    background: str | None
    slots: list[Slot] = field(default_factory=list)


def _escape(text: str) -> str:
    """Text for HTML, keeping repeated and leading spaces, which HTML would collapse."""
    text = html.escape(text, quote=False).replace("\t", "\u2003")
    text = re.sub(r" (?= )", "\u00a0", text)
    return "\u00a0" + text[1:] if text.startswith(" ") else text


def _css_name(family: str) -> str:
    """A font name for an inline style attribute: single quotes, which the attribute's double quotes allow."""
    escaped = family.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _indent(lines: list[str] | str, prefix: str = "  ") -> str:
    text = lines if isinstance(lines, str) else "\n".join(lines)
    return "\n".join(prefix + line if line else line for line in text.split("\n"))


def _roman(n: int) -> str:
    out = ""
    for value, letters in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                           (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while n >= value:
            out, n = out + letters, n - value
    return out


def _letters(n: int) -> str:
    out = ""
    while n > 0:
        n, rest = divmod(n - 1, 26)
        out = chr(97 + rest) + out
    return out


def _text_of(el: ET.Element) -> str:
    return " ".join("".join(t.text or "" for t in p.iterfind(".//a:t", NS)) for p in el.iterfind("p:txBody/a:p", NS)).strip()


def _position_key(box: tuple[float, float, float, float]) -> tuple[int, int]:
    return round(box[0] / EMU_PER_PX / 16), round(box[1] / EMU_PER_PX / 16)


class SlideConverter:
    """Turns each slide of a template into the HTML of one layout."""

    def __init__(self, collector: Collector, design: Design, radius_overrides: dict[str, float] | None = None,
                 image_roles: dict[int, dict[str, str]] | None = None):
        self.c = collector
        self.pkg = collector.pkg
        # Slide number -> {image file name: "chart" | "photo" | "icon" | "logo" | "decoration"}: what a
        # person looking at the slides knows and the .pptx does not say.
        self.image_roles = image_roles or {}
        tokens = design.tokens
        self.styles = {s["class"]: s for s in tokens["text_styles"]}
        self.color_vars = {c["hex"]: c["var"] for c in tokens["colors"]}
        self.font_vars = tokens["font_vars"]
        self.slide_w = collector.width / EMU_PER_PX
        self.slide_h = collector.height / EMU_PER_PX
        self.radius_overrides = radius_overrides or {}  # "<part>#<shape id>" -> corner radius in px
        self.corner_candidates: list[CornerCandidate] = []
        self.assets: dict[str, str] = {}  # package part -> asset path in the skill
        self.warnings: list[str] = []
        self._digests: dict[str, str] = {}
        self.brand_images, self.brand_texts = self._find_brand()

    def digest(self, part: str) -> str:
        """Content hash of a media part: Canva stores the same logo once per slide."""
        if part not in self._digests:
            self._digests[part] = hashlib.sha1(self.pkg.read(part)).hexdigest() if part in self.pkg else part
        return self._digests[part]

    def _find_brand(self) -> tuple[set, set]:
        """Images and texts repeated at the same place on several slides: the logo and brand name."""
        images: Counter = Counter()
        texts: Counter = Counter()
        for part in self.pkg.slide_parts(self.c.presentation):
            tree = self.pkg.xml(part).find("p:cSld/p:spTree", NS)
            seen = set()
            for _, el, box in walk(tree, Transform()) if tree is not None else ():
                if box is None:
                    continue
                for blip in [*el.iterfind(".//a:blip", NS), *el.iterfind(".//asvg:svgBlip", NS)]:
                    rel = self.pkg.rels(part).get(blip.get(q("r:embed"), ""))
                    if rel is not None and not rel.external:
                        seen.add(("image", self.digest(rel.target), _position_key(box)))
                text = _text_of(el)
                if text:
                    seen.add(("text", text, _position_key(box)))
            for kind, value, position in seen:
                (images if kind == "image" else texts)[(value, position)] += 1
        return (
            {key for key, n in images.items() if n >= BRAND_SLIDES},
            {key for key, n in texts.items() if n >= BRAND_SLIDES},
        )

    def warn(self, message: str) -> None:
        message = f"Slide {self.index}: {message}"
        if message not in self.warnings:
            self.warnings.append(message)

    # Slides ---------------------------------------------------------------

    def convert(self, index: int, part: str, name: str) -> Layout:
        s, layout_part, master_part = self.c.context(index, part)
        self.s, self.index, self.part = s, index, part
        self.layout_part, self.master_part = layout_part, master_part
        self.slots: list[Slot] = []
        self._ids = itertools.count(1)
        self._slot_ids = itertools.count(1)

        background_css, background_html, background = self.background([part, layout_part, master_part])
        body = [background_html] if background_html else []
        for tree, tree_part in self.trees(part, layout_part, master_part):
            body += self.children(tree, Frame(), tree_part, inherited=tree_part != part)
        self.name_slots()
        markup = "\n".join(body)
        for slot in self.slots:
            markup = markup.replace(slot.key, slot.name)
        section = (
            f'<section class="slide" data-layout="{name}" style="{background_css}">\n'
            f"{_indent(markup)}\n</section>"
        )
        return Layout(index, section, background, self.slots)

    def trees(self, part: str, layout: str | None, master: str | None) -> list[tuple[ET.Element, str]]:
        """Shape trees in paint order: master and layout decorations, unless hidden, then the slide."""
        out = []
        slide = self.pkg.xml(part)
        if layout and slide.get("showMasterSp") in (None, "1", "true"):
            layout_root = self.pkg.xml(layout)
            if master and layout_root.get("showMasterSp") in (None, "1", "true"):
                out.append((self.pkg.xml(master).find("p:cSld/p:spTree", NS), master))
            out.append((layout_root.find("p:cSld/p:spTree", NS), layout))
        out.append((slide.find("p:cSld/p:spTree", NS), part))
        return [(tree, tree_part) for tree, tree_part in out if tree is not None]

    def background(self, parts: list[str | None]) -> tuple[str, str, str | None]:
        """(CSS for the slide, an <img> when the background is a picture, a summary)."""
        for part in parts:
            bg = self.pkg.xml(part).find("p:cSld/p:bg", NS) if part else None
            if bg is None:
                continue
            fill = first_child(bg.find("p:bgPr", NS), FILL_TAGS)
            if fill is None:
                color = self.s.colors.fill_color(bg.find("p:bgRef", NS))
                if color is not None:
                    return f"background:{self.css_color(color)}", "", color.hex
                continue
            kind = local_name(fill)
            if kind == "solidFill":
                color = self.s.colors.fill_color(fill)
                if color is not None:
                    return f"background:{self.css_color(color)}", "", color.hex
            elif kind == "gradFill":
                return f"background:{self.css_gradient(fill)}", "", "gradient"
            elif kind == "blipFill":
                asset = self.image_asset(fill.find("a:blip", NS), part)
                if asset:
                    style = f"left:0;top:0;width:{fmt(self.slide_w)}px;height:{fmt(self.slide_h)}px"
                    return "", f'<img class="el" data-role="background" src="{asset[0]}" alt="" style="{style}">', "image"
            return "", "", None
        return "background:#FFFFFF", "", "#FFFFFF"

    def children(self, tree: ET.Element, frame: Frame, part: str, inherited: bool) -> list[str]:
        out = []
        for el in tree:
            kind = local_name(el)
            if kind == "AlternateContent":
                branch = el.find("mc:Fallback", NS)
                if branch is None:
                    branch = el.find("mc:Choice", NS)
                if branch is not None:
                    out += self.children(branch, frame, part, inherited)
                continue
            if inherited and el.find("*/p:nvPr/p:ph", NS) is not None:
                continue  # layout and master placeholders only define where slide content goes
            if kind == "grpSp":
                out.append(self.group(el, frame, part, inherited))
            elif kind in ("sp", "cxnSp", "pic"):
                out.append(self.shape(el, frame, part))
            elif kind == "graphicFrame":
                out.append(self.graphic_frame(el, frame))
            elif kind == "contentPart":
                self.warn("ink drawings are not converted")
        return [item for item in out if item]

    def group(self, el: ET.Element, frame: Frame, part: str, inherited: bool) -> str:
        xfrm = el.find("p:grpSpPr/a:xfrm", NS)
        box = frame.box(xfrm)
        if box is None:
            return "\n".join(self.children(el, frame, part, inherited))
        items = self.children(el, frame.inside(xfrm, box), part, inherited)
        if not items:
            return ""
        return f'<div class="group" style="{";".join(box.css())}">\n{_indent(items)}\n</div>'

    def graphic_frame(self, el: ET.Element, frame: Frame) -> str:
        box = frame.box(el.find("p:xfrm", NS))
        data = el.find("a:graphic/a:graphicData", NS)
        kind = GRAPHIC_KINDS.get((data.get("uri", "") if data is not None else "").rpartition("/")[2], "embedded object")
        self.warn(f"{kind} not converted yet; the layout shows a placeholder")
        if box is None:
            return ""
        style = ";".join(box.css() + ["outline:2px dashed #E5484D", "display:grid", "place-items:center",
                                      "font:24px system-ui,sans-serif", "color:#E5484D"])
        return f'<div class="el" data-role="unsupported" style="{style}">{kind}</div>'

    # Shapes ---------------------------------------------------------------

    def placeholder_xfrm(self, el: ET.Element) -> ET.Element | None:
        """Position of a slide placeholder without its own: the matching layout or master placeholder."""
        for _, source in self.c.placeholder_sources(el, self.layout_part, self.master_part):
            xfrm = source.find("p:spPr/a:xfrm", NS)
            if xfrm is not None:
                return xfrm
        return None

    def shape(self, el: ET.Element, frame: Frame, part: str) -> str:
        sp_pr = el.find("p:spPr", NS)
        style = el.find("p:style", NS)
        xfrm = sp_pr.find("a:xfrm", NS) if sp_pr is not None else None
        if xfrm is None:
            xfrm = self.placeholder_xfrm(el)
        box = frame.box(xfrm)
        if box is None:
            return ""
        out = []
        props = el.find("*/p:cNvPr", NS)
        key = f"{part}#{props.get('id', '') if props is not None else ''}"
        blip_fill = el.find("p:blipFill", NS) if local_name(el) == "pic" else None
        fill = first_child(sp_pr, FILL_TAGS)
        if blip_fill is None and fill is not None and local_name(fill) == "blipFill":
            blip_fill = fill
        if blip_fill is not None and blip_fill.find("a:blip", NS) is not None:
            out.append(self.image(el, sp_pr, style, blip_fill, xfrm, box, frame, part, key))
        else:
            out.append(self.geometry(el, sp_pr, style, xfrm, box, frame, key))
        body = el.find("p:txBody", NS)
        if body is not None:
            out.append(self.text(el, body, box, frame, style))
        return "\n".join(item for item in out if item)

    def paths(self, el: ET.Element, sp_pr: ET.Element | None, xfrm: ET.Element, box: Box, key: str = "") -> list[SvgPath]:
        w, h = box.width, box.height
        rect = SvgPath(f"M0 0 H{fmt(w)} V{fmt(h)} H0 Z")
        if sp_pr is None:
            return [rect]
        custom = sp_pr.find("a:custGeom", NS)
        if custom is not None:
            if key in self.radius_overrides and rounded_rect_radius(custom) is not None:
                return [SvgPath(rounded_rect_path(w, h, self.radius_overrides[key]))]
            ext = xfrm.find("a:ext", NS)
            return custom_paths(custom, w, h, num(ext.get("cx")), num(ext.get("cy")))
        preset = sp_pr.find("a:prstGeom", NS)
        name = preset.get("prst", "rect") if preset is not None else "rect"
        if local_name(el) == "cxnSp" or name in LINE_PRESETS or "Connector" in name:
            if name.startswith("bentConnector"):
                return [SvgPath(f"M0 0 H{fmt(w / 2)} V{fmt(h)} H{fmt(w)}", fill=False)]
            if name.startswith("curvedConnector"):
                return [SvgPath(f"M0 0 C{fmt(w / 2)} 0 {fmt(w / 2)} {fmt(h)} {fmt(w)} {fmt(h)}", fill=False)]
            return [SvgPath(f"M0 0 L{fmt(w)} {fmt(h)}", fill=False)]
        d = preset_path(preset, w, h) if preset is not None else rect.d
        if d is None:
            self.warn(f"preset shape '{name}' is drawn as a rectangle")
            return [rect]
        return [SvgPath(d)]

    def note_corners(self, sp_pr, style, box: Box, frame: Frame, key: str, fill_visible: bool) -> None:
        """Remember rounded rectangles, so their corners can be checked against the reference."""
        custom = sp_pr.find("a:custGeom", NS) if sp_pr is not None else None
        radius = rounded_rect_radius(custom) if custom is not None else None
        if radius is None or frame.turned or box.rotation:
            return
        path = custom.find("a:pathLst/a:path", NS)
        radius_px = radius * box.width / num(path.get("w"), 1)
        fills: list[Color] = []
        fill = first_child(sp_pr, FILL_TAGS)
        if fill_visible and fill is not None and local_name(fill) == "solidFill":
            color = self.s.colors.fill_color(fill)
            fills = [color] if color is not None else []
        elif fill_visible and fill is not None and local_name(fill) == "gradFill":
            fills = [color for _, color in self.stops(fill)]
        outline = self.outline_color(sp_pr, style)
        line = sp_pr.find("a:ln", NS)
        stroke = num(line.get("w"), DEFAULT_LINE_WIDTH) / EMU_PER_PX if line is not None else 1.0
        slide_box = frame.on_slide(box)
        if outline is not None:
            self.corner_candidates.append(CornerCandidate(
                key, self.index, slide_box, radius_px, outline.hex, True, [c.hex for c in fills], stroke,
            ))
        elif len(fills) == 1:
            self.corner_candidates.append(CornerCandidate(key, self.index, slide_box, radius_px, fills[0].hex, False))

    def geometry(self, el: ET.Element, sp_pr, style, xfrm: ET.Element, box: Box, frame: Frame, key: str) -> str:
        """An <svg> for the shape's fill and outline, or nothing when neither shows."""
        defs: list[str] = []
        fill_css = self.fill_css(sp_pr, style, box, defs)
        stroke_css = self.stroke_css(sp_pr, style)
        if not fill_css and not stroke_css:
            return ""
        self.note_corners(sp_pr, style, box, frame, key, bool(fill_css))
        items = []
        for path in self.paths(el, sp_pr, xfrm, box, key):
            css = [fill_css if fill_css and path.fill else "fill:none"]
            if stroke_css and path.stroke:
                css.append(stroke_css)
            items.append(f'<path d="{path.d}" style="{";".join(css)}"/>')
        if all(item.endswith('style="fill:none"/>') for item in items):
            return ""
        return self.svg(box, defs, items)

    def svg(self, box: Box, defs: list[str], items: list[str]) -> str:
        style = box.css()
        style[2] = f"width:{fmt(max(box.width, 1))}px"
        style[3] = f"height:{fmt(max(box.height, 1))}px"
        inner = ([f"<defs>{''.join(defs)}</defs>"] if defs else []) + items
        return f'<svg class="el" style="{";".join(style)}" aria-hidden="true">\n{_indent(inner)}\n</svg>'

    def new_id(self, prefix: str) -> str:
        return f"l{self.index:02d}-{prefix}{next(self._ids)}"

    def css_color(self, color: Color, alpha: bool = True) -> str:
        """A palette variable when the color is in the palette; hex (with alpha) otherwise."""
        if alpha and color.alpha < 0.999:
            return f"{color.hex}{round(color.alpha * 255):02X}"
        var = self.color_vars.get(color.hex)
        return f"var({var})" if var else color.hex

    def stops(self, grad: ET.Element) -> list[tuple[float, Color]]:
        stops = []
        for stop in grad.findall("a:gsLst/a:gs", NS):
            color = self.s.colors.fill_color(stop)
            if color is not None:
                stops.append((percent(stop.get("pos"), 0.0), color))
        return sorted(stops, key=lambda st: st[0])

    def css_gradient(self, grad: ET.Element) -> str:
        stops = ", ".join(f"{self.css_color(c)} {fmt(pos * 100)}%" for pos, c in self.stops(grad))
        if grad.find("a:path", NS) is not None:
            return f"radial-gradient(circle, {stops})"
        linear = grad.find("a:lin", NS)
        angle = (num(linear.get("ang")) / 60_000 if linear is not None else 0) + 90
        return f"linear-gradient({fmt(angle % 360)}deg, {stops})"

    def svg_gradient(self, grad: ET.Element, box: Box, defs: list[str]) -> str:
        gid = self.new_id("g")
        stops = "".join(
            f'<stop offset="{fmt(pos * 100)}%" style="stop-color:{self.css_color(c, alpha=False)}'
            + (f';stop-opacity:{fmt(c.alpha)}"/>' if c.alpha < 0.999 else '"/>')
            for pos, c in self.stops(grad)
        )
        path = grad.find("a:path", NS)
        if path is not None:
            focus = path.find("a:fillToRect", NS)
            l, t, r, b = (percent(focus.get(k), 0.0) if focus is not None else 0.0 for k in "ltrb")
            cx, cy = (l + 1 - r) / 2, (t + 1 - b) / 2
            defs.append(f'<radialGradient id="{gid}" cx="{fmt(cx)}" cy="{fmt(cy)}" r="0.71">{stops}</radialGradient>')
        else:
            linear = grad.find("a:lin", NS)
            angle = math.radians(num(linear.get("ang")) / 60_000 if linear is not None else 0)
            w, h = box.width, box.height
            half = (abs(w * math.cos(angle)) + abs(h * math.sin(angle))) / 2
            dx, dy = math.cos(angle) * half, math.sin(angle) * half
            defs.append(
                f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{fmt(w / 2 - dx)}" y1="{fmt(h / 2 - dy)}" '
                f'x2="{fmt(w / 2 + dx)}" y2="{fmt(h / 2 + dy)}">{stops}</linearGradient>'
            )
        return f"url(#{gid})"

    def fill_css(self, sp_pr, style, box: Box, defs: list[str]) -> str:
        """CSS fill for the shape's geometry, or "" when it has none."""
        fill = first_child(sp_pr, FILL_TAGS)
        if fill is None:
            ref = style.find("a:fillRef", NS) if style is not None else None
            if ref is None or ref.get("idx", "0") == "0":
                return ""
            color = self.s.colors.fill_color(ref)
            return self.paint("fill", color) if color is not None else ""
        kind = local_name(fill)
        if kind == "solidFill":
            color = self.s.colors.fill_color(fill)
            return self.paint("fill", color) if color is not None else ""
        if kind == "gradFill":
            return f"fill:{self.svg_gradient(fill, box, defs)}" if self.stops(fill) else ""
        if kind == "pattFill":
            self.warn("pattern fill drawn with its foreground color")
            color = self.s.colors.fill_color(fill.find("a:fgClr", NS))
            return self.paint("fill", color) if color is not None else ""
        if kind == "grpFill":
            self.warn("group fill is not converted")
        return ""

    def paint(self, prop: str, color: Color) -> str:
        css = f"{prop}:{self.css_color(color, alpha=False)}"
        return css + (f";{prop}-opacity:{fmt(color.alpha)}" if color.alpha < 0.999 else "")

    def outline_color(self, sp_pr, style) -> Color | None:
        """The color of the shape's outline, or None when it has no visible outline."""
        line = sp_pr.find("a:ln", NS) if sp_pr is not None else None
        fill = first_child(line, LINE_FILL_TAGS)
        if fill is not None:
            kind = local_name(fill)
            if kind == "noFill":
                return None
            if kind == "gradFill":
                stops = self.stops(fill)
                return stops[0][1] if stops else None
            return self.s.colors.fill_color(fill if kind == "solidFill" else fill.find("a:fgClr", NS))
        ref = style.find("a:lnRef", NS) if style is not None else None
        if ref is None or ref.get("idx", "0") == "0":
            return None
        return self.s.colors.fill_color(ref)

    def stroke_css(self, sp_pr, style) -> str:
        """CSS stroke for the shape's outline, or "" when it has none."""
        color = self.outline_color(sp_pr, style)
        if color is None:
            return ""
        line = sp_pr.find("a:ln", NS) if sp_pr is not None else None
        width = num(line.get("w"), DEFAULT_LINE_WIDTH) if line is not None else DEFAULT_LINE_WIDTH
        width_px = max(width / EMU_PER_PX, 1.0) if width else 1.0
        css = [self.paint("stroke", color), f"stroke-width:{fmt(width_px)}"]
        cap = LINE_CAPS.get(line.get("cap", "flat") if line is not None else "flat", "butt")
        if cap != "butt":
            css.append(f"stroke-linecap:{cap}")
        if line is not None:
            if line.find("a:bevel", NS) is not None:
                css.append("stroke-linejoin:bevel")
            elif line.find("a:miter", NS) is None:
                css.append("stroke-linejoin:round")
            dash = line.find("a:prstDash", NS)
            pattern = DASHES.get(dash.get("val", "solid")) if dash is not None else None
            custom = line.findall("a:custDash/a:ds", NS)
            if custom:
                pattern = tuple(v for ds in custom for v in (percent(ds.get("d"), 1.0), percent(ds.get("sp"), 1.0)))
            if pattern:
                css.append("stroke-dasharray:" + " ".join(fmt(v * width_px) for v in pattern))
            for end in ("a:headEnd", "a:tailEnd"):
                arrow = line.find(end, NS)
                if arrow is not None and arrow.get("type", "none") != "none":
                    self.warn("arrowheads are not drawn yet")
        return ";".join(css)

    # Images ---------------------------------------------------------------

    def image_asset(self, blip: ET.Element | None, part: str) -> tuple[str, str, str] | None:
        """(asset path, file type, package part) of a picture; the SVG version when there is one."""
        if blip is None:
            return None
        rels = self.pkg.rels(part)
        svg = blip.find("a:extLst/a:ext/asvg:svgBlip", NS)
        ids = ([svg.get(q("r:embed"), "")] if svg is not None else []) + [blip.get(q("r:embed"), "")]
        for rid in ids:
            rel = rels.get(rid)
            if rel is None or rel.external or rel.target not in self.pkg:
                continue
            ext = rel.target.rpartition(".")[2].lower()
            if ext not in WEB_IMAGES:
                self.warn(f"{posixpath.basename(rel.target)} is a .{ext} image, which browsers cannot show")
                continue
            asset = f"assets/images/{posixpath.basename(rel.target)}"
            self.assets[rel.target] = asset
            return asset, ext, rel.target
        return None

    def image_slot(self, ext: str, target: str, box: Box, frame: Frame) -> str:
        """data-slot for pictures the user replaces (photos, icons, the logo), data-role for decoration."""
        slide_box = frame.on_slide(box)
        emu_box = tuple(v * EMU_PER_PX for v in slide_box)
        small = box.width * box.height < ICON_AREA * self.slide_w * self.slide_h
        role = self.image_roles.get(self.index, {}).get(posixpath.basename(target))
        if role == "decoration":
            return 'data-role="decoration"'
        if role:
            kind = role
        elif small and (self.digest(target), _position_key(emu_box)) in self.brand_images:
            kind = "logo"
        elif ext in PHOTO_TYPES:
            kind = "photo"
        elif small:
            kind = "icon"
        else:
            return 'data-role="decoration"'
        slot = Slot(f"@@slot{next(self._slot_ids)}@@", kind, kind, *slide_box)
        self.slots.append(slot)
        return f'data-slot="{slot.key}"'

    def image(self, el, sp_pr, style, blip_fill: ET.Element, xfrm, box: Box, frame: Frame, part: str, key: str) -> str:
        blip = blip_fill.find("a:blip", NS)
        asset = self.image_asset(blip, part)
        if asset is None:
            return ""
        src, ext, target = asset
        alpha = blip.find("a:alphaModFix", NS)
        opacity = percent(alpha.get("amt")) if alpha is not None else 1.0
        if blip.find("a:duotone", NS) is not None or blip.find("a:clrChange", NS) is not None:
            self.warn("recolored images keep their original colors")
        if blip_fill.find("a:tile", NS) is not None:
            self.warn("tiled image fills are stretched instead")

        # Where the whole image goes once its crop (srcRect) maps onto the box.
        crop = blip_fill.find("a:srcRect", NS)
        cl, ct, cr, cb = (percent(crop.get(k), 0.0) if crop is not None else 0.0 for k in "ltrb")
        target_rect = blip_fill.find("a:stretch/a:fillRect", NS)
        fl, ft, fr, fb = (percent(target_rect.get(k), 0.0) if target_rect is not None else 0.0 for k in "ltrb")
        tx, ty = fl * box.width, ft * box.height
        tw, th = box.width * (1 - fl - fr), box.height * (1 - ft - fb)
        iw = tw / (1 - cl - cr) if 1 - cl - cr > 0 else tw
        ih = th / (1 - ct - cb) if 1 - ct - cb > 0 else th
        ix, iy = tx - cl * iw, ty - ct * ih
        cropped = any(abs(v) > 1e-4 for v in (cl, ct, cr, cb, fl, ft, fr, fb))

        attr = self.image_slot(ext, target, box, frame)
        custom = sp_pr.find("a:custGeom", NS) if sp_pr is not None else None
        preset = sp_pr.find("a:prstGeom", NS) if sp_pr is not None else None
        rectangular = (is_rectangle(custom) if custom is not None
                       else preset is None or preset.get("prst") == "rect")
        stroke = self.stroke_css(sp_pr, style)
        effects = [] if opacity >= 0.999 else [f"opacity:{fmt(opacity)}"]

        if rectangular and not stroke:
            css = box.css() + effects
            if not cropped:
                return f'<img class="el" {attr} src="{src}" alt="" style="{";".join(css)}">'
            if 'data-slot' in attr and fl == ft == fr == fb == 0:
                # Replaceable pictures use object-fit so a new image of any shape still fills the box.
                px = cl / (cl + cr) * 100 if cl + cr else 50
                py = ct / (ct + cb) * 100 if ct + cb else 50
                css += ["object-fit:cover", f"object-position:{fmt(px)}% {fmt(py)}%"]
                return f'<img class="el" {attr} src="{src}" alt="" style="{";".join(css)}">'
            inner = f"left:{fmt(ix)}px;top:{fmt(iy)}px;width:{fmt(iw)}px;height:{fmt(ih)}px"
            return (f'<div class="el" {attr} style="{";".join(css + ["overflow:hidden"])}">'
                    f'<img src="{src}" alt="" style="position:absolute;{inner};max-width:none"></div>')

        clip_id = self.new_id("c")
        if stroke:
            self.note_corners(sp_pr, style, box, frame, key, False)
        paths = self.paths(el, sp_pr, xfrm, box, key)
        clip = "".join(f'<path d="{p.d}"/>' for p in paths if p.fill)
        defs = [f'<clipPath id="{clip_id}">{clip}</clipPath>']
        items = [
            f'<image href="{src}" x="{fmt(ix)}" y="{fmt(iy)}" width="{fmt(iw)}" height="{fmt(ih)}" '
            f'preserveAspectRatio="none" clip-path="url(#{clip_id})"'
            + (f' opacity="{fmt(opacity)}"/>' if opacity < 0.999 else "/>")
        ]
        if stroke:
            items += [f'<path d="{p.d}" style="fill:none;{stroke}"/>' for p in paths if p.stroke]
        svg = self.svg(box, defs, items)
        return svg.replace('<svg class="el"', f'<svg class="el" {attr}', 1)

    # Text -----------------------------------------------------------------

    def text(self, el: ET.Element, body: ET.Element, box: Box, frame: Frame, style) -> str:
        paragraphs = body.findall("a:p", NS)
        if not any((t.text or "").strip() for p in paragraphs for t in p.iterfind(".//a:t", NS)):
            return ""  # Canva leaves empty text boxes inside many groups
        pr = body.find("a:bodyPr", NS)
        get = pr.get if pr is not None else (lambda key, default=None: default)
        insets = [num(get(key), default) / EMU_PER_PX for key, default in zip(("lIns", "tIns", "rIns", "bIns"), DEFAULT_INSETS)]
        anchor = get("anchor", "t")
        autofit = pr.find("a:normAutofit", NS) if pr is not None else None
        scale = percent(autofit.get("fontScale"), 1.0) if autofit is not None else 1.0

        containers = self.c.text_containers(el, self.s)
        font_ref = style.find("a:fontRef", NS) if style is not None else None
        shape_color = self.s.colors.fill_color(font_ref)
        numbering: Counter = Counter()
        lines, classes, first_style = [], Counter(), None
        for i, para in enumerate(paragraphs):
            markup, cls, chars, main_style = self.paragraph(para, containers, shape_color, scale, numbering, first=i == 0)
            lines.append(markup)
            classes[cls] += chars
            if chars and first_style is None:
                first_style = main_style

        css = box.css(height=anchor in ANCHOR, flips=False)
        if any(insets):
            css.append("padding:" + " ".join(f"{fmt(v)}px" for v in (insets[1], insets[2], insets[3], insets[0])))
        if anchor in ANCHOR:
            css.append(f"justify-content:{ANCHOR[anchor]}")
        if get("wrap", "square") == "none":
            css.append("white-space:nowrap")
        columns = int(num(get("numCol"), 1))
        if columns > 1:
            css += [f"display:block;column-count:{columns}", f"column-gap:{fmt(num(get('spcCol')) / EMU_PER_PX)}px"]
        if get("vert", "horz") not in ("horz", None):
            css.append("writing-mode:vertical-rl")
            self.warn("vertical text is approximated")

        main_class = classes.most_common(1)[0][0] if classes else None
        text = _text_of(el)
        slide_box = frame.on_slide(box)
        emu_box = tuple(v * EMU_PER_PX for v in slide_box)
        if (text, _position_key(emu_box)) in self.brand_texts:
            base = "brand"
        else:
            base = main_class.removeprefix("text-") if main_class else "text"
        slot = Slot(f"@@slot{next(self._slot_ids)}@@", "text", base, *slide_box, style=main_class, text=text,
                    turned=frame.turned or bool(box.rotation))
        if first_style is not None:
            slot.family, slot.size_px, slot.line_height = first_style.family, first_style.size_px * scale, first_style.line_height
        self.slots.append(slot)
        return f'<div class="text" data-slot="{slot.key}" style="{";".join(css)}">\n{_indent(lines)}\n</div>'

    def paragraph(self, para: ET.Element, containers, shape_color, scale: float, numbering: Counter, first: bool):
        """(<p> markup, its text style class, its number of characters, its main TextStyle)."""
        ppr = para.find("a:pPr", NS)
        lvl = min(max(int(num(ppr.get("lvl") if ppr is not None else None)) + 1, 1), 9)
        runs = []  # ("br", None...) or ("run", text, style, color, rPr, hyperlink)
        for child in para:
            name = local_name(child)
            if name == "br":
                runs.append(("br", "", None, None, None))
            elif name in ("r", "fld"):
                text = "".join(t.text or "" for t in child.iterfind("a:t", NS))
                if text:
                    rpr = child.find("a:rPr", NS)
                    style, color = self.c.run_style(self.s, rpr, ppr, lvl, containers, shape_color)
                    runs.append(("run", text, style, color, rpr))
        text_runs = [r for r in runs if r[0] == "run"]
        if text_runs:
            weights: Counter = Counter()
            for _, text, style, color, _ in text_runs:
                weights[(style, color)] += len(text.strip()) or 1
            main_style, main_color = weights.most_common(1)[0][0]
        else:  # an empty paragraph still takes up a line, at its end-of-paragraph size
            main_style, main_color = self.c.run_style(self.s, para.find("a:endParaRPr", NS), ppr, lvl, containers, shape_color)

        cls = self.c.style_classes.get(main_style)
        info = self.styles.get(cls) if cls else None
        css = self.style_css(main_style, info, scale)
        if main_color is not None and (info is None or main_color.hex != info["color"] or main_color.alpha < 0.999):
            css.append(f"color:{self.css_color(main_color)}")
        sizes = {r[2].size_px for r in text_runs}
        if len(sizes) > 1:  # mixed sizes: keep PowerPoint's fixed line pitch
            css = [c for c in css if not c.startswith("line-height:")]
            css.append(f"line-height:{fmt(main_style.line_height * main_style.size_px * scale)}px")

        def lookup(tag: str):
            el = ppr.find(f"a:{tag}", NS) if ppr is not None else None
            return el if el is not None else level_lookup(containers, lvl, f"a:{tag}")

        def attr(name: str):
            value = ppr.get(name) if ppr is not None else None
            return value if value is not None else level_lookup(containers, lvl, "", name)

        align = attr("algn") or "l"
        if align in ALIGN:
            css.append(f"text-align:{ALIGN[align]}")
        for tag, prop in (("spcBef", "margin-top"), ("spcAft", "margin-bottom")):
            if tag == "spcBef" and first:
                continue
            spacing = lookup(tag)
            points = spacing.find("a:spcPts", NS) if spacing is not None else None
            pct = spacing.find("a:spcPct", NS) if spacing is not None else None
            if points is not None and num(points.get("val")):
                css.append(f"{prop}:{fmt(num(points.get('val')) / 100 * 96 / 72)}px")
            elif pct is not None and percent(pct.get("val"), 0.0):
                css.append(f"{prop}:{fmt(percent(pct.get('val'), 0.0) * SINGLE_LINE_EM)}em")
        margin = num(attr("marL")) / EMU_PER_PX
        indent = num(attr("indent")) / EMU_PER_PX
        bullet = self.bullet(ppr, lvl, containers, main_color, numbering, indent) if text_runs else ""
        if margin:
            css.append(f"padding-left:{fmt(margin)}px")
        if indent:
            css.append(f"text-indent:{fmt(indent)}px")

        parts = [bullet]
        for kind, text, style, color, rpr in runs:
            if kind == "br":
                parts.append("<br>")
                continue
            span = self.run_css(style, color, rpr, main_style, main_color, scale)
            content = _escape(text)
            parts.append(f'<span style="{";".join(span)}">{content}</span>' if span else content)
            link = rpr.find("a:hlinkClick", NS) if rpr is not None else None
            if link is not None:
                rel = self.pkg.rels(self.part).get(link.get(q("r:id"), ""))
                if rel is not None and rel.external:
                    parts[-1] = f'<a href="{html.escape(rel.target)}">{parts[-1]}</a>'
        if text_runs and runs[-1][0] == "br":
            parts.append("<br>")  # PowerPoint gives a final line break an empty line; HTML needs a second <br>
        content = "".join(parts) if text_runs else "<br>"
        attrs = (f' class="{cls}"' if cls else "") + (f' style="{";".join(css)}"' if css else "")
        chars = sum(len(r[1].strip()) for r in text_runs)
        return f"<p{attrs}>{content}</p>", cls, chars, main_style

    def font_css(self, family: str) -> str:
        var = self.font_vars.get(family)
        return f"var({var})" if var else f"{_css_name(family)}, sans-serif"

    def style_css(self, style, info: dict | None, scale: float) -> list[str]:
        """What a paragraph's main style changes from its class (everything without a class)."""
        css = []
        if info is None or style.family != info["family"]:
            css.append(f"font-family:{self.font_css(style.family)}")
        if info is None or style.weight != info["weight"]:
            css.append(f"font-weight:{style.weight}")
        if info is None and style.italic or info is not None and style.italic != info["italic"]:
            css.append(f"font-style:{'italic' if style.italic else 'normal'}")
        size = style.size_px * scale
        if info is None or abs(size - info["size_px"]) > 0.005:
            css.append(f"font-size:{fmt(size)}px")
        if info is None or abs(style.line_height - info["line_height"]) > 0.0005:
            css.append(f"line-height:{style.line_height:g}")
        if info is None and style.letter_spacing_em or info is not None and abs(style.letter_spacing_em - info["letter_spacing_em"]) > 0.00005:
            css.append(f"letter-spacing:{style.letter_spacing_em:g}em")
        caps = style.caps if info is None else (style.caps if style.caps != info["caps"] else None)
        if caps == "all":
            css.append("text-transform:uppercase")
        elif caps == "small":
            css.append("font-variant-caps:small-caps")
        elif caps == "none" and info is not None:
            css.append("text-transform:none")
        return css

    def run_css(self, style, color, rpr, main_style, main_color, scale: float) -> list[str]:
        """What one run changes from its paragraph."""
        css = []
        if style.family != main_style.family:
            css.append(f"font-family:{self.font_css(style.family)}")
        if style.weight != main_style.weight:
            css.append(f"font-weight:{style.weight}")
        if style.italic != main_style.italic:
            css.append(f"font-style:{'italic' if style.italic else 'normal'}")
        if abs(style.size_px - main_style.size_px) > 0.005:
            css.append(f"font-size:{fmt(style.size_px * scale)}px")
        if abs(style.letter_spacing_em - main_style.letter_spacing_em) > 0.00005:
            css.append(f"letter-spacing:{style.letter_spacing_em:g}em")
        if style.caps != main_style.caps:
            css.append({"all": "text-transform:uppercase", "small": "font-variant-caps:small-caps"}.get(style.caps, "text-transform:none"))
        if color is not None and (main_color is None or color != main_color):
            css.append(f"color:{self.css_color(color)}")
        if rpr is not None:
            decorations = []
            if rpr.get("u", "none") != "none":
                decorations.append("underline")
            if rpr.get("strike", "noStrike") != "noStrike":
                decorations.append("line-through")
            if decorations:
                css.append("text-decoration:" + " ".join(decorations))
            baseline = num(rpr.get("baseline"))
            if baseline:
                css += [f"vertical-align:{'super' if baseline > 0 else 'sub'}", "font-size:0.65em"]
            highlight = self.s.colors.fill_color(rpr.find("a:highlight", NS))
            if highlight is not None:
                css.append(f"background:{self.css_color(highlight)}")
        return css

    def bullet(self, ppr, lvl: int, containers, text_color, numbering: Counter, indent: float) -> str:
        """The bullet <span> of a paragraph, or "" when it has none."""
        sources = ([ppr] if ppr is not None else []) + [
            c.find(f"a:lvl{lvl}pPr", NS) for c in containers if c.find(f"a:lvl{lvl}pPr", NS) is not None
        ]

        def find(tag: str):
            for source in sources:
                el = source.find(f"a:{tag}", NS)
                if el is not None:
                    return el
            return None

        kind = None
        for source in sources:  # the most specific bullet setting wins
            for tag in ("buNone", "buChar", "buAutoNum", "buBlip"):
                el = source.find(f"a:{tag}", NS)
                if el is not None:
                    kind = (tag, el)
                    break
            if kind:
                break
        if kind is None or kind[0] in ("buNone", "buBlip"):
            numbering.clear()
            return ""
        tag, el = kind
        css = []
        if tag == "buChar":
            char = el.get("char", "\u2022")
            font = find("buFont")
            typeface = font.get("typeface") if font is not None else None
            if typeface and not typeface.startswith("+"):
                css.append(f"font-family:{_css_name(typeface)}")
        else:
            numbering[lvl] += 1
            n = numbering[lvl] + int(num(el.get("startAt"), 1)) - 1
            pattern = AUTO_NUMBERS.get(el.get("type", "arabicPeriod"), "{n}.")
            char = pattern.format(n=n, a=_letters(n), A=_letters(n).upper(), i=_roman(n), I=_roman(n).upper())
        color_el = find("buClr")
        color = self.s.colors.fill_color(color_el) if color_el is not None else None
        if color is not None and color != text_color:
            css.append(f"color:{self.css_color(color)}")
        size = find("buSzPct")
        if size is not None and abs(percent(size.get("val")) - 1) > 0.01:
            css.append(f"font-size:{fmt(percent(size.get('val')))}em")
        if indent < 0:
            css.append(f"width:{fmt(-indent)}px")
        else:
            css.append("margin-right:0.5em")
        return f'<span class="bullet" style="{";".join(css)}">{html.escape(char)}</span>'

    # Slots ----------------------------------------------------------------

    def name_slots(self) -> None:
        """Name slots after their style or kind, numbered in reading order when repeated."""
        groups: dict[str, list[Slot]] = defaultdict(list)
        for slot in self.slots:
            groups[slot.base].append(slot)
        for base, slots in groups.items():
            slots.sort(key=lambda s: (round(s.top / 12), s.left))
            for i, slot in enumerate(slots, start=1):
                slot.name = base if len(slots) == 1 else f"{base}-{i}"

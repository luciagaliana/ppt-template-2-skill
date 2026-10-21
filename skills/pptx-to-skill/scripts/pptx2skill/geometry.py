"""DrawingML geometry -> SVG path data, in the shape's own px space."""

from __future__ import annotations

import math
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from .ooxml import NS, local_name, num

ANGLE = 60_000  # DrawingML angles are in 60000ths of a degree

# Dash patterns in multiples of the line width, as PowerPoint draws them.
DASHES = {
    "sysDot": (1, 1), "sysDash": (3, 1), "sysDashDot": (3, 1, 1, 1), "sysDashDotDot": (3, 1, 1, 1, 1, 1),
    "dot": (1, 3), "dash": (4, 3), "lgDash": (8, 3), "dashDot": (4, 3, 1, 3),
    "lgDashDot": (8, 3, 1, 3), "lgDashDotDot": (8, 3, 1, 3, 1, 3),
}
LINE_PRESETS = {"line", "straightConnector1"}


def fmt(value: float) -> str:
    """Shortest number with 2 decimals at most: 108, 27.43, -0.5."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


@dataclass
class SvgPath:
    d: str
    fill: bool = True
    stroke: bool = True


class PathBuilder:
    """Builds SVG path data from DrawingML commands given in path units, scaled to px."""

    def __init__(self, sx: float, sy: float):
        self.sx, self.sy = sx, sy
        self.parts: list[str] = []
        self.x = self.y = 0.0  # current point, in path units

    def _pt(self, x: float, y: float) -> str:
        return f"{fmt(x * self.sx)} {fmt(y * self.sy)}"

    def move(self, x: float, y: float) -> None:
        self.parts.append(f"M{self._pt(x, y)}")
        self.x, self.y = x, y

    def line(self, x: float, y: float) -> None:
        self.parts.append(f"L{self._pt(x, y)}")
        self.x, self.y = x, y

    def cubic(self, x1: float, y1: float, x2: float, y2: float, x: float, y: float) -> None:
        self.parts.append(f"C{self._pt(x1, y1)} {self._pt(x2, y2)} {self._pt(x, y)}")
        self.x, self.y = x, y

    def quad(self, x1: float, y1: float, x: float, y: float) -> None:
        self.parts.append(f"Q{self._pt(x1, y1)} {self._pt(x, y)}")
        self.x, self.y = x, y

    def arc(self, wr: float, hr: float, start: float, sweep: float) -> None:
        """DrawingML arcTo: an arc of the ellipse (wr, hr) that starts at the current point.

        Angles are in degrees, clockwise; DrawingML gives them as seen on screen, so they
        are turned into the ellipse's parametric angles first.
        """
        if not wr or not hr or not sweep:
            return

        def parametric(degrees: float) -> float:
            a = math.radians(degrees)
            return math.atan2(wr * math.sin(a), hr * math.cos(a))

        t0 = parametric(start)
        cx, cy = self.x - wr * math.cos(t0), self.y - hr * math.sin(t0)
        steps = [sweep] if abs(sweep) < 360 else [sweep / 2, sweep / 2]  # SVG cannot draw a full turn in one arc
        angle = start
        for step in steps:
            angle += step
            t = parametric(angle)
            x, y = cx + wr * math.cos(t), cy + hr * math.sin(t)
            large = 1 if abs(step) > 180 else 0
            clockwise = 1 if step > 0 else 0
            self.parts.append(f"A{fmt(wr * self.sx)} {fmt(hr * self.sy)} 0 {large} {clockwise} {self._pt(x, y)}")
            self.x, self.y = x, y

    def close(self) -> None:
        self.parts.append("Z")

    def d(self) -> str:
        return " ".join(self.parts)


def _value(text: str | None, guides: dict[str, float]) -> float:
    """A coordinate: a number, or the name of a guide (w, h, or one from a:gdLst)."""
    if text is None:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return guides.get(text, 0.0)


def custom_paths(geom: ET.Element, width: float, height: float, shape_w: float, shape_h: float) -> list[SvgPath]:
    """Every a:path of an a:custGeom as SVG path data in px.

    `width`/`height` are the shape's size in px; `shape_w`/`shape_h` its size in its own
    coordinate units, which paths without w/h attributes use.
    """
    guides = {"w": shape_w, "h": shape_h, "l": 0.0, "t": 0.0, "r": shape_w, "b": shape_h,
              "hc": shape_w / 2, "vc": shape_h / 2, "wd2": shape_w / 2, "hd2": shape_h / 2}
    for gd in geom.iterfind("a:gdLst/a:gd", NS):
        parts = (gd.get("fmla") or "").split()
        if len(parts) == 2 and parts[0] == "val":
            guides[gd.get("name", "")] = _value(parts[1], guides)
    paths = []
    for path in geom.iterfind("a:pathLst/a:path", NS):
        pw = num(path.get("w")) or shape_w
        ph = num(path.get("h")) or shape_h
        builder = PathBuilder(width / pw if pw else 0.0, height / ph if ph else 0.0)
        for cmd in path:
            name = local_name(cmd)
            pts = [(_value(p.get("x"), guides), _value(p.get("y"), guides)) for p in cmd.iterfind("a:pt", NS)]
            if name == "moveTo" and pts:
                builder.move(*pts[0])
            elif name == "lnTo" and pts:
                builder.line(*pts[0])
            elif name == "cubicBezTo" and len(pts) == 3:
                builder.cubic(*pts[0], *pts[1], *pts[2])
            elif name == "quadBezTo" and len(pts) == 2:
                builder.quad(*pts[0], *pts[1])
            elif name == "arcTo":
                builder.arc(
                    _value(cmd.get("wR"), guides), _value(cmd.get("hR"), guides),
                    _value(cmd.get("stAng"), guides) / ANGLE, _value(cmd.get("swAng"), guides) / ANGLE,
                )
            elif name == "close":
                builder.close()
        paths.append(SvgPath(builder.d(), fill=path.get("fill", "norm") != "none", stroke=path.get("stroke") not in ("0", "false")))
    return paths


def is_rectangle(geom: ET.Element) -> bool:
    """Whether an a:custGeom is just its own bounding box, as Canva writes images."""
    paths = geom.findall("a:pathLst/a:path", NS)
    if len(paths) != 1:
        return False
    path = paths[0]
    w, h = num(path.get("w")), num(path.get("h"))
    corners = {(0, 0), (1, 0), (1, 1), (0, 1)}
    seen = set()
    for cmd in path:
        name = local_name(cmd)
        if name == "close":
            continue
        if name not in ("moveTo", "lnTo"):
            return False
        pt = cmd.find("a:pt", NS)
        x, y = num(pt.get("x")) if pt is not None else -1, num(pt.get("y")) if pt is not None else -1
        corner = (round(x / w) if w else -1, round(y / h) if h else -1)
        if corner not in corners or abs(x - corner[0] * w) > 2 or abs(y - corner[1] * h) > 2:
            return False
        seen.add(corner)
    return seen == corners


def rounded_rect_radius(geom: ET.Element) -> float | None:
    """Corner radius, in path units, of an a:custGeom drawn as a rounded rectangle (Canva cards)."""
    paths = geom.findall("a:pathLst/a:path", NS)
    if len(paths) != 1:
        return None
    path = paths[0]
    w, h = num(path.get("w")), num(path.get("h"))
    commands = [(local_name(c), [(num(p.get("x")), num(p.get("y"))) for p in c.iterfind("a:pt", NS)]) for c in path]
    if not w or not h or len(commands) < 10 or commands[0][0] != "moveTo" or commands[-1][0] != "close":
        return None
    r, top = commands[0][1][0]
    if top != 0 or not 0 < r <= min(w, h) / 2 + 2:
        return None
    # Four edges, each a straight line followed by one or more curves that turn the corner.
    edges: list[list[tuple[str, list]]] = []
    for name, points in commands[1:-1]:
        if name == "lnTo" and points:
            edges.append([(name, points)])
        elif name in ("cubicBezTo", "quadBezTo") and points and edges:
            edges[-1].append((name, points))
        else:
            return None
    if len(edges) != 4 or any(len(edge) < 2 for edge in edges):
        return None
    ends = [p for edge in edges for p in (edge[0][1][-1], edge[-1][1][-1])]
    corners = [(w - r, 0), (w, r), (w, h - r), (w - r, h), (r, h), (0, h - r), (0, r), (r, 0)]
    tolerance = max(w, h) * 0.002 + 2
    if all(abs(x - cx) <= tolerance and abs(y - cy) <= tolerance for (x, y), (cx, cy) in zip(ends, corners)):
        return r
    return None


def _adjust(prst_geom: ET.Element, name: str, default: float) -> float:
    for gd in prst_geom.iterfind("a:avLst/a:gd", NS):
        parts = (gd.get("fmla") or "").split()
        if gd.get("name") == name and len(parts) == 2 and parts[0] == "val":
            return num(parts[1], default)
    return default


def rounded_rect_path(w: float, h: float, r: float) -> str:
    r = max(0.0, min(r, w / 2, h / 2))
    if not r:
        return f"M0 0 H{fmt(w)} V{fmt(h)} H0 Z"
    return (
        f"M{fmt(r)} 0 H{fmt(w - r)} A{fmt(r)} {fmt(r)} 0 0 1 {fmt(w)} {fmt(r)} "
        f"V{fmt(h - r)} A{fmt(r)} {fmt(r)} 0 0 1 {fmt(w - r)} {fmt(h)} "
        f"H{fmt(r)} A{fmt(r)} {fmt(r)} 0 0 1 0 {fmt(h - r)} "
        f"V{fmt(r)} A{fmt(r)} {fmt(r)} 0 0 1 {fmt(r)} 0 Z"
    )


def preset_path(prst_geom: ET.Element, w: float, h: float) -> str | None:
    """SVG path data for the common preset shapes; None for the ones not drawn yet."""
    prst = prst_geom.get("prst", "rect")
    if prst == "rect":
        return f"M0 0 H{fmt(w)} V{fmt(h)} H0 Z"
    if prst == "roundRect":
        return rounded_rect_path(w, h, min(w, h) * _adjust(prst_geom, "adj", 16667) / 100_000)
    if prst == "ellipse":
        rx, ry = w / 2, h / 2
        return (f"M0 {fmt(ry)} A{fmt(rx)} {fmt(ry)} 0 1 1 {fmt(w)} {fmt(ry)} "
                f"A{fmt(rx)} {fmt(ry)} 0 1 1 0 {fmt(ry)} Z")
    if prst == "triangle":
        apex = w * _adjust(prst_geom, "adj", 50000) / 100_000
        return f"M{fmt(apex)} 0 L{fmt(w)} {fmt(h)} H0 Z"
    if prst == "rtTriangle":
        return f"M0 0 L{fmt(w)} {fmt(h)} H0 Z"
    if prst == "diamond":
        return f"M{fmt(w / 2)} 0 L{fmt(w)} {fmt(h / 2)} L{fmt(w / 2)} {fmt(h)} L0 {fmt(h / 2)} Z"
    if prst == "parallelogram":
        dx = min(w, h) * _adjust(prst_geom, "adj", 25000) / 100_000
        return f"M{fmt(dx)} 0 H{fmt(w)} L{fmt(w - dx)} {fmt(h)} H0 Z"
    return None

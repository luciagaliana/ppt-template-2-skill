"""Use the template's PDF as ground truth to correct what the .pptx alone gets wrong.

- Corner radii: Canva's PowerPoint export shrinks the rounding of rounded rectangles, so
  each one is measured on the reference image.
- Text baseline: every app places text inside its line height a little differently from
  browsers. The shift is measured on every text box and fitted, per font family, as
  shift = A * line height + B * font size, which theme.css applies through CSS variables.
"""

from __future__ import annotations

import io
import math
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from .colors import Color

COLOR_TOLERANCE = 60  # max channel distance for a pixel to count as the outline color


def reference_images(pdf: Path, width: int) -> list[Image.Image]:
    """Every page of the reference PDF as an RGB image `width` px wide."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf))
    try:
        return [page.render(scale=width / page.get_width()).to_pil().convert("RGB") for page in document]
    finally:
        document.close()


# Corner radii -------------------------------------------------------------

def _near(pixel: tuple, color: tuple, tolerance: int) -> bool:
    return max(abs(a - b) for a, b in zip(pixel, color)) <= tolerance


def _rgb(color: str) -> tuple[int, int, int]:
    c = Color.from_hex(color)
    return round(c.r * 255), round(c.g * 255), round(c.b * 255)


def _crossing(image: Image.Image, corner: tuple[float, float, int, int], limit: int, target: tuple,
              outline: bool, inside: list[tuple], stroke: float) -> float | None:
    """Steps along the corner's diagonal until the shape's edge, or None if not found.

    For an outline, the run of outline-colored pixels must be followed by a fill color or be
    about as thin as the stroke, so photos behind the shape count less often.
    """
    cx, cy, sx, sy = corner

    def pixel(t: int) -> tuple | None:
        px, py = round(cx + sx * t), round(cy + sy * t)
        return image.getpixel((px, py)) if 0 <= px < image.width and 0 <= py < image.height else None

    run_start = None
    for t in range(limit):
        value = pixel(t)
        if value is None:
            return None
        if _near(value, target, COLOR_TOLERANCE):
            if not outline:
                return max(t - 0.5, 0.0)
            run_start = t if run_start is None else run_start
            continue
        if run_start is not None:
            run_end = t - 1
            after = [pixel(t + k) for k in (0, 1, 2)]
            if any(sum(1 for v in after if v is not None and _near(v, color, 40)) >= 2 for color in inside):
                # The fill starts right after: the outline is the inner end of the run (light
                # photo pixels can sit just outside it and lengthen the run).
                return max(run_end + 0.5 - stroke / math.sqrt(2) / 2, 0.0)
            if run_end - run_start <= 2 * stroke + 3:
                return (run_start + run_end) / 2
            run_start = None
    return None


def measure_radius(image: Image.Image, box: tuple[float, float, float, float], color: str, outline: bool,
                   fills: list[str] | None = None, stroke: float = 1.0) -> float | None:
    """Corner radius of a rounded rectangle on the reference image, or None if unclear.

    From each corner of the bounding box, walks along the diagonal towards the center until
    it meets the outline (or the fill). A corner of radius R crosses the diagonal at
    R * (1 - 1/sqrt(2)) steps of (1, 1).
    """
    target = _rgb(color)
    inside = [_rgb(fill) for fill in fills or [] if fill != color]
    x, y, w, h = box
    limit = int(min(w, h) / 2)
    radii = []
    for corner in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        crossing = _crossing(image, corner, limit, target, outline, inside, stroke)
        if crossing is not None:
            radii.append(crossing / (1 - 1 / math.sqrt(2)))
    if len(radii) < 3:
        return None
    radii.sort()
    median = radii[len(radii) // 2]
    close = [r for r in radii if abs(r - median) <= 0.15 * median + 3]
    return sum(close) / len(close) if len(close) >= 3 else None


def corner_fixes(candidates, references: list[Image.Image]) -> dict[str, float]:
    """Radius (px) for every rounded rectangle whose measured corners differ from the .pptx."""
    fixes = {}
    for c in candidates:
        if c.slide > len(references):
            continue
        measured = measure_radius(references[c.slide - 1], c.box, c.color, c.outline, c.fills, c.stroke)
        if measured is not None and abs(measured - c.radius) > max(3.0, 0.15 * c.radius):
            fixes[c.key] = round(min(measured, c.box[2] / 2, c.box[3] / 2), 1)
    return fixes


# Text baseline ------------------------------------------------------------

def cost(reference: Image.Image, rendered: Image.Image, box: tuple[int, int, int, int], dx: int, dy: int) -> float:
    """Mean difference between a region of `reference` and the same region of `rendered` moved by (dx, dy)."""
    x, y, w, h = box
    a = reference.crop((x, y, x + w, y + h))
    b = rendered.crop((x + dx, y + dy, x + dx + w, y + dy + h))
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def best_shift(reference: Image.Image, rendered: Image.Image, box, span: int, span_x: int | None = None) -> tuple[int, int, float]:
    """(dx, dy, cost) that best aligns `rendered` with `reference` in `box` (grayscale images)."""
    span_x = span if span_x is None else span_x
    dx = dy = 0
    for _ in range(3):  # alternate the axes until they settle
        dy = min(range(-span, span + 1), key=lambda v: cost(reference, rendered, box, dx, v))
        dx = min(range(-span_x, span_x + 1), key=lambda v: cost(reference, rendered, box, v, dy))
    return dx, dy, cost(reference, rendered, box, dx, dy)


def render_layouts(out: Path, files: dict[int, str], width: int, height: int) -> dict[int, Image.Image]:
    """Screenshots of the layout files (index -> image), or {} when Playwright is not installed."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}
    images = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            for index, file in files.items():
                page.goto((out / file).resolve().as_uri() + "?static")
                page.evaluate("document.fonts.ready.then(() => true)")
                png = page.locator("section.slide").first.screenshot()
                images[index] = Image.open(io.BytesIO(png)).convert("RGB")
        finally:
            browser.close()
    return images


def text_samples(slots_by_layout: dict[int, list], renders: dict[int, Image.Image],
                 references: list[Image.Image]) -> list[tuple[str, float, float, float]]:
    """(font family, font size px, line height px, shift down needed px) for each measurable text box."""
    samples = []
    for index, slots in slots_by_layout.items():
        if index not in renders or index > len(references):
            continue
        reference = references[index - 1].convert("L")
        rendered = renders[index].convert("L").resize(reference.size)
        for slot in slots:
            if slot.kind != "text" or slot.turned or not slot.size_px or slot.height < 12:
                continue
            x0, y0 = max(int(slot.left) - 6, 0), max(int(slot.top) - 6, 0)
            x1 = min(int(slot.left + slot.width) + 6, reference.width)
            y1 = min(int(slot.top + slot.height) + 6, reference.height)
            span = max(4, round(0.15 * slot.size_px))
            if x1 - x0 < 12 or y1 - y0 < 12 or y0 - span < 0 or y1 + span > reference.height:
                continue
            box = (x0, y0, x1 - x0, y1 - y0)
            before = cost(reference, rendered, box, 0, 0)
            dx, dy, after = best_shift(reference, rendered, box, span, 4)
            if after < before * 0.8 or dy == 0:  # only trust shifts that clearly improve the match
                samples.append((slot.family, slot.size_px, slot.size_px * slot.line_height, float(-dy)))
    return samples


def _solve(points: list[tuple[float, float, float, float]]) -> tuple[float, float]:
    """Weighted least squares of shift = A * line height + B * size (weight: the size)."""
    sll = sum(s * lh * lh for _, s, lh, _ in points)
    sls = sum(s * lh * s for _, s, lh, _ in points)
    sss = sum(s * s * s for _, s, lh, _ in points)
    sld = sum(s * lh * d for _, s, lh, d in points)
    ssd = sum(s * s * d for _, s, lh, d in points)
    det = sll * sss - sls * sls
    if abs(det) < 1e-9 * max(sll * sss, 1):  # every sample has the same line height: one parameter
        return 0.0, ssd / sss if sss else 0.0
    return (sld * sss - ssd * sls) / det, (sll * ssd - sls * sld) / det


def fit_baseline(samples: list[tuple[str, float, float, float]]) -> dict[str, dict[str, float]]:
    """Per font family (and "*" for all), {"lh": A, "em": B} with shift = A * 1lh + B * 1em."""
    if len(samples) < 3:
        return {}

    def robust(points):
        a, b = _solve(points)
        residuals = sorted(abs(d - a * lh - b * s) for _, s, lh, d in points)
        limit = max(2.0, 3 * residuals[len(residuals) // 2])
        kept = [p for p in points if abs(p[3] - a * p[2] - b * p[1]) <= limit]
        return _solve(kept) if len(kept) >= 3 else (a, b)

    fits = {}
    a, b = robust(samples)
    fits["*"] = {"lh": round(a, 4), "em": round(b, 4)}
    by_family = defaultdict(list)
    for sample in samples:
        by_family[sample[0]].append(sample)
    for family, points in by_family.items():
        if len(points) >= 4:
            a, b = robust(points)
            fits[family] = {"lh": round(a, 4), "em": round(b, 4)}
    return fits

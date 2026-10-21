"""Assemble a generated skill: layouts, assets, previews, the deck runtime and SKILL.md."""

from __future__ import annotations

import html
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .calibrate import corner_fixes, fit_baseline, reference_images, render_layouts, text_samples
from .design import Design, load_template
from .slides import Layout, Slot, SlideConverter
from .writers import _json, theme_css, write_all

SKELETON = Path(__file__).resolve().parents[2] / "skeleton"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PREVIEW_WIDTH = 960
GENERATED = ("layouts", "previews", "references")  # rebuilt from scratch on every build

LAYOUT_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="../">
<title>{title}</title>
<link rel="stylesheet" href="theme.css">
<link rel="stylesheet" href="deck/deck.css">
</head>
<body>
<main class="deck">
{section}
</main>
<script src="deck/deck.js"></script>
</body>
</html>
"""


@dataclass
class BuiltLayout:
    layout: Layout
    name: str  # "06-discover-our-services"
    use: str  # what the layout is for
    file: str  # "layouts/06-discover-our-services.html"
    preview: str | None  # "previews/06-discover-our-services.jpg"


def slug(text: str, words: int = 4) -> str:
    parts = re.findall(r"[a-z0-9]+", text.lower().replace("’", "").replace("'", ""))
    return "-".join(parts[:words]) or "slide"


def title_case(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def _default_name(layout: Layout, rank: dict[str, int]) -> str:
    """The layout's biggest text, as a slug: a starting point until someone names it."""
    texts = [s for s in layout.slots if s.kind == "text" and s.base != "brand" and s.text]
    if not texts:
        return "slide"
    biggest = min(texts, key=lambda s: (rank.get(s.style or "", 99), s.top))
    return slug(biggest.text)


def _default_use(layout: Layout) -> str:
    """A structural summary of the slots, e.g. "title, 3 body, 2 photo"."""
    counts: dict[str, int] = {}
    for slot in layout.slots:
        stem = slot.base if slot.kind == "text" else slot.kind
        counts[stem] = counts.get(stem, 0) + 1
    return ", ".join(stem if n == 1 else f"{n} {stem}" for stem, n in counts.items()) or "decoration only"


def build_skill(pptx: Path, out: Path, name: str, pdf: Path | None = None, descriptions: dict | None = None,
                calibrate: bool = True) -> tuple[list[BuiltLayout], list[str], Design, dict]:
    """Write a complete skill into `out`.

    Returns its layouts, the warnings, the design system and what the calibration against
    the reference PDF changed.
    """
    if not NAME_RE.match(name):
        raise ValueError(f"skill name '{name}' must be lowercase letters, digits and hyphens")
    descriptions = descriptions or {}
    described = descriptions.get("layouts", {})

    collector, design = load_template(pptx)
    tokens = design.tokens
    width, height = tokens["slide"]["width"], tokens["slide"]["height"]
    references = reference_images(pdf, width) if pdf else []
    parts = collector.pkg.slide_parts(collector.presentation)

    image_roles = {int(i): info["images"] for i, info in described.items() if info.get("images")}

    def convert(radius_fixes: dict[str, float]) -> tuple[SlideConverter, list[Layout]]:
        converter = SlideConverter(collector, design, radius_fixes, image_roles)
        return converter, [converter.convert(i, part, "@@layout@@") for i, part in enumerate(parts, start=1)]

    converter, layouts = convert({})
    corners = corner_fixes(converter.corner_candidates, references) if references and calibrate else {}
    if corners:
        converter, layouts = convert(corners)

    out.mkdir(parents=True, exist_ok=True)
    for folder in GENERATED:
        shutil.rmtree(out / folder, ignore_errors=True)
        (out / folder).mkdir()
    write_all(design, out)

    rank = {s["class"]: i for i, s in enumerate(tokens["text_styles"])}
    built: list[BuiltLayout] = []
    for layout in layouts:
        index = layout.index
        info = described.get(str(index), {})
        layout_name = f"{index:02d}-{slug(info.get('name') or _default_name(layout, rank), 6)}"
        layout.html = layout.html.replace("@@layout@@", layout_name)
        preview = f"previews/{layout_name}.jpg" if index <= len(references) else None
        item = BuiltLayout(layout, layout_name, info.get("use") or _default_use(layout), f"layouts/{layout_name}.html", preview)
        title = f"{layout_name} · {descriptions.get('title') or title_case(name)}"
        (out / item.file).write_text(LAYOUT_PAGE.format(title=html.escape(title), section=item.layout.html), encoding="utf-8")
        if preview:
            reference = references[index - 1]
            reference.resize((PREVIEW_WIDTH, round(reference.height * PREVIEW_WIDTH / reference.width))).save(out / preview, quality=85)
        built.append(item)

    for part, asset in converter.assets.items():
        path = out / asset
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(collector.pkg.read(part))
    for folder in ("deck", "scripts"):
        shutil.copytree(SKELETON / folder, out / folder, dirs_exist_ok=True)

    warnings = design.warnings + converter.warnings
    report = {"corners": len(corners), "text_samples": 0, "baseline": {}}
    if references and calibrate:
        renders = render_layouts(out, {b.layout.index: b.file for b in built}, width, height)
        if renders:
            samples = text_samples({b.layout.index: b.layout.slots for b in built}, renders, references)
            report["text_samples"] = len(samples)
            report["baseline"] = fit_baseline(samples)
            if report["baseline"]:
                tokens["baseline"] = report["baseline"]
                (out / "theme.css").write_text(theme_css(design), encoding="utf-8")
                (out / "design-tokens.json").write_text(_json(tokens), encoding="utf-8")
        else:
            warnings.append("Playwright is not installed, so text positions were not calibrated")

    manifest = {
        "skill": name,
        "source": tokens["source"],
        "slide": {"width": tokens["slide"]["width"], "height": tokens["slide"]["height"]},
        "layouts": [
            {
                "index": b.layout.index,
                "name": b.name,
                "file": b.file,
                "preview": b.preview,
                "use": b.use,
                "background": b.layout.background,
                "slots": [_slot_json(s) for s in b.layout.slots],
            }
            for b in built
        ],
    }
    (out / "layouts.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "references" / "layouts.md").write_text(layouts_reference(built), encoding="utf-8")
    (out / "SKILL.md").write_text(skill_md(name, design, built, descriptions), encoding="utf-8")
    return built, warnings, design, report


def _slot_json(slot: Slot) -> dict:
    data = {"name": slot.name, "kind": slot.kind}
    if slot.style:
        data["style"] = slot.style
    if slot.text:
        data["text"] = slot.text
    data["box"] = [round(v, 1) for v in (slot.left, slot.top, slot.width, slot.height)]
    return data


def _md(text: str) -> str:
    """Text for a markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def layouts_reference(built: list[BuiltLayout]) -> str:
    out = ["# Layouts", "", "Every slot of every layout, with the template's text. Boxes are left, top, width, height in px.", ""]
    for b in built:
        out += [f"## {b.name}", "", f"File: `{b.file}`" + (f" · preview: `{b.preview}`" if b.preview else ""), "",
                f"Use it for: {b.use}", ""]
        if b.layout.slots:
            out += ["| Slot | Kind | Style | Template text | Box |", "|---|---|---|---|---|"]
            for s in b.layout.slots:
                text = s.text if len(s.text) <= 90 else s.text[:89].rsplit(" ", 1)[0] + "…"
                box = ", ".join(f"{v:.0f}" for v in (s.left, s.top, s.width, s.height))
                out.append(f"| `{s.name}` | {s.kind} | {f'`{s.style}`' if s.style else ''} | {_md(text)} | {box} |")
            out.append("")
    return "\n".join(out)


def skill_md(name: str, design: Design, built: list[BuiltLayout], descriptions: dict) -> str:
    t = design.tokens
    title = descriptions.get("title") or title_case(name)
    hex_of = {c["var"]: c["hex"] for c in t["colors"]}
    families = sorted({f["family"] for f in t["fonts"] if f["chars"]})
    weights = {fam: sorted({f["weight"] for f in t["fonts"] if f["family"] == fam and f["chars"]}) for fam in families}
    font_text = ", ".join(f"{fam} ({', '.join(map(str, weights[fam]))})" for fam in families) or "the template fonts"
    description = descriptions.get("description") or (
        f"Create HTML slide presentations with the design of the {title} template: {', '.join(families) or 'its'} "
        f"typography, its color palette and {len(built)} ready-made slide layouts. Use it whenever the user asks for "
        f"slides, a deck or a presentation in the {title} style."
    )

    layout_rows = "\n".join(
        f"| `{b.file}` | {f'`{b.preview}`' if b.preview else '—'} | {_md(b.use)} |" for b in built
    )
    roles = t["color_roles"]
    surfaces = []
    for i in range(1, 4):
        bg, fg = roles.get(f"surface-{i}"), roles.get(f"on-surface-{i}")
        if bg and fg:
            surfaces.append(f"  - Background `--color-surface-{i}` ({hex_of[bg]}) takes text `--color-on-surface-{i}` ({hex_of[fg]}).")
    accents = [f"`--color-{role}` ({hex_of[var]})" for role, var in roles.items() if role.startswith("accent")]
    gradients = [f"`{g['var']}`" for g in t["gradients"][:2]]
    spacing = t["spacing"]
    margins = ", ".join(f"{k.removeprefix('margin-')} {v}px" for k, v in spacing.items())
    style_rows = "\n".join(
        f"| `{s['class']}` | {s['family']} {s['weight']} | {s['size_px']:g}px"
        + (f" ({s['size_range_px'][0]:g}–{s['size_range_px'][1]:g})" if s["size_range_px"][0] != s["size_range_px"][1] else "")
        + f" | {_md(s['sample'][:40])} |"
        for s in t["text_styles"]
    )
    rules = "".join(f"- {rule}\n" for rule in descriptions.get("rules", []))
    kinds = {slot.kind for b in built for slot in b.layout.slots}
    chart_slot = (
        "\n- `data-slot=\"chart\"`: a chart the template shows as a picture. Replace the `<img>` with an inline\n"
        "  `<svg>` chart of the user's data in the same box, drawn with the palette colors and text styles."
        if "chart" in kinds else ""
    )

    return f"""---
name: {name}
description: {_md(description)}
---

# {title} presentations

Build HTML presentations with the design of the {title} template ({t['source']}). Every slide of a
presentation is a copy of one of the {len(built)} layouts below, filled with the user's content. Paths
are relative to this skill's folder.

## Make a presentation

1. Start the deck: `python3 scripts/new_deck.py <folder> --title "<title>" --lang <language code>`. It copies
   `theme.css`, `deck/` and `assets/` into `<folder>` and writes an empty `<folder>/presentation.html`.
2. Plan the slides: for each one, pick the layout whose structure fits the content (table below). Look at
   its preview image when unsure. A layout can be used several times.
3. Copy the whole `<section class="slide">…</section>` of each chosen layout from its file into
   `presentation.html`, in presentation order.
4. Fill the slots (see Slots). Keep everything else as it is: classes, positions and the elements marked
   `data-role="decoration"`.
5. Check the result: `python3 scripts/render_deck.py <folder>/presentation.html -o <folder>/renders` writes
   one PNG per slide (needs Playwright). Look at every image and fix text that overflows its box, overlaps
   other elements or breaks into too many lines.
6. For a PDF, add `--pdf <folder>/presentation.pdf`. To present, open `presentation.html` in a browser:
   arrow keys move between slides, F goes full screen.

## Layouts

| Layout | Preview | Use it for |
|---|---|---|
{layout_rows}

Every slot of every layout, with the template's text: `references/layouts.md`.

## Slots

- `data-slot="title"`, `data-slot="body-2"`…: text. Write the new text inside the existing `<p>` (and
  `<span>`) tags and keep their classes. Use about as many words as the template text, because the boxes
  have a fixed width. For a longer title, lower its `font-size` within its style's range (Text styles
  below). For more paragraphs, copy the `<p>`.
- `data-slot="photo-N"`: a photo. Set `src` to the user's image; it is cropped to fill the box. Without
  one, keep the template's photo.
- `data-slot="icon-N"`: an icon. Replace `src` with an SVG of the same style and color, or keep it.{chart_slot}
- `data-slot="logo"`, `data-slot="brand"`: the logo and the brand name, repeated on several slides.
  Replace them on every slide with the user's, or keep them.
- Repeated items (cards, columns, rows, steps): to remove one, delete all its elements; to add one, copy
  them and move the copy by the same distance that separates the existing ones. Stay inside the margins.
  With fewer items, spread the remaining ones over the same area instead of leaving a gap.

## Fixing what the renders show

- A word too wide for its box (display and title styles are huge): use a shorter word, split the text
  over two lines, or lower `font-size` within the style's range.
- A title anchored at the bottom of its slide with fewer lines than the template's: move its box down
  by `font-size × line-height` px per missing line, so it still ends where the template's did.
- Shapes that follow a text (a dot after a word, an underline): move them when the text changes width.
- Choose line breaks in short titles and subtitles with `<br>`. Keep numbers with their unit using
  `&nbsp;` (`2,9&nbsp;%`, `150&nbsp;K`) so a unit never sits alone on a line.

## Design rules

- Canvas: {t['slide']['width']} × {t['slide']['height']} px.{f' Margins: {margins}.' if margins else ''}
- Colors: use the variables of `theme.css`, never new colors.
{chr(10).join(surfaces)}
{f"  - Accents: {', '.join(accents)}." if accents else ''}{(chr(10) + f"  - Gradients: {', '.join(gradients)}.") if gradients else ''}
- Fonts: {font_text}. They are in `assets/fonts/` and `theme.css` loads them.
- Text styles: the classes of `theme.css`, largest first.

| Class | Font | Size (range in the template) | Template text |
|---|---|---|---|
{style_rows}

{rules}
## A slide no layout fits

Start from the closest layout and change it; it keeps the template's spacing and decoration. When a slide
has to be written from scratch:

- `<section class="slide" data-layout="custom" style="background:var(--color-background)">`.
- Every element is absolutely positioned in px: `class="el"` for images and shapes, `class="text"` for
  text boxes, e.g. `<div class="text" style="left:{spacing.get('margin-left', 100)}px;top:300px;width:900px"><p class="text-body">…</p></div>`.
- Copy shapes (cards, lines, pills) as `<svg>` from a layout instead of drawing new ones.
"""

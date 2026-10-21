# ppt-template-2-skill

Turn a PowerPoint template (or a Canva or Google Slides one, exported to `.pptx`) into a **skill** so Claude Code or Copilot can build **HTML** presentations with the same design: colors, typography, shapes, images and diagrams.

## How it works

```
template.pptx + template.pdf (the template exported to PDF: the visual reference)
      │
      ▼  skills/pptx-to-skill: the skill that builds skills (this repo)
      │    1. Scripts read the pptx XML: colors, fonts, text styles, shapes, images, positions
      │    2. Every slide becomes an HTML + SVG layout with slots for new content
      │    3. The layouts are measured against the PDF and corrected (corners, text positions)
      │    4. Claude names and describes the layouts; the build writes the skill's SKILL.md
      ▼
my-template/: the generated skill
      SKILL.md, theme.css, layouts/, previews/, assets/, deck/, scripts/
      │
      ▼
"Make me a presentation about X with my-template" → presentation.html (and a PDF)
```

## Status

| Phase | What it does | Status |
|---|---|---|
| 1 | Design system: colors, gradients, fonts, text styles and margins → `theme.css` | Done |
| 2 | Slides → HTML layouts: text, images, freeform and basic preset shapes, groups, gradients, lines | Done |
| 3 | The generated skill: SKILL.md, previews, deck runtime and scripts; calibration against the PDF | Done |
| 4 | All preset shapes, tables, charts and SmartArt, effects (shadows, glow), arrowheads | To do |

Tested with a 14-slide Canva pitch deck: the layouts differ from the PDF in 2.9% of their pixels on average (mostly text antialiasing).

## Usage

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
.venv/bin/playwright install chromium

# Build the skill (the PDF gives the previews and the calibration)
.venv/bin/python skills/pptx-to-skill/scripts/build_skill.py inputs/template/template.pptx \
  -o output/my-template --name my-template --pdf inputs/template/template.pdf

# Compare each layout with the PDF
.venv/bin/python skills/pptx-to-skill/scripts/pdf_to_png.py inputs/template/template.pdf -o build/template/reference
.venv/bin/python skills/pptx-to-skill/scripts/compare.py output/my-template --reference build/template/reference -o build/template/compare

# Name and describe the layouts (see skills/pptx-to-skill/SKILL.md), then build again with --describe describe.json
```

Then use the generated skill:

```bash
python3 output/my-template/scripts/new_deck.py my-deck --title "My deck"
# copy slides from output/my-template/layouts/ into my-deck/presentation.html and fill their slots
.venv/bin/python output/my-template/scripts/render_deck.py my-deck/presentation.html -o my-deck/renders --pdf my-deck/presentation.pdf
```

In Claude Code, the whole process is a request: "turn this template into a skill" with `skills/pptx-to-skill` installed, then "make a presentation about X with my-template".

## Layout

```
skills/pptx-to-skill/        the skill that builds skills
  SKILL.md                   instructions for Claude
  requirements.txt
  scripts/
    build_skill.py           pptx (+ pdf) -> complete skill folder
    compare.py               layouts vs the PDF: score and side-by-side images
    extract.py               only the design system: theme.css, tokens, fonts, preview page
    pdf_to_png.py            PDF -> one image per slide
    render.py                HTML -> PNG screenshot (headless Chromium)
    measure_offset.py        how far a region of a render is shifted from the reference
    crop.py                  zoom into a region of an image
    pptx2skill/              the library: pptx reading, colors, fonts, design system,
                             slide conversion, geometry, calibration, skill assembly
  skeleton/                  copied into every generated skill
    deck/                    deck.css, deck.js: slide runtime (keys, scaling, print)
    scripts/                 new_deck.py, render_deck.py
inputs/   build/   output/   templates and results (not committed)
```

## Templates and licenses

`inputs/`, `build/` and `output/` are in `.gitignore`: Canva and internet templates are usually licensed and cannot be redistributed. Only commit templates you own or that have an open license.

# ppt-template-2-skill

Turn a PowerPoint template (or a Canva or Google Slides one, exported to `.pptx`) into a **skill** so Claude Code or Copilot can build **HTML** presentations with the same design: colors, typography, shapes, images and diagrams.

## How it works

```
template.pptx (+ template.pdf as the visual reference)
      │
      ▼  skills/pptx-to-skill: the skill that builds skills (this repo)
      │    1. Scripts read the pptx XML: colors, fonts, positions, shapes, images
      │    2. They convert every slide to HTML + SVG
      │    3. They compare screenshots of the HTML with the PDF and fix the differences
      │    4. Claude writes the SKILL.md of the new skill
      ▼
output/my-template/: the generated skill
      SKILL.md, theme.css, layouts/, assets/, previews/
      │
      ▼
"Make me a presentation about X with my-template" → presentation.html
```

## Status

| Phase | What it does | Status |
|---|---|---|
| 1 | Design system: colors, gradients, fonts, text styles and margins → `theme.css` | Done |
| 2 | Slides → HTML: text, images, shapes | To do |
| 3 | Generate the skill and compare it with the PDF | To do |
| 4 | Preset shapes, charts, SmartArt, effects | To do |

## Usage

Requirements: Python 3.10+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
.venv/bin/playwright install chromium

# The template's design system
.venv/bin/python skills/pptx-to-skill/scripts/extract.py inputs/template/template.pptx -o output/template

# Reference images, one per slide
.venv/bin/python skills/pptx-to-skill/scripts/pdf_to_png.py inputs/template/template.pdf -o build/template/reference
```

Open `output/template/theme-preview.html` in a browser to see the palette, surfaces, text styles and fonts.

## Layout

```
skills/pptx-to-skill/        the skill that builds skills
  SKILL.md                   instructions for Claude
  requirements.txt
  scripts/
    extract.py               pptx → theme.css, design-tokens.json, inventory.json, fonts
    pdf_to_png.py            PDF → one image per slide
    render.py                HTML → PNG screenshot (headless Chromium)
    crop.py                  crops a region of an image to inspect details
    pptx2skill/              shared code: pptx reading, colors, fonts, design
inputs/   build/   output/   templates and results (not committed)
```

## Templates and licenses

`inputs/`, `build/` and `output/` are in `.gitignore`: Canva and internet templates are usually licensed and cannot be redistributed. Only commit templates you own or that have an open license.

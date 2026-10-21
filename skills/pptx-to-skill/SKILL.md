---
name: pptx-to-skill
description: Turns a PowerPoint template (.pptx, also exported from Canva or Google Slides) into a skill that builds HTML presentations with the same design. Use it when the user wants to turn a slide template into a skill, or to reproduce a template's look (colors, fonts, shapes, layouts) in HTML slides.
---

# pptx-to-skill

Builds, from a template, a skill folder that any AI can use to make HTML presentations with the
template's exact design: its layouts, palette, fonts, text styles, shapes and images.

## Inputs

- `template.pptx`.
- `template.pdf`: the same template exported to PDF from the app it was made in (Canva, PowerPoint,
  Google Slides). Strongly recommended: it is the ground truth the build calibrates against, and it
  gives every layout its preview image.

Ask for the PDF if the user only gives the .pptx. In Canva: Share → Download → PDF Standard.

## Setup

The scripts live in `scripts/` and need the packages of `requirements.txt`:

```bash
pip install -r requirements.txt
playwright install chromium
```

If the files come from the internet, run Python with `-I` and keep the template in a folder of its own,
with no scripts in it.

## 1. Build the skill

```bash
python3 scripts/build_skill.py template.pptx -o <skill> --name <skill-name> --pdf template.pdf
```

`<skill-name>`: lowercase letters, digits and hyphens, e.g. `acme-pitch`. The build:

- reads the design system from the slides (`theme.css`, `design-tokens.json`, `theme-preview.html`);
- turns every slide into a layout (`layouts/NN-name.html`): absolutely positioned HTML and SVG, text in
  `data-slot` boxes, pictures as photo/icon/logo slots or decoration;
- copies the images and fonts (`assets/`), the deck runtime (`deck/`) and the deck scripts (`scripts/`);
- calibrates against the PDF: corrects rounded corners the .pptx export got wrong, and measures where
  the template's app places text inside its line height, which `theme.css` then applies;
- writes `layouts.json`, `references/layouts.md` and a first `SKILL.md`.

Read the summary it prints, above all the warnings: fonts that are not embedded, shapes or charts that
are not converted yet.

## 2. Check the layouts against the template

```bash
python3 scripts/compare.py <skill> --reference <build>/reference -o <build>/compare
```

with the reference images made by `python3 scripts/pdf_to_png.py template.pdf -o <build>/reference`.
It prints, per layout, the share of pixels that differ from the PDF and writes `NN-compare.png`
(reference and HTML on top, differences and both overlaid below). Look at the worst layouts:

- Text edges and a word wrapping one line earlier or later are normal (a few % of pixels).
- An element out of place, a wrong color or a missing shape is not: find its element in the layout file.
  `scripts/measure_offset.py` measures how far a region is shifted; `scripts/crop.py` zooms into one.

## 3. Name and describe the layouts

Look at every preview (`<skill>/previews/`) and write a description file, then build again with it:

```json
{
  "title": "Acme Pitch",
  "description": "Create HTML slide presentations in the style of ...: <colors, shapes, fonts>, with N layouts (cover, agenda, ...). Use it whenever the user asks for slides, a deck or a presentation in this style.",
  "rules": ["Design rules a person sees in the slides: light/dark alternation, where the logo goes, how titles break, which items get the accent ..."],
  "layouts": {
    "1": {"name": "cover", "use": "Opening slide: ..."},
    "10": {"name": "chart-highlights", "use": "...", "images": {"image23.png": "chart"}}
  }
}
```

```bash
python3 scripts/build_skill.py template.pptx -o <skill> --name <skill-name> --pdf template.pdf --describe describe.json
```

- `name`: what the layout is structurally (`timeline`, `four-cards`, `team`), not its sample text.
- `use`: when to pick it, in one sentence, naming its parts.
- `images`: images whose role the .pptx cannot tell, by file name as they appear in the layout
  (`assets/images/<file>`): `chart` (a chart drawn as a picture), `photo`, `icon`, `logo` or `decoration`.
- `rules`: what the template does consistently that the tokens do not say.

## 4. Test the skill

Make a short presentation (5 to 7 slides, new content) with the generated skill, following only its
`SKILL.md`, and render it with its `scripts/render_deck.py`. Look at every slide. When a problem comes from
the skill (a missing rule, a wrong slot, a misleading description), fix the description file and build
again rather than patching the presentation.

## 5. Hand it over

The skill folder is self-contained. Copy it to `~/.claude/skills/<skill-name>/` (Claude Code) or to the
project's `.github/skills/` or `.claude/skills/` folder, and tell the user how to ask for a presentation
with it.

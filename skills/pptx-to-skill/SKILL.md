---
name: pptx-to-skill
description: Turns a PowerPoint template (.pptx, also exported from Canva or Google Slides) into a skill that builds HTML presentations with the same design. Use it when the user wants to turn a template into a skill or extract its design system (colors, fonts, text styles).
---

# pptx-to-skill

> Work in progress: step 1 (design system) works today.

## Inputs

- `template.pptx`
- `template.pdf`: the same template exported to PDF from the app it was made in (Canva, PowerPoint, Google Slides). It is the visual reference for what the HTML must look like.

## Setup

The scripts live in `scripts/` and need the packages in `requirements.txt`:

```bash
pip install -r requirements.txt
playwright install chromium
```

If the files come from the internet, run Python with `-I` and keep the template in a folder of its own, with no scripts in it.

## Step 1: design system

```bash
python3 scripts/extract.py template.pptx -o <skill>
python3 scripts/pdf_to_png.py template.pdf -o <build>/reference
python3 scripts/render.py <skill>/theme-preview.html -o <build>/theme-preview.png --width 1480 --full-page
```

`extract.py` writes into `<skill>`:

| File | Contents |
|---|---|
| `theme.css` | `@font-face` rules, palette, color roles, gradients, margins and one class per text style |
| `design-tokens.json` | The same in JSON, with how often and on which slides each value is used |
| `inventory.json` | What each slide contains: text, images, shapes, effects |
| `assets/fonts/` | The fonts embedded in the template |
| `theme-preview.html` | A page to review the design system at a glance |

How to review the result:

1. Read the summary `extract.py` prints, above all the `Warnings`: fonts that are not embedded in the pptx, or that lack characters.
2. Look at the `theme-preview.html` screenshot next to the images in `reference/`. Check that colors, fonts and text styles match the template.
3. Text style names (`text-title`, `text-h2`, `text-body`...) come from a heuristic: the style with the most text is `body`, and the biggest text on the most slides is `title`. If a name does not match how the style is really used, note it for the generated skill's `SKILL.md`.

Why the design system comes from the slides and not from the theme: Canva and many internet templates leave the Office theme at its defaults and format every shape by hand. `extract.py` reports whether the theme is used (`theme.used_by_slides`).

## Next steps (to do)

2. Convert every slide to HTML + SVG (`layouts/`).
3. Compare screenshots of the HTML with `reference/` and fix the differences.
4. Write the generated skill's `SKILL.md`: which layout to use for what, and the style rules.

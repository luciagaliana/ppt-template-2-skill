#!/usr/bin/env python3
"""Extract a template's design system from a .pptx: colors, fonts and text styles.

Writes theme.css, theme-preview.html, design-tokens.json, inventory.json and the
embedded fonts into the output folder.

Usage: python3 extract.py template.pptx -o output/template
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also works under python -I

from pptx2skill.design import Design, extract_design  # noqa: E402
from pptx2skill.writers import write_all  # noqa: E402


def summary(design: Design, out: Path, written: list[Path]) -> str:
    t, inventory = design.tokens, design.inventory
    count = t["slide"]["count"]
    theme = t["theme"]
    lines = [
        f"{t['source']}: {count} slides, {t['slide']['width']}x{t['slide']['height']} px",
        f"Theme '{theme['name']}': "
        + ("used by the slides" if theme["used_by_slides"] else "not used by any slide; tokens come from the slides"),
        f"Colors ({len(t['colors'])}): " + ", ".join(f"{c['hex']} {len(c['slides'])}/{count}" for c in t["colors"]),
        "Color roles: " + ", ".join(
            f"{role} {next(c['hex'] for c in t['colors'] if c['var'] == var)}" for role, var in t["color_roles"].items()
        ),
        "Surfaces: " + ", ".join(f"{s['text']} on {s['background']} ({s['chars']} chars)" for s in t["surfaces"]),
        f"Gradients ({len(t['gradients'])}): " + "; ".join(f"{g['css']} x{g['count']}" for g in t["gradients"][:4]),
        "Spacing: " + (", ".join(f"{k} {v}px" for k, v in t["spacing"].items()) or "no repeated margins"),
        "Fonts: " + ", ".join(
            f"{f['family']} {f['weight']}{' italic' if f['italic'] else ''}{'' if f['file'] else ' (not embedded)'}"
            for f in t["fonts"]
        ),
        f"Text styles ({len(t['text_styles'])}):",
    ]
    for s in t["text_styles"]:
        low, high = s["size_range_px"]
        sizes = f"{s['size_px']}px" + (f" ({low}-{high})" if low != high else "")
        lines.append(
            f"  .{s['class']:<22} {s['family']} {s['weight']} {sizes:<20}"
            f" lh {s['line_height']} ls {s['letter_spacing_em']}em x{s['runs']}  \"{s['sample'][:36]}\""
        )
    lines.append("Elements: " + ", ".join(f"{k} {v}" for k, v in inventory["elements"].items()))
    lines.append("Features: " + (", ".join(f"{k} {v}" for k, v in inventory["features"].items()) or "none"))
    if design.warnings:
        lines.append("Warnings:")
        lines += [f"  - {w}" for w in design.warnings]
    lines.append(f"Wrote {len(written)} files to {out}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pptx", type=Path, help="template .pptx")
    parser.add_argument("-o", "--out", type=Path, required=True, help="output folder (the skill being built)")
    args = parser.parse_args(argv)

    design = extract_design(args.pptx)
    written = write_all(design, args.out)
    print(summary(design, args.out, written))
    return 0


if __name__ == "__main__":
    sys.exit(main())

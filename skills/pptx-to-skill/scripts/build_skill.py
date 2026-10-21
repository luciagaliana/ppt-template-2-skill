#!/usr/bin/env python3
"""Build a skill from a PowerPoint template: layouts, design system, assets, runtime and SKILL.md.

Usage: python3 build_skill.py template.pptx -o <skill folder> --name <skill-name>
                              [--pdf template.pdf] [--describe layouts.json]

--pdf adds a preview image per layout. --describe takes a JSON file that names and describes
the layouts, and adds rules to SKILL.md:
  {"title": "...", "description": "...", "rules": ["..."],
   "layouts": {"1": {"name": "cover", "use": "Opening slide: ..."}}}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also works under python -I

from pptx2skill.skill import build_skill  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pptx", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True, help="folder of the generated skill")
    parser.add_argument("--name", required=True, help="skill name: lowercase letters, digits and hyphens")
    parser.add_argument("--pdf", type=Path, help="the template exported to PDF: previews, and the reference to calibrate against")
    parser.add_argument("--describe", type=Path, help="JSON file with layout names, uses and rules")
    parser.add_argument("--no-calibrate", action="store_true", help="do not correct the layouts against the PDF")
    args = parser.parse_args(argv)

    descriptions = json.loads(args.describe.read_text(encoding="utf-8")) if args.describe else None
    layouts, warnings, design, report = build_skill(
        args.pptx, args.out, args.name, args.pdf, descriptions, calibrate=not args.no_calibrate
    )

    files = [p for p in args.out.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1024 / 1024
    print(f"Skill '{args.name}' in {args.out}: {len(layouts)} layouts, {len(files)} files, {size:.1f} MB")
    for built in layouts:
        slots = ", ".join(s.name for s in built.layout.slots)
        print(f"  {built.file}: {slots}")
    if args.pdf and not args.no_calibrate:
        fits = "; ".join(f"{family}: {fit['lh']:+g}*1lh {fit['em']:+g}*1em" for family, fit in report["baseline"].items())
        print(f"Calibration: {report['corners']} corner radii corrected; text baseline from "
              f"{report['text_samples']} text boxes: {fits or 'not enough measurements'}")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

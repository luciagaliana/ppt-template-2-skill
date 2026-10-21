#!/usr/bin/env python3
"""Screenshot every slide of a presentation (and optionally save it as PDF) to check it.

Needs Playwright: pip install playwright && playwright install chromium

Usage: python3 render_deck.py presentation.html -o renders/ [--pdf presentation.pdf] [--slides 2,5]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", type=Path)
    parser.add_argument("-o", "--out", type=Path, help="folder for one PNG per slide")
    parser.add_argument("--pdf", type=Path, help="also save the presentation as a PDF")
    parser.add_argument("--slides", help="only these slides, e.g. 2,5 (1 is the first)")
    args = parser.parse_args(argv)
    if not args.out and not args.pdf:
        parser.error("give -o and/or --pdf")
    wanted = {int(n) for n in args.slides.split(",")} if args.slides else None

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.goto(args.html.resolve().as_uri() + "?static")
            page.evaluate("document.fonts.ready.then(() => true)")
            width, height = page.evaluate(
                "['--slide-width', '--slide-height'].map(n => parseFloat(getComputedStyle(document.documentElement).getPropertyValue(n)))"
            )
            page.set_viewport_size({"width": int(width), "height": int(height)})
            if args.out:
                args.out.mkdir(parents=True, exist_ok=True)
                for number, slide in enumerate(page.locator("section.slide").all(), start=1):
                    if wanted and number not in wanted:
                        continue
                    path = args.out / f"slide-{number:02d}.png"
                    slide.screenshot(path=str(path))
                    print(path)
            if args.pdf:
                page.pdf(path=str(args.pdf), width=f"{width}px", height=f"{height}px", print_background=True)
                print(args.pdf)
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

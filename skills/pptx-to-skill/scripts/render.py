#!/usr/bin/env python3
"""Screenshot an HTML file with headless Chromium, after its web fonts load.

Usage: python3 render.py page.html -o page.png [--width 1920] [--height 1080] [--full-page]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("html", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True, help="PNG to write")
    parser.add_argument("--width", type=int, default=1920, help="viewport width in px (default: 1920)")
    parser.add_argument("--height", type=int, default=1080, help="viewport height in px (default: 1080)")
    parser.add_argument("--full-page", action="store_true", help="capture the whole page, not only the viewport")
    args = parser.parse_args(argv)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": args.width, "height": args.height})
            page.goto(args.html.resolve().as_uri())
            page.evaluate("document.fonts.ready.then(() => true)")
            page.screenshot(path=str(args.out), full_page=args.full_page)
        finally:
            browser.close()
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

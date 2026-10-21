#!/usr/bin/env python3
"""Start a presentation with this skill's design.

Copies theme.css, the deck runtime and the assets into the folder and writes an empty
presentation.html that links them. Then copy slides from layouts/ into it.

Usage: python3 new_deck.py <folder> [--title "Deck title"] [--lang en]
"""

from __future__ import annotations

import argparse
import html
import shutil
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent

PAGE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="theme.css">
<link rel="stylesheet" href="deck/deck.css">
</head>
<body>
<main class="deck">

<!-- Slides go here: copy a whole <section class="slide"> from a file in layouts/, then edit its slots. -->

</main>
<script src="deck/deck.js"></script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--title", default="Presentation")
    parser.add_argument("--lang", default="en", help="language of the slides, for the html lang attribute")
    parser.add_argument("--force", action="store_true", help="overwrite an existing presentation.html")
    args = parser.parse_args(argv)

    page = args.folder / "presentation.html"
    if page.exists() and not args.force:
        parser.error(f"{page} already exists (use --force to overwrite it)")
    args.folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SKILL / "theme.css", args.folder / "theme.css")
    for folder in ("deck", "assets"):
        shutil.copytree(SKILL / folder, args.folder / folder, dirs_exist_ok=True)
    page.write_text(PAGE.format(lang=html.escape(args.lang), title=html.escape(args.title)), encoding="utf-8")
    print(page)
    return 0


if __name__ == "__main__":
    sys.exit(main())

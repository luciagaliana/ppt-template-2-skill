#!/usr/bin/env python3
"""Render every page of a PDF to PNG: the reference images of the original template.

Export the PDF from the app the template was made in (Canva, PowerPoint, Google Slides):
it shows exactly what the HTML has to look like.

Usage: python3 pdf_to_png.py template.pdf -o build/template/reference [--width 1920]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True, help="output folder")
    parser.add_argument("--width", type=int, default=1920, help="image width in px (default: 1920)")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(args.pdf))
    try:
        for number, page in enumerate(pdf, start=1):
            image = page.render(scale=args.width / page.get_width()).to_pil()
            path = args.out / f"slide-{number:02d}.png"
            image.save(path)
            print(f"{path} {image.width}x{image.height}")
    finally:
        pdf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

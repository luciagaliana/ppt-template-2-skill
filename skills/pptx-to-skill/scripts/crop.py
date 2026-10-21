#!/usr/bin/env python3
"""Crop a region of an image, optionally zoomed, to inspect details at full resolution.

Usage: python3 crop.py image.png -o crop.png --box X Y WIDTH HEIGHT [--zoom 2]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--box", type=int, nargs=4, required=True, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--zoom", type=float, default=1.0)
    args = parser.parse_args(argv)

    x, y, width, height = args.box
    with Image.open(args.image) as image:
        region = image.crop((x, y, x + width, y + height))
    if args.zoom != 1:
        region = region.resize((round(width * args.zoom), round(height * args.zoom)), Image.LANCZOS)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    region.save(args.out)
    print(f"{args.out} {region.width}x{region.height}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

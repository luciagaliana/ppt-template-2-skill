#!/usr/bin/env python3
"""Measure how far a region of a rendered slide is shifted from the reference image.

Searches the shift (dx, dy) that best aligns the region of `rendered` with `reference`.
A result of dx=0 dy=+6 means the rendered content sits 6 px lower than in the reference.

Usage: python3 measure_offset.py reference.png rendered.png --box X Y WIDTH HEIGHT [--box ...] [--range 30]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))  # also works under python -I

from pptx2skill.calibrate import best_shift, cost  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reference", type=Path)
    parser.add_argument("rendered", type=Path)
    parser.add_argument("--box", type=int, nargs=4, action="append", required=True,
                        metavar=("X", "Y", "WIDTH", "HEIGHT"), help="region to align; repeat for several")
    parser.add_argument("--range", type=int, default=30, help="largest shift to try, in px (default: 30)")
    args = parser.parse_args(argv)

    with Image.open(args.reference) as ref, Image.open(args.rendered) as ren:
        reference = ref.convert("L")
        rendered = ren.convert("L").resize(reference.size)
    for box in args.box:
        before = cost(reference, rendered, tuple(box), 0, 0)
        dx, dy, after = best_shift(reference, rendered, tuple(box), args.range)
        print(f"box {' '.join(map(str, box))}: dx={dx:+d} dy={dy:+d} (mean difference {before:.1f} -> {after:.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

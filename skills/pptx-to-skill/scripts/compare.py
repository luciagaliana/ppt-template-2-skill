#!/usr/bin/env python3
"""Compare every layout of a generated skill with the template's reference images.

For each layout, renders its HTML, compares it with slide-NN.png from --reference and writes
NN-compare.png: reference and HTML on top, differences and both overlaid below. Prints the
share of pixels that differ visibly (0% means identical).

Usage: python3 compare.py <skill folder> --reference <folder with slide-NN.png> -o <folder> [--layouts 3,6]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageOps
from playwright.sync_api import sync_playwright

THRESHOLD = 48  # a pixel differs when a color channel moves more than this (0-255)


def difference(reference: Image.Image, rendered: Image.Image) -> tuple[float, Image.Image]:
    """(share of differing pixels, mask image with them in red)."""
    diff = ImageChops.difference(reference, rendered)
    channels = diff.split()
    strongest = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
    mask = strongest.point(lambda v: 255 if v > THRESHOLD else 0)
    share = sum(mask.histogram()[255:]) / (mask.width * mask.height)
    red = Image.new("RGB", reference.size, (229, 72, 77))
    canvas = Image.new("RGB", reference.size, (255, 255, 255))
    canvas.paste(ImageOps.grayscale(reference).convert("RGB").point(lambda v: 200 + v // 5), (0, 0))
    canvas.paste(red, (0, 0), mask)
    return share, canvas


def composite(reference: Image.Image, rendered: Image.Image, diff: Image.Image) -> Image.Image:
    w, h = reference.width // 2, reference.height // 2
    sheet = Image.new("RGB", (w * 2, h * 2), (255, 255, 255))
    for i, image in enumerate((reference, rendered, diff, Image.blend(reference, rendered, 0.5))):
        sheet.paste(image.resize((w, h), Image.LANCZOS), ((i % 2) * w, (i // 2) * h))
    return sheet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("skill", type=Path)
    parser.add_argument("--reference", type=Path, required=True, help="folder with slide-01.png, slide-02.png, ...")
    parser.add_argument("-o", "--out", type=Path, required=True)
    parser.add_argument("--layouts", help="only these layout numbers, e.g. 3,6")
    args = parser.parse_args(argv)

    manifest = json.loads((args.skill / "layouts.json").read_text(encoding="utf-8"))
    wanted = {int(n) for n in args.layouts.split(",")} if args.layouts else None
    width, height = manifest["slide"]["width"], manifest["slide"]["height"]
    args.out.mkdir(parents=True, exist_ok=True)
    scores = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            for layout in manifest["layouts"]:
                index = layout["index"]
                if wanted and index not in wanted:
                    continue
                reference_path = args.reference / f"slide-{index:02d}.png"
                if not reference_path.exists():
                    print(f"{layout['name']}: no reference image {reference_path}")
                    continue
                page.goto((args.skill / layout["file"]).resolve().as_uri() + "?static")
                page.evaluate("document.fonts.ready.then(() => true)")
                rendered_path = args.out / f"{index:02d}-html.png"
                page.locator("section.slide").first.screenshot(path=str(rendered_path))
                with Image.open(reference_path) as ref, Image.open(rendered_path) as html:
                    reference = ref.convert("RGB").resize((width, height))
                    rendered = html.convert("RGB").resize((width, height))
                share, diff = difference(reference, rendered)
                composite(reference, rendered, diff).save(args.out / f"{index:02d}-compare.png")
                scores.append((share, layout["name"]))
                print(f"{layout['name']}: {share:.1%} of pixels differ")
        finally:
            browser.close()
    if scores:
        mean = sum(s for s, _ in scores) / len(scores)
        worst = max(scores)
        print(f"Mean: {mean:.1%} · worst: {worst[1]} ({worst[0]:.1%}) · images in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

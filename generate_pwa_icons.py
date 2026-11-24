#!/usr/bin/env python3
"""Regenerate install icons and favicons from the canonical SVG using ImageMagick."""

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

STATIC_DIR = Path(__file__).resolve().parent / "static"
ICONS_DIR = STATIC_DIR / "icons"
SOURCE = ICONS_DIR / "neurosync.svg"


def main():
    magick = shutil.which("magick")
    if not magick:
        raise SystemExit("Install ImageMagick to regenerate Neurosync AI icons.")

    svg = ElementTree.parse(SOURCE).getroot()
    plate = svg.find("{http://www.w3.org/2000/svg}rect[@id='plate']").attrib["fill"]

    def render(*args):
        subprocess.run([magick, *map(str, args)], check=True)

    with TemporaryDirectory() as tmp:
        master = Path(tmp) / "phantomlimb.png"
        render("-background", "none", "-density", "1152", SOURCE, master)
        for size in (192, 512):
            render(master, "-resize", f"{size}x{size}", ICONS_DIR / f"icon-{size}.png")
            inset = round(size * 0.68)
            render(master, "-resize", f"{inset}x{inset}", "-background", plate,
                   "-gravity", "center", "-extent", f"{size}x{size}", "-alpha", "remove",
                   ICONS_DIR / f"icon-maskable-{size}.png")
        render(master, "-resize", "180x180", "-background", plate, "-alpha", "remove",
               ICONS_DIR / "apple-touch-icon.png")
        for size in (16, 32):
            render(master, "-resize", f"{size}x{size}", ICONS_DIR / f"favicon-{size}x{size}.png")
        render(ICONS_DIR / "favicon-32x32.png", ICONS_DIR / "favicon-16x16.png",
               STATIC_DIR / "favicon.ico")


if __name__ == "__main__":
    main()

"""Render the geometric logo to PNG/ICO; Pillow is a build-only dependency."""
from __future__ import annotations

import argparse
from io import BytesIO
from pathlib import Path
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parents[1] / "assets"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render_logo(size: int) -> Image.Image:
    source = ET.parse(ASSETS / "launcher-logo.svg").getroot()
    if source.attrib["viewBox"] != "0 0 64 64":
        raise ValueError("Unexpected logo coordinate system")
    scale = size * 8 / 64
    image = Image.new("RGBA", (size * 8, size * 8))
    draw = ImageDraw.Draw(image)
    for shape in source:
        kind = shape.tag.rsplit("}", 1)[-1]
        if kind in ("title", "desc"):
            continue
        if kind == "rect":
            x, y, width, height = (float(shape.attrib[key]) for key in ("x", "y", "width", "height"))
            draw.rounded_rectangle(
                (x * scale, y * scale, (x + width) * scale, (y + height) * scale),
                radius=float(shape.attrib.get("rx", 0)) * scale, fill=shape.attrib["fill"],
            )
        elif kind == "polygon":
            points = [tuple(float(value) * scale for value in point.split(","))
                      for point in shape.attrib["points"].split()]
            draw.polygon(points, fill=shape.attrib["fill"])
        else:
            raise ValueError("Unsupported logo shape")
    return image.resize((size, size), Image.Resampling.LANCZOS)


def build_assets() -> dict[str, bytes]:
    frames = {size: render_logo(size) for size in ICON_SIZES}
    outputs = {}
    for size in (16, 32, 48, 64, 256):
        stream = BytesIO()
        frames[size].save(stream, format="PNG")
        outputs[f"launcher-{size}.png"] = stream.getvalue()
    stream = BytesIO()
    frames[256].save(stream, format="ICO", bitmap_format="bmp", sizes=[(size, size) for size in ICON_SIZES],
                     append_images=[frames[size] for size in ICON_SIZES if size != 256])
    outputs["launcher.ico"] = stream.getvalue()
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify checked-in assets without writing")
    args = parser.parse_args()
    outputs = build_assets()
    if args.check:
        return 0 if all((ASSETS / name).read_bytes() == data for name, data in outputs.items()) else 1
    for name, data in outputs.items():
        (ASSETS / name).write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

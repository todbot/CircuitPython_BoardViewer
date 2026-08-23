#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Generate docs/social-card.png: the Open Graph / Twitter Card banner."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
PURPLE = (101, 47, 143)
PURPLE_DARK = (74, 34, 105)
WHITE = (255, 255, 255)
LAVENDER = (223, 202, 240)

FONT_PATH = "/System/Library/Fonts/SFNS.ttf"
MONO_FONT_PATH = "/System/Library/Fonts/SFNSMono.ttf"


def font(size, variation=None):
    f = ImageFont.truetype(FONT_PATH, size)
    if variation:
        f.set_variation_by_name(variation)
    return f


def mono_font(size):
    return ImageFont.truetype(MONO_FONT_PATH, size)


def centered_text(draw, y, text, fnt, fill):
    bbox = draw.textbbox((0, 0), text, font=fnt)
    w = bbox[2] - bbox[0]
    draw.text(((WIDTH - w) / 2, y), text, font=fnt, fill=fill)
    return bbox[3] - bbox[1]


def main():
    img = Image.new("RGB", (WIDTH, HEIGHT), PURPLE)
    draw = ImageDraw.Draw(img)

    # A subtle darker footer band, echoing the top nav bar on circuitpython.org.
    draw.rectangle([0, HEIGHT - 90, WIDTH, HEIGHT], fill=PURPLE_DARK)

    title_font = font(72, "Bold")
    subtitle_font = font(34, "Regular")
    chip_font = mono_font(26)

    centered_text(draw, 190, "CircuitPython Board Viewer", title_font, WHITE)
    centered_text(
        draw, 300,
        "Pin names and board.SPI()/I2C()/UART()/DISPLAY, board by board",
        subtitle_font, LAVENDER,
    )

    # Small pill chips, echoing the site's own pin-alias/chip styling.
    chips = ["board.I2C()", "board.SPI()", "board.UART()", "board.DISPLAY"]
    pad_x, gap = 22, 16
    widths = []
    for c in chips:
        bbox = draw.textbbox((0, 0), c, font=chip_font)
        widths.append(bbox[2] - bbox[0] + pad_x * 2)
    total_w = sum(widths) + gap * (len(chips) - 1)
    x = (WIDTH - total_w) / 2
    y0, y1 = 400, 452
    for c, w in zip(chips, widths):
        draw.rounded_rectangle([x, y0, x + w, y1], radius=(y1 - y0) / 2, fill=PURPLE_DARK)
        bbox = draw.textbbox((0, 0), c, font=chip_font)
        text_h = bbox[3] - bbox[1]
        draw.text((x + pad_x, y0 + ((y1 - y0) - text_h) / 2 - bbox[1]), c, font=chip_font, fill=WHITE)
        x += w + gap

    footer_font = font(28, "Regular")
    draw.text((40, HEIGHT - 62), "todbot.github.io/CircuitPython_BoardViewer", font=footer_font, fill=WHITE)

    out_path = Path(__file__).resolve().parent.parent / "docs" / "social-card.png"
    img.save(out_path)
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

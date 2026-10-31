#!/usr/bin/env python3
"""Generates logo.png (800x800) and banner.png (2560x1440) for The Money Blueprint. Run: python branding/make_branding.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent
NAVY, NAVY2, GRID, GOLD, WHITE = "#0B1F3A", "#13335C", "#1E4A80", "#F5B82E", "#FFFFFF"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def background(w, h, step):
    img = Image.new("RGB", (w, h), NAVY)
    d = ImageDraw.Draw(img)
    for y in range(h):  # vertical gradient
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip((11, 31, 58), (19, 51, 92))))
    for x in range(0, w, step):  # blueprint grid
        d.line([(x, 0), (x, h)], fill=GRID, width=2 if x % (step * 5) else 4)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=GRID, width=2 if y % (step * 5) else 4)
    return img


def chart(d, x0, y0, w, h, bars=5):
    """Ascending gold bars with a rising line and arrowhead; box is (x0,y0,w,h)."""
    gap, bw = w * 0.06, (w - w * 0.06 * (bars - 1)) / bars
    tops = []
    for i in range(bars):
        bh = h * (0.28 + 0.72 * i / (bars - 1))
        x = x0 + i * (bw + gap)
        d.rounded_rectangle([x, y0 + h - bh, x + bw, y0 + h], radius=bw * 0.12, fill=GOLD)
        tops.append((x + bw / 2, y0 + h - bh - h * 0.12))
    d.line(tops, fill=WHITE, width=int(h * 0.04), joint="curve")
    (x1, y1), (x2, y2) = tops[-2], tops[-1]
    r = h * 0.07
    d.polygon([(x2 + r * 1.2, y2 - r * 1.2), (x2 - r * 0.5, y2 - r * 1.1), (x2 + r * 1.1, y2 + r * 0.5)], fill=WHITE)


def logo():
    S = 800
    img = background(S, S, 40)
    d = ImageDraw.Draw(img)
    d.ellipse([30, 30, S - 30, S - 30], outline=GOLD, width=10)
    chart(d, 200, 190, 400, 300)
    f = ImageFont.truetype(BOLD, 110)
    t = "MB"
    d.text(((S - d.textlength(t, font=f)) / 2, 540), t, font=f, fill=WHITE)
    img.save(OUT / "logo.png")


def banner():
    W, H = 2560, 1440
    img = background(W, H, 80)
    d = ImageDraw.Draw(img)
    # safe area (visible on all devices): 1546x423 centred
    cx, cy = W // 2, H // 2
    chart(d, cx - 740, cy - 150, 300, 300)
    f1, f2 = ImageFont.truetype(BOLD, 112), ImageFont.truetype(REG, 46)
    x = cx - 380
    d.text((x, cy - 120), "THE MONEY", font=f1, fill=WHITE)
    d.text((x, cy - 5), "BLUEPRINT", font=f1, fill=GOLD)
    d.text((x, cy + 130), "Simple money lessons. New videos every week.", font=f2, fill="#C9D6EA")
    img.save(OUT / "banner.png")


logo()
banner()

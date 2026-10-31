#!/usr/bin/env python3
"""Generates logo.png (800x800) and banner.png (2560x1440) per channel.
Run: python engine/branding/make_branding.py [channel ...]   (default: facts news ai-updates). Edit BRANDS to rename."""
import math, sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent.parent / "channels"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BRANDS = {
    "facts": dict(name="CURIO VAULT", mono="CV", tag="Facts worth knowing, with the sources to prove them", bg=((24, 12, 48), (58, 28, 96)), grid="#4a2f7d", accent="#FFC247", icon="bulb"),
    "news": dict(name="THE EVENING BRIEF", mono="EB", tag="The day's verified world news in five minutes", bg=((8, 20, 30), (16, 52, 72)), grid="#1e5a78", accent="#FF5A4F", icon="globe"),
    "ai-updates": dict(name="NEURAL NOTES", mono="NN", tag="Today's AI news, explained plainly", bg=((6, 24, 28), (10, 60, 64)), grid="#17706f", accent="#3DF5C8", icon="net"),
}
WHITE = "#FFFFFF"


def background(w, h, b, step):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a + (c - a) * t) for a, c in zip(*b["bg"])))
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=b["grid"], width=2)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=b["grid"], width=2)
    return img


def icon(d, kind, cx, cy, r, c):
    lw = max(4, int(r * 0.07))
    if kind == "bulb":
        d.ellipse([cx - r * .6, cy - r, cx + r * .6, cy + r * .2], fill=c)
        d.rounded_rectangle([cx - r * .3, cy + r * .2, cx + r * .3, cy + r * .75], radius=r * .08, fill=c)
        d.rounded_rectangle([cx - r * .22, cy + r * .82, cx + r * .22, cy + r * 1.0], radius=r * .08, fill=WHITE)
        f = ImageFont.truetype(BOLD, int(r * .9))
        d.text((cx - d.textlength("?", font=f) / 2, cy - r * .95), "?", font=f, fill="#2a1450")
    elif kind == "globe":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=c, width=lw * 2)
        d.ellipse([cx - r * .45, cy - r, cx + r * .45, cy + r], outline=c, width=lw)
        d.line([cx - r, cy, cx + r, cy], fill=c, width=lw)
        d.arc([cx - r, cy - r * .55, cx + r, cy + r * .55], 0, 360, fill=c, width=lw)
        for k in (1, 2):
            d.arc([cx - r * (1 + .18 * k), cy - r * (1 + .18 * k), cx + r * (1 + .18 * k), cy + r * (1 + .18 * k)], -50, 50, fill=WHITE, width=lw)
    else:
        pts = [(cx - r * .8, cy - r * .5), (cx - r * .8, cy + r * .5), (cx, cy - r * .85), (cx, cy), (cx, cy + r * .85), (cx + r * .8, cy - r * .45), (cx + r * .8, cy + r * .45)]
        links = [(0, 2), (0, 3), (1, 3), (1, 4), (2, 5), (3, 5), (3, 6), (4, 6), (2, 3), (3, 4)]
        for a, b in links:
            d.line([pts[a], pts[b]], fill=WHITE, width=lw)
        for x, y in pts:
            d.ellipse([x - r * .14, y - r * .14, x + r * .14, y + r * .14], fill=c)


def make(ch):
    b = BRANDS[ch]
    out = ROOT / ch / "branding"
    out.mkdir(parents=True, exist_ok=True)
    S = 800
    img = background(S, S, b, 40)
    d = ImageDraw.Draw(img)
    d.ellipse([30, 30, S - 30, S - 30], outline=b["accent"], width=10)
    icon(d, b["icon"], S // 2, 330, 190, b["accent"])
    f = ImageFont.truetype(BOLD, 110)
    d.text(((S - d.textlength(b["mono"], font=f)) / 2, 560), b["mono"], font=f, fill=WHITE)
    img.save(out / "logo.png")
    W, H = 2560, 1440
    img = background(W, H, b, 80)
    d = ImageDraw.Draw(img)
    cx, cy = W // 2, H // 2  # safe area 1546x423 centred
    icon(d, b["icon"], cx - 560, cy, 150, b["accent"])
    f1, f2 = ImageFont.truetype(BOLD, 104), ImageFont.truetype(REG, 42)
    d.text((cx - 300, cy - 95), b["name"], font=f1, fill=WHITE)
    d.rectangle([cx - 300, cy + 40, cx - 300 + 240, cy + 50], fill=b["accent"])
    d.text((cx - 300, cy + 70), b["tag"], font=f2, fill="#d7e3f4")
    img.save(out / "banner.png")


if __name__ == "__main__":
    for ch in (sys.argv[1:] or list(BRANDS)):
        make(ch)
        print("wrote", ch)

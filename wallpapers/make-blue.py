#!/usr/bin/python3 -I
"""Generate MAGI's default wallpaper (catppuccin-blue.png): Catppuccin Macchiato slate with
soft blue / mauve / teal glows and faint diagonal hairlines. Original artwork, made at
install time, so the repo carries no image files.

usage: make-blue.py OUT.png [WIDTH HEIGHT]
"""
import math
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter

out = sys.argv[1]
W, H = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (1920, 1080)

# Catppuccin Macchiato: crust/mantle/base and the accents the blue theme uses
crust, base = (24, 25, 38), (36, 39, 58)
blue, mauve, teal = (138, 173, 244), (198, 160, 246), (139, 213, 202)

# vertical-ish base gradient: crust at the top, base towards the bottom-left
img = Image.new("RGB", (W, H))
px = img.load()
for y in range(H):
    for x in range(W):
        t = min(1.0, max(0.0, (y / H) * 0.85 + (1 - x / W) * 0.25))
        px[x, y] = tuple(int(crust[i] + (base[i] - crust[i]) * t) for i in range(3))


def glow(color, cx, cy, radius, strength):
    """A soft radial light, added on top of the image."""
    layer = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(layer)
    r = int(radius)
    c = tuple(int(v * strength) for v in color)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=c)
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.6))


for color, cx, cy, rad, s in (
    (mauve, W * 0.80, H * 0.18, H * 0.55, 0.22),  # upper right
    (blue, W * 0.18, H * 0.82, H * 0.60, 0.16),   # lower left
    (teal, W * 0.55, H * 0.70, H * 0.35, 0.06),   # faint centre
):
    img = ImageChops.add(img, glow(color, int(cx), int(cy), rad, s))

# darken the bottom-right corner a little
shade = Image.new("L", (W, H), 0)
ImageDraw.Draw(shade).ellipse((W * 0.75, H * 0.6, W * 1.35, H * 1.4), fill=90)
shade = shade.filter(ImageFilter.GaussianBlur(H * 0.2))
img = Image.composite(Image.new("RGB", (W, H), crust), img, shade)

# faint 45-degree hairlines, strongest at the corners
lines = Image.new("RGBA", (W, H), (0, 0, 0, 0))
d = ImageDraw.Draw(lines)
for k in range(-H, W, 14):
    d.line((k, 0, k + H, H), fill=blue + (22,), width=1)
mask = Image.new("L", (W, H), 0)
mp = mask.load()
for y in range(0, H, 4):
    for x in range(0, W, 4):
        v = int(255 * min(1.0, (math.hypot(x / W - 0.5, y / H - 0.5) / 0.7) ** 1.5))
        for dy in range(4):
            for dx in range(4):
                if x + dx < W and y + dy < H:
                    mp[x + dx, y + dy] = v
lines.putalpha(ImageChops.multiply(lines.getchannel("A"), mask))
img = Image.alpha_composite(img.convert("RGBA"), lines).convert("RGB")

img.save(out)
print(out)

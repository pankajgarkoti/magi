#!/usr/bin/env python3
"""Generate the installer branding for MAGI (no Fedora logos): the images and stylesheet that
images/product.img lays over Anaconda's own. Usage: make-branding.py OUTDIR FONT.ttf"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

out, font_path = sys.argv[1], sys.argv[2]
BLACK, ORANGE, RED, DIM = (11, 11, 15, 255), (255, 106, 0, 255), (224, 48, 30, 255), (120, 120, 130, 255)
pix = os.path.join(out, "usr/share/anaconda/pixmaps")
os.makedirs(pix, exist_ok=True)


def font(size):
    return ImageFont.truetype(font_path, size)


def hazard(d, x0, y0, x1, y1, color, w=12):
    for x in range(x0 - (y1 - y0), x1, w * 2):
        d.polygon([(x, y1), (x + w, y1), (x + w + (y1 - y0), y0), (x + (y1 - y0), y0)], fill=color)


# sidebar: black glass, hazard band at the bottom, MAGI wordmark in vertical text
im = Image.new("RGBA", (406, 767), BLACK)
d = ImageDraw.Draw(im)
d.rectangle([0, 0, 405, 3], fill=ORANGE)
hazard(d, 0, 727, 406, 747, (255, 106, 0, 150))
d.text((24, 662), "a Fedora remix", font=font(13), fill=DIM)
im.save(os.path.join(pix, "sidebar-bg.png"))

im = Image.new("RGBA", (150, 69), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.text((4, 2), "MAGI", font=font(44), fill=ORANGE)
d.rectangle([4, 60, 146, 63], fill=RED)
im.save(os.path.join(pix, "sidebar-logo.png"))

im = Image.new("RGBA", (1040, 132), BLACK)
d = ImageDraw.Draw(im)
hazard(d, 0, 0, 1040, 6, (255, 106, 0, 110))
d.rectangle([0, 129, 1039, 131], fill=ORANGE)
im.save(os.path.join(pix, "topbar-bg.png"))

im = Image.new("RGBA", (119, 36), (0, 0, 0, 0))
ImageDraw.Draw(im).text((2, 2), "MAGI", font=font(28), fill=ORANGE)
im.save(os.path.join(pix, "anaconda_header.png"))

with open(os.path.join(pix, "fedora.css"), "w") as f:
    f.write("""/* MAGI installer styling (replaces the stock stylesheet; no Fedora logos) */
@define-color fedora #0b0b0f;
.logo-sidebar { background-image: url('/usr/share/anaconda/pixmaps/sidebar-bg.png'); background-color: #0b0b0f; background-repeat: no-repeat; }
.logo { background-image: url('/usr/share/anaconda/pixmaps/sidebar-logo.png'); background-position: 50% 20px; background-repeat: no-repeat; background-color: transparent; }
.product-logo { background-image: none; background-color: transparent; }
AnacondaSpokeWindow #nav-box { background-color: #0b0b0f; background-image: url('/usr/share/anaconda/pixmaps/topbar-bg.png'); background-repeat: repeat; color: #ff6a00; }
""")

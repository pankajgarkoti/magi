"""Glyph outlines from OFL fonts via fontTools -> SVG path data (y down, baseline at 0)."""
import pathlib
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen
F = pathlib.Path(__file__).resolve().parent / "fonts"
_cache = {}
def font(name, **axes):
    key = (name, tuple(sorted(axes.items())))
    if key not in _cache:
        f = TTFont(F / name)
        if axes and "fvar" in f:
            f = instancer.instantiateVariableFont(f, axes, overlap=instancer.OverlapMode.REMOVE)
        _cache[key] = f
    return _cache[key]

def glyph(f, ch, size, x=0, y=0, sx=1.0):
    """path d for one char scaled to `size` (font-size px), placed with baseline at y. returns (d, advance_px)."""
    cmap = f.getBestCmap(); gs = f.getGlyphSet(); upm = f["head"].unitsPerEm
    gn = cmap[ord(ch)]; s = size / upm
    pen = SVGPathPen(gs)
    tp = TransformPen(pen, (s * sx, 0, 0, -s, x, y))
    gs[gn].draw(tp)
    return pen.getCommands(), gs[gn].width * s * sx

def bounds(f, ch, size):
    cmap = f.getBestCmap(); gs = f.getGlyphSet(); s = size / f["head"].unitsPerEm
    bp = BoundsPen(gs); gs[cmap[ord(ch)]].draw(bp)
    xmin, ymin, xmax, ymax = bp.bounds
    return xmin * s, -ymax * s, xmax * s, -ymin * s, gs[cmap[ord(ch)]].width * s

def metrics(f, size):
    os2 = f["OS/2"]; s = size / f["head"].unitsPerEm
    return dict(cap=os2.sCapHeight * s, x=os2.sxHeight * s)

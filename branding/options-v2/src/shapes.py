import math, pathops
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.recordingPen import RecordingPen
import glyphs as G

def glyph(f, ch, size, x=0, y=0, sx=1.0):
    gs=f.getGlyphSet(); gn=f.getBestCmap()[ord(ch)]; s=size/f["head"].unitsPerEm
    p=pathops.Path(); gs[gn].draw(TransformPen(p.getPen(glyphSet=gs), (s*sx,0,0,-s,x,y)))
    p.fillType=pathops.FillType.WINDING; p.simplify(fix_winding=True, keep_starting_points=False)
    return p, gs[gn].width*s*sx
def poly(pts):
    p=pathops.Path(); pen=p.getPen(); pen.moveTo(pts[0])
    for q in pts[1:]: pen.lineTo(q)
    pen.closePath(); return p
def rect(x,y,w,h): return poly([(x,y),(x+w,y),(x+w,y+h),(x,y+h)])
def circle(cx,cy,r):
    k=0.5522847498*r; p=pathops.Path(); pen=p.getPen()
    pen.moveTo((cx+r,cy)); pen.curveTo((cx+r,cy+k),(cx+k,cy+r),(cx,cy+r)); pen.curveTo((cx-k,cy+r),(cx-r,cy+k),(cx-r,cy))
    pen.curveTo((cx-r,cy-k),(cx-k,cy-r),(cx,cy-r)); pen.curveTo((cx+k,cy-r),(cx+r,cy-k),(cx+r,cy)); pen.closePath(); return p
def union(ps):
    out=pathops.Path()
    pathops.union(ps, out.getPen()); return out
def op(a,b,kind):
    out=pathops.Path(); pathops.op(a,b,kind,).draw(out.getPen()) if False else None
    return pathops.op(a,b,kind)
def diff(a,b): return pathops.op(a,b,pathops.PathOp.DIFFERENCE)
def inter(a,b): return pathops.op(a,b,pathops.PathOp.INTERSECTION)
def xor(a,b): return pathops.op(a,b,pathops.PathOp.XOR)
def move(p,dx,dy,s=1.0):
    q=pathops.Path(); p.draw(TransformPen(q.getPen(),(s,0,0,s,dx,dy))); return q
def d(p):
    pen=SVGPathPen(None, ntos=lambda v: ("%.2f"%v).rstrip("0").rstrip(".")); p.draw(pen); return pen.getCommands()
def bounds(p): return p.bounds  # xmin,ymin,xmax,ymax
def segs(p):
    r=RecordingPen(); p.draw(r); return r.value

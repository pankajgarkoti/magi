"""reverse: lowercase m beside AGI set in reverse video, a selection/warning-label block. At mark size the block
collapses to a cursor: m followed by a block cursor. Lettering: JetBrains Mono Bold (OFL), spaced and blocked by hand."""
import pathlib, shapes as S, glyphs as G
from common import *
OUT=OUT_ROOT/"reverse"; TMP=pathlib.Path("tmp/reverse"); TMP.mkdir(parents=True, exist_ok=True)
f=G.font('JetBrainsMono[wght].ttf',wght=700); SZ=100; CAP=G.metrics(f,SZ)['cap']
PADY=13; GAP=9      # block padding above cap / below baseline, gap between m and block
m,adv=S.glyph(f,'m',SZ,0,0); mb=m.bounds
x=mb[2]+GAP
L=[]; lx=0
for ch in 'AGI':
    p,a=S.glyph(f,ch,SZ,lx,0); L.append(p); lx+=a
U=S.union(L); ub=U.bounds
PADX=PADY*0.95     # optical: side padding a touch under the vertical so the block doesn't look wide
U=S.move(U, x+PADX-ub[0], 0); ub=U.bounds
block=S.rect(x,-CAP-PADY, ub[2]+PADX-x, CAP+2*PADY)
knock=S.diff(block,U)
# mark: m + cursor block, cursor = block height, width a little under a terminal cell
cur=S.rect(mb[2]+GAP+2,-CAP-PADY,34,CAP+2*PADY)
def word(theme, colour):
    fg = INK_DARK_FG if theme=="dark" else INK_LIGHT_FG
    if not colour: return [(m,fg),(knock,fg)]
    if theme=="dark": return [(m,fg),(knock,ORANGE)]
    return [(m,fg),(block,ORANGE),(U,fg)]
def mark(theme, colour):
    fg = INK_DARK_FG if theme=="dark" else INK_LIGHT_FG
    return [(m,fg),(cur,ORANGE if colour else fg)]
for th in ("dark","light"):
    for col,cn in ((False,"mono"),(True,"colour")):
        write(OUT/f"wordmark-{cn}-on-{th}.svg", paths_svg(word(th,col),8))
        write(OUT/f"mark-{cn}-on-{th}.svg", paths_svg(mark(th,col),8))
        write(OUT/f"lockup-{cn}-on-{th}.svg", lockup_svg(word(th,col), mark("dark",col), tile_bg=("#221B2E" if th=="dark" else INK_LIGHT_FG), mark_fill=0.62))
fav={}
P16={'m':"M2 6h7v7h-1v-6h-2v6h-1v-6h-2v6h-1z", 'c':(10,4,4,10)}
P32={'m':"M4 11H18.5Q20 11 20 12.5V25H17V14H14V25H11V14H8V25H4Z"}
for th,bg,fg in (("dark",INK_DARK_BG,INK_DARK_FG),("light",INK_LIGHT_BG,INK_LIGHT_FG)):
    svg=tile_svg(mark(th,True),512,bg,inset=0.17); write(OUT/f"favicon-{th}.svg",svg); fav[th]={}
    for sz in (16,32,180,512):
        pth=OUT/f"favicon-{th}-{sz}.png"
        if sz==16:
            px=f'<rect width="16" height="16" rx="3.5" fill="{bg}"/><path fill="{fg}" d="M2 6h8v7h-2v-6h-1v6h-2v-6h-1v6h-2z"/><rect x="11" y="3" width="3" height="11" fill="{ORANGE}"/>'
            s_=svg_doc(16,16,px); write(OUT/("favicon-%s-16.svg"%th),s_); raster_exact(s_,pth,16)
        elif sz==32:
            px=f'<rect width="32" height="32" rx="7" fill="{bg}"/><path fill="{fg}" d="M4 12H17Q19 12 19 14V25H16V15H13V25H10V15H7V25H4Z"/><rect x="21" y="7" width="6" height="20" fill="{ORANGE}"/>'
            s_=svg_doc(32,32,px); write(OUT/("favicon-%s-32.svg"%th),s_); raster_exact(s_,pth,32)
        else: raster_exact(svg,pth,sz)
        fav[th][sz]=pth
preview("reverse","mAGI · reverse","lowercase m, then AGI in reverse video: a selection, a warning label. Shrunk to a mark, the block becomes a cursor. Lettering: JetBrains Mono Bold (OFL).",
  OUT/"lockup-colour-on-dark.svg", OUT/"lockup-mono-on-light.svg", OUT/"mark-colour-on-dark.svg", OUT/"mark-mono-on-light.svg", fav["dark"], fav["light"], OUT_ROOT/"reverse-preview.png", TMP)
print("done")

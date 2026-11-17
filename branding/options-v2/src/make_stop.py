"""stop: a serif lowercase m and a full stop. Terse, academic: the name abbreviated to one letter, then nothing more to say.
Lettering: IBM Plex Serif Medium (OFL), respaced; the full stop is redrawn as a square at stem weight."""
import pathlib, shapes as S, glyphs as G
from common import *
OUT=OUT_ROOT/"stop"; TMP=pathlib.Path("tmp/stop"); TMP.mkdir(parents=True, exist_ok=True)
f=G.font('IBMPlexSerif-Medium.ttf'); SZ=100; CAP=G.metrics(f,SZ)['cap']
def stem_w():
    p,_=S.glyph(f,'l',SZ); prev=None; xs=[]
    for op,args in S.segs(p):
        if op=='lineTo' and prev and abs(args[0][0]-prev[0])<0.01 and abs(args[0][1]-prev[1])>30: xs.append(prev[0])
        if args: prev=args[-1]
    xs=sorted(xs); return xs[-1]-xs[0] if len(xs)>=2 else 10
SW=stem_w(); print('stem',SW)
DOT=SW*1.0
def m_dot(x=0, gap=None):
    m,a=S.glyph(f,'m',SZ,x,0); b=m.bounds
    gap = SW*0.5 if gap is None else gap
    dot=S.rect(b[2]+gap, -DOT, DOT, DOT)
    return m, dot
# wordmark: m A G I, caps tracked slightly open, kerned by eye
TRACK={'m':-2.0,'A':-1.0,'G':0.6,'I':0}
def word():
    x=0; ps=[]
    for ch in 'mAGI':
        p,a=S.glyph(f,ch,SZ,x,0); ps.append(p); x+=a+TRACK[ch]
    return S.union(ps)
W=word(); M,D=m_dot()
def items_word(fg): return [(W,fg)]
def items_mark(fg, acc=None): return [(M,fg),(D,acc or fg)]
for th in ("dark","light"):
    fg = INK_DARK_FG if th=="dark" else INK_LIGHT_FG
    write(OUT/f"wordmark-mono-on-{th}.svg", paths_svg(items_word(fg),8))
    write(OUT/f"mark-mono-on-{th}.svg", paths_svg(items_mark(fg),8))
    write(OUT/f"mark-colour-on-{th}.svg", paths_svg(items_mark(fg,ORANGE),8))
    # lockup: the full name with its stop, "mAGI." (the mark "m." is its abbreviation)
    wb=W.bounds; stop=S.rect(wb[2]+SW*0.55, -DOT, DOT, DOT)
    write(OUT/f"lockup-mono-on-{th}.svg", paths_svg([(W,fg),(stop,fg)],10))
    write(OUT/f"lockup-colour-on-{th}.svg", paths_svg([(W,fg),(stop,ORANGE)],10))
fav={}
for th,bg,fg in (("dark",INK_DARK_BG,INK_DARK_FG),("light",INK_LIGHT_BG,INK_LIGHT_FG)):
    svg=tile_svg(items_mark(fg,ORANGE),512,bg,inset=0.12); write(OUT/f"favicon-{th}.svg",svg); fav[th]={}
    for sz in (16,32,180,512):
        pth=OUT/f"favicon-{th}-{sz}.png"
        if sz==16:
            px=(f'<rect width="16" height="16" rx="3.5" fill="{bg}"/><path fill="{fg}" fill-rule="evenodd" d="M2 13V6H11V7H12V13ZM4 13V8H6V13ZM8 13V8H10V13Z"/>'
                f'<rect x="1" y="6" width="1" height="1" fill="{fg}"/><rect x="13" y="11" width="2" height="2" fill="{ORANGE}"/>')
            s_=svg_doc(16,16,px); write(OUT/("favicon-%s-16.svg"%th),s_); raster_exact(s_,pth,16)
        elif sz==32:
            px=(f'<rect width="32" height="32" rx="7" fill="{bg}"/><path fill="{fg}" fill-rule="evenodd" d="M5 25V12H17.5Q20 12 20 14.5V25ZM8 25V16.5Q8 15 9.5 15Q11 15 11 16.5V25ZM14 25V16.5Q14 15 15.5 15Q17 15 17 16.5V25Z"/>'
                f'<rect x="3" y="12" width="2" height="2" fill="{fg}"/><rect x="4" y="24" width="5" height="1" fill="{fg}"/><rect x="10" y="24" width="5" height="1" fill="{fg}"/><rect x="16" y="24" width="5" height="1" fill="{fg}"/>'
                f'<rect x="23" y="21" width="4" height="4" fill="{ORANGE}"/>')
            s_=svg_doc(32,32,px); write(OUT/("favicon-%s-32.svg"%th),s_); raster_exact(s_,pth,32)
        else: raster_exact(svg,pth,sz)
        fav[th][sz]=pth
preview("stop","mAGI · stop","a serif m and a full stop: the name cut to one letter. Terse, academic. Lettering: IBM Plex Serif Medium (OFL), respaced.",
  OUT/"lockup-colour-on-dark.svg", OUT/"lockup-mono-on-light.svg", OUT/"mark-colour-on-dark.svg", OUT/"mark-mono-on-light.svg", fav["dark"], fav["light"], OUT_ROOT/"stop-preview.png", TMP)
print("done")

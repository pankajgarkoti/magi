"""tittle: the lowercase m carries a tittle (i-dot) over its middle stem. Lettering derived from Geist (OFL) SemiBold, customised."""
import sys, pathlib, shapes as S, glyphs as G
from common import *
OUT=OUT_ROOT/"tittle"; TMP=pathlib.Path("tmp/tittle"); TMP.mkdir(parents=True, exist_ok=True)
W=int(sys.argv[1]) if len(sys.argv)>1 else 600
f=G.font('Geist[wght].ttf',wght=W)
SZ=100; CAP=71.0
# stem x-range of the m's middle stem (measured from the outline at this weight)
def mid_stem(f):
    p,_=S.glyph(f,'m',SZ); prev=None; xs=[]
    for op,args in S.segs(p):
        if op=='lineTo' and prev and abs(args[0][0]-prev[0])<0.01 and abs(args[0][1]-prev[1])>15: xs.append(prev[0])
        if args: prev=args[-1]
    xs=sorted(xs); return xs[2], xs[3]
def m_reach(x=0, rise=CAP):
    p,a=S.glyph(f,'m',SZ,x,0); l,r=mid_stem(f)
    ip,_=S.glyph(f,'i',SZ); dot=S.inter(ip,S.rect(-10,-200,200,140)); b=dot.bounds
    dot=S.move(dot, x+(l+r)/2-(b[0]+b[2])/2, 0)
    return S.union([p,dot]), a, dot
# kerning (by eye, in font-size units of 100): space after each glyph
KERN={'m':-1.0,'A':-2.6,'G':2.2,'I':0}
def word(x=0):
    parts=[]; m,a,top=m_reach(x); parts.append(('m',m)); x+=a+KERN['m']
    for ch in 'AGI':
        if ch=='I':
            # serifed I so "GI" can't read as "Gl"; serif length echoes the m's stem spacing
            ib,_=S.glyph(f,'I',SZ,0,0); b=ib.bounds; sw=b[2]-b[0]; th=sw*0.78; half=sw*1.18
            cx=x+half+1.5
            p=S.union([S.rect(cx-sw/2,-CAP,sw,CAP), S.rect(cx-half,-CAP,2*half,th), S.rect(cx-half,-th,2*half,th)])
            a=2*half+3
        else:
            p,a=S.glyph(f,ch,SZ,x,0)
        parts.append((ch,p)); x+=a+KERN[ch]
    return parts, top
parts, top = word()
allp=S.union([p for _,p in parts])
mark, _, mtop = m_reach(0)
def lockup_items(fg, accent=None):
    return [(allp, fg)] + ([(S.inter(allp, S.rect(-100,-80,1000,0)), fg)] if False else [])
# --- SVG outputs
def items_word(fg, acc=None):
    if acc is None: return [(allp,fg)]
    return [(S.diff(allp,top),fg),(top,acc)]
def items_mark(fg, acc=None):
    if acc is None: return [(mark,fg)]
    return [(S.diff(mark,mtop),fg),(mtop,acc)]
write(OUT/"wordmark-mono-on-light.svg", paths_svg(items_word(INK_LIGHT_FG),8))
write(OUT/"wordmark-mono-on-dark.svg", paths_svg(items_word(INK_DARK_FG),8))
write(OUT/"wordmark-colour-on-dark.svg", paths_svg(items_word(INK_DARK_FG,ORANGE),8))
write(OUT/"wordmark-colour-on-light.svg", paths_svg(items_word(INK_LIGHT_FG,ORANGE),8))
# lockup = mark + wordmark? here the wordmark already contains the mark; lockup = wordmark (mark is the m)
write(OUT/"mark-mono-on-light.svg", paths_svg(items_mark(INK_LIGHT_FG),6))
write(OUT/"mark-mono-on-dark.svg", paths_svg(items_mark(INK_DARK_FG),6))
write(OUT/"mark-colour-on-dark.svg", paths_svg(items_mark(INK_DARK_FG,ORANGE),6))
write(OUT/"mark-colour-on-light.svg", paths_svg(items_mark(INK_LIGHT_FG,ORANGE),6))
fav={}
for theme,bg,fg in (("dark",INK_DARK_BG,INK_DARK_FG),("light",INK_LIGHT_BG,INK_LIGHT_FG)):
    svg=tile_svg(items_mark(fg,ORANGE),512,bg,inset=0.15); write(OUT/f"favicon-{theme}.svg",svg); fav[theme]={}
    for sz in (16,32,180,512):
        pth=OUT/f"favicon-{theme}-{sz}.png"
        if sz==16:
            px=f'<rect width="16" height="16" rx="3.5" fill="{bg}"/><path fill="{fg}" d="M3 6h10v7h-2v-5h-2v5h-2v-5h-2v5h-2z"/><rect x="7" y="3" width="2" height="2" fill="{ORANGE}"/>'
            s16=svg_doc(16,16,px); write(OUT/f"favicon-{theme}-16.svg",s16); raster_exact(s16,pth,16)
        elif sz==32:
            px=f'<rect width="32" height="32" rx="7" fill="{bg}"/><path fill="{fg}" d="M6 26V15.5Q6 13 8.5 13H23.5Q26 13 26 15.5V26H22V18Q22 17 21 17H19Q18 17 18 18V26H14V18Q14 17 13 17H11Q10 17 10 18V26Z"/><rect x="14" y="7" width="4" height="4" fill="{ORANGE}"/>'
            s32=svg_doc(32,32,px); write(OUT/f"favicon-{theme}-32.svg",s32); raster_exact(s32,pth,32)
        else: raster_exact(svg,pth,sz)
        fav[theme][sz]=pth
def lockup(theme):
    bg,fg=(INK_DARK_BG,INK_DARK_FG) if theme=="dark" else (INK_LIGHT_BG,INK_LIGHT_FG)
    items=items_word(fg,ORANGE); b=[S.bounds(p) for p,_ in items]; x0=min(q[0] for q in b); y0=min(q[1] for q in b); x1=max(q[2] for q in b); y1=max(q[3] for q in b)
    h=y1-y0; gap=h*0.42; T=h*1.25; tile_bg = "#221B2E" if theme=="dark" else "#17131D"; tile_fg="#ECE8F1"
    mi=items_mark(tile_fg,ORANGE); mb=[S.bounds(p) for p,_ in mi]; mx0=min(q[0] for q in mb); my0=min(q[1] for q in mb); mx1=max(q[2] for q in mb); my1=max(q[3] for q in mb)
    s_=T*0.68/max(mx1-mx0,my1-my0); tx=-T-gap+(T-(mx1-mx0)*s_)/2-mx0*s_; ty=y0-(T-h)/2+(T-(my1-my0)*s_)/2-my0*s_
    ty0=y0-(T-h)/2
    body=f'<rect x="{-T-gap:.2f}" y="{ty0:.2f}" width="{T:.2f}" height="{T:.2f}" rx="{T*0.22:.2f}" fill="{tile_bg}"/><g transform="translate({tx:.2f} {ty:.2f}) scale({s_:.4f})">'+"".join(f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in mi)+'</g>'+"".join(f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in items)
    pad=6; vx=-T-gap-pad; vy=ty0-pad; vw=x1+pad-vx; vh=T+2*pad
    return svg_doc(round(vw,2),round(vh,2),body,None,f"{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}")
write(OUT/"lockup-on-dark.svg",lockup("dark")); write(OUT/"lockup-on-light.svg",lockup("light"))
preview("tittle","mAGI · tittle","the m carries a tittle over its middle stem: of three stems, one is marked, the orchestrator. Lettering: Geist SemiBold (OFL), customised.",
  OUT/"lockup-on-dark.svg", OUT/"lockup-on-light.svg", OUT/"mark-colour-on-dark.svg", OUT/"mark-mono-on-light.svg", fav["dark"], fav["light"], OUT_ROOT/"tittle-preview.png", TMP)
print("done")

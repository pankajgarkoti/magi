import pathlib, subprocess, tempfile
from PIL import Image, ImageDraw, ImageFont
import shapes as S
FONTS = pathlib.Path(__file__).resolve().parent / "fonts"
OUT_ROOT = pathlib.Path("/home/pankajgarkoti/Desktop/code/magi/branding/options-v2")
INK_DARK_BG = "#100C17"; INK_DARK_FG = "#ECE8F1"
INK_LIGHT_BG = "#F2F0EB"; INK_LIGHT_FG = "#17131D"
PURPLE = "#7A4FD6"; GREEN = "#4FE08A"; ORANGE = "#FF7A1F"

def svg_doc(w, h, body, bg=None, vb=None):
    vb = vb or f"0 0 {w:g} {h:g}"
    bgr = f'<rect width="100%" height="100%" fill="{bg}"/>' if bg else ""
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:g}" height="{h:g}" viewBox="{vb}">{bgr}{body}</svg>\n'

def paths_svg(items, pad, bg=None, height=None):
    """items: list of (pathops.Path, fill). Fit tight bbox + pad. returns svg string."""
    xs=[S.bounds(p) for p,_ in items]; x0=min(b[0] for b in xs); y0=min(b[1] for b in xs); x1=max(b[2] for b in xs); y1=max(b[3] for b in xs)
    w=x1-x0+2*pad; h=y1-y0+2*pad
    body="".join(f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in items)
    vb=f"{x0-pad:.2f} {y0-pad:.2f} {w:.2f} {h:.2f}"
    H = height or h; W = w*H/h
    return svg_doc(round(W,2), round(H,2), body, bg, vb)

def tile_svg(items, size=512, bg="#100C17", radius=0.22, inset=0.22, optical_dy=0.0):
    """app-icon tile: rounded square with the mark centred inside."""
    xs=[S.bounds(p) for p,_ in items]; x0=min(b[0] for b in xs); y0=min(b[1] for b in xs); x1=max(b[2] for b in xs); y1=max(b[3] for b in xs)
    w=x1-x0; h=y1-y0; avail=size*(1-2*inset); s=avail/max(w,h)
    tx=(size-w*s)/2 - x0*s; ty=(size-h*s)/2 - y0*s + optical_dy*size
    body=f'<rect width="{size}" height="{size}" rx="{size*radius:.2f}" fill="{bg}"/>' + f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s:.5f})">' + "".join(f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in items) + "</g>"
    return svg_doc(size, size, body)

def write(path, text):
    path=pathlib.Path(path); path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text)

def raster(svg_path, png_path, w=None, h=None):
    args=["magick","-background","none","-density","600",str(svg_path)]
    if w or h: args += ["-resize", f"{w or ''}x{h or ''}"]
    subprocess.run(args+[str(png_path)], check=True)

def raster_exact(svg_text, png_path, size):
    """render an svg whose width/height == size directly (no resample) for crisp favicons"""
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False) as t: t.write(svg_text)
    subprocess.run(["magick","-background","none",t.name,"-resize",f"{size}x{size}",str(png_path)], check=True)

def mono(size, wght=None):
    f = ImageFont.truetype(str(FONTS/"IBMPlexMono-Medium.ttf"), size); return f

def preview(name, title, desc, lockup_dark_svg, lockup_light_svg, mark_dark_svg, mark_light_svg, fav_dark, fav_light, out_png, tmp):
    """fav_dark/light: dict size->png path. svgs are file paths."""
    W=1600; DK=INK_DARK_BG; LT=INK_LIGHT_BG
    img=Image.new("RGB",(W,1640),DK); dr=ImageDraw.Draw(img)
    def place(svg, box, maxw, maxh, center=True):
        p=tmp/(pathlib.Path(svg).stem+f"-{maxw}x{maxh}.png"); raster(svg,p,maxw,maxh)
        im=Image.open(p).convert("RGBA")
        if im.width>maxw: im=im.resize((maxw, round(im.height*maxw/im.width)), Image.LANCZOS)
        x=box[0]+(box[2]-im.width)//2; y=box[1]+(box[3]-im.height)//2
        img.paste(im,(x,y),im)
    lab=mono(15); small=mono(13)
    # 1 dark lockup
    dr.rectangle((0,0,W,460),fill=DK); place(lockup_dark_svg,(0,0,W,460),1180,180)
    dr.text((40,425),"lockup · on dark",font=small,fill="#6E6779")
    # 2 light lockup
    dr.rectangle((0,460,W,920),fill=LT); place(lockup_light_svg,(0,460,W,460),1180,180)
    dr.text((40,885),"lockup · on light",font=small,fill="#8A8590")
    # 3 mark large + favicons
    dr.rectangle((0,920,W//2,1520),fill=DK); dr.rectangle((W//2,920,W,1520),fill=LT)
    place(mark_dark_svg,(40,950,360,360),300,300); place(mark_light_svg,(W//2+40,950,360,360),300,300)
    for side,fav,col in ((0,fav_dark,"#6E6779"),(W//2,fav_light,"#8A8590")):
        x=side+440; ybase=1300
        for sz in (180,32,16):
            im=Image.open(fav[sz]).convert("RGBA")
            if sz==180: ImageDraw.Draw(img).rounded_rectangle((x-1,ybase-sz-1,x+sz,ybase),radius=round(sz*0.22),outline=("#2E2639" if side==0 else "#D6D1C9"))
            img.paste(im,(x,ybase-sz),im)
            ImageDraw.Draw(img).text((x,ybase+12),f"{sz}",font=small,fill=col); x+=sz+28
        dr.text((side+40,1485),"mark · favicons at 1:1 pixel size",font=small,fill=col)
    # footer
    dr.rectangle((0,1520,W,1640),fill="#000000")
    dr.text((40,1542),title,font=mono(20),fill="#ECE8F1")
    dr.text((40,1578),desc,font=lab,fill="#9C95A6")
    img.save(out_png)

def bbox(items):
    b=[S.bounds(p) for p,_ in items]; return min(q[0] for q in b), min(q[1] for q in b), max(q[2] for q in b), max(q[3] for q in b)

def lockup_svg(word_items, mark_items, tile_bg=None, gap_ratio=0.42, tile_ratio=1.25, mark_fill=0.68, pad=6, mark_h_ratio=None, align="center"):
    """horizontal lockup: mark (optionally on a rounded tile) + wordmark. word_items/mark_items in their own coords."""
    x0,y0,x1,y1=bbox(word_items); h=y1-y0; gap=h*gap_ratio
    mx0,my0,mx1,my1=bbox(mark_items)
    parts=[]
    if tile_bg:
        T=h*tile_ratio; s_=T*mark_fill/max(mx1-mx0,my1-my0)
        ty0=y0-(T-h)/2; tx0=x0-gap-T
        parts.append(f'<rect x="{tx0:.2f}" y="{ty0:.2f}" width="{T:.2f}" height="{T:.2f}" rx="{T*0.22:.2f}" fill="{tile_bg}"/>')
        tx=tx0+(T-(mx1-mx0)*s_)/2-mx0*s_; ty=ty0+(T-(my1-my0)*s_)/2-my0*s_
        left=tx0; top=min(ty0,y0); bottom=max(ty0+T,y1)
    else:
        mh=h*(mark_h_ratio or 1.0); s_=mh/(my1-my0); mw=(mx1-mx0)*s_
        tx=x0-gap-mw-mx0*s_; ty=y0-(mh-h)/2-my0*s_
        left=x0-gap-mw; top=min(y0,y0-(mh-h)/2); bottom=max(y1,y0-(mh-h)/2+mh)
    parts.append(f'<g transform="translate({tx:.2f} {ty:.2f}) scale({s_:.5f})">'+"".join(f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in mark_items)+'</g>')
    parts+= [f'<path fill="{c}" d="{S.d(p)}"/>' for p,c in word_items]
    vx=left-pad; vy=top-pad; vw=x1+pad-vx; vh=bottom-top+2*pad
    return svg_doc(round(vw,2),round(vh,2),"".join(parts),None,f"{vx:.2f} {vy:.2f} {vw:.2f} {vh:.2f}")

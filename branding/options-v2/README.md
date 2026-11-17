mAGI logo options, v2 (15 Nov 2026). Not wired into anything.

  tittle/   tittle-preview.png    lowercase m with a tittle (i-dot) over its middle stem: of three stems, one is
                                  marked (the orchestrator). Orange dot is the only colour. Geist SemiBold, I given slab
                                  serifs so "GI" can't read as "Gl". Lockup: app tile + wordmark.
  reverse/  reverse-preview.png   lowercase m, then AGI in reverse video (a selection, a warning label). As a mark the
                                  block shrinks to a cursor: "m▮". JetBrains Mono Bold. Lockup: app tile + wordmark.
  stop/     stop-preview.png      serif "m." as the mark; full name "mAGI." as the lockup. Terse, academic register.
                                  IBM Plex Serif Medium; the stop redrawn as a square at stem weight.

Per option: mark-{mono,colour}-on-{dark,light}.svg, wordmark-*.svg, lockup-*.svg,
favicon-{dark,light}-{16,32,180,512}.png (16 and 32 are hand-pixelled: favicon-*-16.svg / -32.svg; 180/512 from favicon-*.svg).
All lettering is outlined paths (no <text>, no font dependency, no strokes).

Fonts (all SIL Open Font License 1.1, outlines derived and modified, which OFL permits for artwork):
  Geist (Vercel), JetBrains Mono (JetBrains), IBM Plex Serif (IBM). Source files: github.com/google/fonts/tree/main/ofl.
Palette: dark bg #100C17, fg #ECE8F1; light bg #F2F0EB, fg #17131D; accent orange #FF7A1F.

Regenerate: src/make_{tittle,reverse,stop}.py. Needs python with fonttools, skia-pathops, pillow; ImageMagick (librsvg)
for rendering; the three fonts in src/fonts/ (Geist[wght].ttf, JetBrainsMono[wght].ttf, IBMPlexSerif-Medium.ttf).

Dropped during iteration (rendered, judged, not shipped): triad marks (pinwheels/Y/Penrose read as fans or the
Mercedes star), converging-stroke A (too close to the Arch Linux pyramid), Reuleaux ring (reads as a recycling/warning
sign), three-disc "consensus" (reads as the radiation trefoil at small sizes; outline version is a Venn cliché),
raised-stem m (reads as "rh").

#!/usr/bin/env python3
"""
Gallery-wide palette-swatch sweep.

Each standardized MvC2 sprite sheet carries a palette reference bar: a band of
rows where the body indices appear as equal-width segments in ascending order
(0..15 or 1..15). That bar is a spatial ground-truth for the correct index order.

For every image we read which index the file actually uses at each swatch slot:
  - identity           -> palette already in the correct order (fine)
  - clean permutation  -> palette is scrambled; the permutation IS the fix
  - otherwise          -> can't determine from the swatch

This detects (and, because the permutation is the correction, can fix) skins
whose palette index order is wrong even when the character pixels don't align to
the base sprite. It does NOT catch base-pixel + scrambled-palette files (those
render as visible confetti and their swatch is the base's, i.e. identity).

Usage:
    python tools/swatch_sweep.py <gallery_dir> [--reviews DIR]

Dry-run only: reports hits and (with --reviews) writes intended/in-game/proposed
review sheets. Never modifies the gallery.
"""
import argparse
import os
import sys
from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvc2_skin_processor import load_sprite_bases
from mvc2_extract.characters import CHARACTERS, PLAYABLE_CHARS, safe_name

MIN_SEG_WIDTH = 3       # ignore thinner runs when chaining swatch segments
MIN_CHAIN = 14          # a real body swatch covers ~15 indices


def runs(row):
    out = []; c = 0; n = len(row)
    while c < n:
        v = row[c]; j = c
        while j < n and row[j] == v:
            j += 1
        out.append((int(v), c, j - c)); c = j
    return out


def find_body_swatch(bpx):
    """Return (band_rows, {index:(col_start,col_end)}) for the body (0..15) bar."""
    h, w = bpx.shape
    best = None
    for y in range(h):
        rl = runs(bpx[y])
        i = 0
        while i < len(rl):
            chain = [rl[i]]; j = i
            while j + 1 < len(rl):
                v, sc, wd = rl[j + 1]
                pv, _, pw = chain[-1]
                if v == pv + 1 and wd >= MIN_SEG_WIDTH and abs(wd - pw) <= max(3, pw // 2):
                    chain.append(rl[j + 1]); j += 1
                else:
                    break
            if len(chain) >= MIN_CHAIN and max(v for v, _, _ in chain) <= 15:
                mapping = {v: (sc, sc + wd) for (v, sc, wd) in chain}
                cover = len(mapping)
                if best is None or cover > best[2]:
                    best = (y, mapping, cover)
            i = j + 1 if j > i else i + 1
    if best is None:
        return None
    y0, mapping, _ = best
    keys = sorted(mapping)

    def ok(y):
        return all(bpx[y, (mapping[k][0] + mapping[k][1]) // 2] == k for k in keys)
    rows = [y0]
    y = y0 - 1
    while y >= 0 and ok(y):
        rows.append(y); y -= 1
    y = y0 + 1
    while y < h and ok(y):
        rows.append(y); y += 1
    return sorted(rows), mapping


def read_perm(own_px, rows, mapping):
    """Majority index the file uses at each swatch slot (body indices only).

    Samples the CENTRE of each segment (and centre rows of the band) to avoid
    anti-aliased edges between neighbouring swatch colours, which otherwise add
    noise that breaks the onto-itself permutation test."""
    if len(rows) >= 3:
        m = len(rows) // 2
        rows = rows[max(0, m - 1):m + 2]
    perm = {}
    for k, (a, b) in mapping.items():
        w = b - a
        pad = max(1, w // 4)
        ca, cb = a + pad, b - pad
        if cb <= ca:
            ca, cb = a, b
        s = own_px[np.ix_(rows, list(range(ca, cb)))].ravel()
        s = s[s < 16]
        perm[k] = int(Counter(s.tolist()).most_common(1)[0][0]) if s.size else -1
    return perm


def classify(perm, mapping):
    keys = sorted(mapping)
    vals = [perm[k] for k in keys]
    if all(perm[k] == k for k in keys):
        return "ok", None
    # A genuine scramble permutes the swatch index set ONTO ITSELF (indices swap
    # among themselves). A ~1px swatch misalignment instead shifts slots onto
    # neighbouring/background indices (e.g. a body slot -> index 0), so the value
    # set differs from the key set — reject those as unreliable reads.
    if set(vals) != set(keys):
        return "cant-tell", None
    full = {k: perm[k] for k in keys}
    for k in range(16):
        full.setdefault(k, k)          # non-swatch indices (e.g. 0) stay identity
    return "scrambled", full


def render(px, palette_rgb, w, h, num_rows):
    im = Image.new("P", (w, h)); flat = []
    for i in range(256):
        if i < len(palette_rgb):
            flat += list(palette_rgb[i][:3])
        else:
            flat += [0, 0, 0]
    im.putpalette(flat); im.putdata(px.flatten().tolist()); im.info["transparency"] = 0
    bg = Image.new("RGBA", (w, h), (110, 110, 110, 255)); bg.alpha_composite(im.convert("RGBA"))
    return bg.convert("RGB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gallery")
    ap.add_argument("--reviews", metavar="DIR", help="write intended/in-game/proposed review sheets")
    args = ap.parse_args()

    bases = load_sprite_bases()
    f2c = {safe_name(CHARACTERS[c]): c for c in PLAYABLE_CHARS}
    if args.reviews:
        os.makedirs(args.reviews, exist_ok=True)

    no_swatch = []
    hits = []           # (folder, fname, perm)  — confident scrambled
    uncertain = []      # (folder, fname)        — non-identity read, not a clean onto-itself perm
    scanned = 0
    for folder in sorted(os.listdir(args.gallery)):
        fp = os.path.join(args.gallery, folder)
        if not os.path.isdir(fp):
            continue
        cid = f2c.get(folder)
        if cid is None or cid not in bases:
            continue
        base = bases[cid]
        bpx = base['pixels']; bh, bw, nr = base['height'], base['width'], base['num_rows']
        if bpx.ndim == 1:
            bpx = bpx.reshape(bh, bw)
        sw = find_body_swatch(bpx)
        if sw is None:
            no_swatch.append(folder)
            continue
        rows, mapping = sw
        char_hits = []
        for fname in sorted(os.listdir(fp)):
            if not fname.lower().endswith(".png"):
                continue
            scanned += 1
            im = Image.open(os.path.join(fp, fname)); own = np.array(im); im.close()
            if own.shape != (bh, bw):
                own = np.array(Image.fromarray(own).resize((bw, bh), Image.NEAREST))
            perm = read_perm(own, rows, mapping)
            status, full = classify(perm, mapping)
            if status == "scrambled":
                char_hits.append((fname, full))
                hits.append((folder, fname, full))
            elif status == "cant-tell":
                uncertain.append((folder, fname))
        if char_hits:
            print(f"\n{folder}: {len(char_hits)} scrambled")
            for fname, full in char_hits:
                seq = [full[k] for k in range(16)]
                print(f"    {fname}\n        perm(base->own) = {seq}")

    print(f"\n{'='*60}\nScanned {scanned} images. Scrambled (fixable) hits: {len(hits)}")
    print(f"Uncertain reads (non-identity, not a clean permutation): {len(uncertain)}")
    for folder, fname in uncertain:
        print(f"    ? {folder}/{fname}")
    if no_swatch:
        print(f"No body swatch detected for {len(no_swatch)} chars: {', '.join(no_swatch)}")

    if args.reviews and hits:
        build_reviews(hits, bases, f2c, args.gallery, args.reviews)


def build_reviews(hits, bases, f2c, gallery, outdir):
    TW = 230
    per_page = 6
    pages = [hits[i:i + per_page] for i in range(0, len(hits), per_page)]
    for pi, page in enumerate(pages):
        rowh = 0; panels = []
        for folder, fname, full in page:
            base = bases[f2c[folder]]
            bpx = base['pixels']; bh, bw, nr = base['height'], base['width'], base['num_rows']
            if bpx.ndim == 1:
                bpx = bpx.reshape(bh, bw)
            im = Image.open(os.path.join(gallery, folder, fname)); own = np.array(im)
            pal = im.getpalette(); im.close()
            if own.shape != (bh, bw):
                own = np.array(Image.fromarray(own).resize((bw, bh), Image.NEAREST))
            pal = (pal or []) + [0] * (768 - len(pal))
            opal = [(pal[i*3], pal[i*3+1], pal[i*3+2]) for i in range(256)]
            fixed = list(opal)
            for k in range(16):
                fixed[k] = opal[full[k]]
            intended = render(own, opal, bw, bh, nr)
            ingame = render(bpx, opal, bw, bh, nr)
            proposed = render(bpx, fixed, bw, bh, nr)
            panels.append((folder, fname, intended, ingame, proposed))
            rowh = max(rowh, TW * intended.height // intended.width + 20)
        sheet = Image.new("RGB", (3 * TW + 40, len(panels) * (rowh + 8) + 40), (25, 25, 25))
        d = ImageDraw.Draw(sheet)
        d.text((10, 8), f"SWATCH SWEEP p{pi+1}: INTENDED (file) | IN-GAME (base+palette) | PROPOSED (swatch-fixed)", fill=(255, 255, 0))
        for i, (folder, fname, a, g, p) in enumerate(panels):
            y = 30 + i * (rowh + 8)
            d.text((10, y), f"{folder}/{fname}", fill=(255, 200, 120))
            for j, im in enumerate((a, g, p)):
                t = im.resize((TW, int(im.height * TW / im.width)), Image.NEAREST)
                sheet.paste(t, (10 + j * (TW + 10), y + 14))
        sheet.save(os.path.join(outdir, f"sweep_{pi+1}.png"))
        print(f"  wrote sweep_{pi+1}.png ({len(panels)} hits)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Re-standardize an existing gallery of skins through the fixed palette derivation.

For every skin it re-derives the palette in the game's index order by sampling
the skin's own rendered pixels (see derive_canonical_palette_rows in the main
processor). Skins already in canonical order come out byte-for-byte identical
(same palette hash -> same filename) and are left untouched. Skins whose palette
was stored in a scrambled index order (render fine as a file, "confetti" in game)
are corrected; because the filename embeds a hash of the palette, a corrected
skin gets a NEW filename, so the old file is removed and the new one written.

Dry-run by default: reports what WOULD change and writes before/after preview
images to a staging dir, without modifying the gallery. Use --apply to perform
the file swaps.

Usage:
    python tools/restandardize_gallery.py <gallery_dir> [--apply] [--preview DIR]
"""
import argparse
import gc
import os
import re
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mvc2_skin_processor import (
    load_sprite_bases, derive_canonical_palette_rows, _legacy_palette_rows,
    get_palette_hash, build_output_name,
)
from mvc2_extract.characters import CHARACTERS, PLAYABLE_CHARS, safe_name
from mvc2_extract.renderer import render_sprite, render_composite

HASH_RE = re.compile(r"^[0-9a-f]{8}$")


def folder_to_cid():
    return {safe_name(CHARACTERS[c]): c for c in PLAYABLE_CHARS}


def split_name(fname):
    """Char_<hash>_<descriptor>.png -> (prefix_before_hash, hash, descriptor)."""
    stem = os.path.splitext(fname)[0]
    parts = stem.split("_")
    for i, p in enumerate(parts):
        if HASH_RE.match(p):
            return "_".join(parts[:i]), p, "_".join(parts[i+1:])
    return stem, None, ""


# A fix is only trustworthy if rendering the derived palette on the base sprite
# reproduces the source's OWN appearance (its intended look). If the mean per-pixel
# colour error over the body exceeds this, the source has a different pixel
# structure than the base (not a pure re-indexing), so the derivation can't
# recover it — leave it alone rather than degrade it. Genuine re-indexed skins
# score ~0; everything else scores 60+, so the threshold is very forgiving.
FIX_FIDELITY_THRESHOLD = 20.0


def restandardize_one(path, base):
    """Re-derive a skin on the base sprite, and only accept the result if it
    faithfully reproduces the source's own appearance.

    Returns (status, result_img):
      ('unchanged', None) — already standard (base pixels); leave as-is.
      ('cantfix', None)   — source isn't a re-indexing of the base; can't auto-fix.
      ('fixed', img)      — corrected image that reproduces the source's look.
    """
    img = Image.open(path)
    pal = img.getpalette()
    own_px = np.array(img)
    img.close()
    if not pal:
        return ('unchanged', None)
    own_pal = [(pal[i*3], pal[i*3+1], pal[i*3+2]) for i in range(len(pal)//3)]

    bw, bh, num_rows = base['width'], base['height'], base['num_rows']
    bpx = base['pixels']
    if bpx.ndim == 1:
        bpx = bpx.reshape(bh, bw)
    default_pal = base['default_palette']

    aligned = (own_px.shape[0] % bh == 0 and own_px.shape[1] % bw == 0
               and own_px.shape[0] // bh == own_px.shape[1] // bw
               and own_px.shape[0] >= bh)
    if not aligned:
        return ('cantfix', None)

    if own_px.shape != (bh, bw):
        own_px = np.array(Image.fromarray(own_px).resize((bw, bh), Image.NEAREST))
    # Already-standard skins have base pixels; leave them exactly as-is.
    if np.array_equal(own_px, bpx):
        return ('unchanged', None)

    rows = derive_canonical_palette_rows(bpx, own_px, own_pal, num_rows, default_pal)

    # Fidelity gate: rendering the derived palette on the base sprite must match
    # the source's own render, pixel for pixel. Transparent pixels are mapped to a
    # sentinel background so that "source transparent but fix coloured" (and vice
    # versa) counts as a mismatch — this is what catches misaligned sources whose
    # derivation collapses to black.
    BG = np.array([110, 110, 110], np.float64)
    ncol = num_rows * 16
    derived_flat = np.zeros((256, 3), np.float64)
    for k in range(ncol):
        derived_flat[k] = rows[k // 16][k % 16][:3]
    own_arr = np.zeros((256, 3), np.float64)
    for i in range(min(256, len(own_pal))):
        own_arr[i] = own_pal[i]
    src = own_arr[own_px]
    src[own_px == 0] = BG
    fix = derived_flat[bpx]
    fix[bpx == 0] = BG
    mask = (bpx != 0) | (own_px != 0)
    if mask.any():
        fidelity = float(np.sqrt(((src[mask] - fix[mask]) ** 2).sum(1)).mean())
        if fidelity > FIX_FIDELITY_THRESHOLD:
            return ('cantfix', None)

    if num_rows > 1:
        result = render_composite(bpx, bw, bh, rows, num_rows, default_pal)
    else:
        result = render_sprite(bpx.tobytes(), bw, bh, rows[0])
    return ('fixed', result)


def preview(path, out):
    with Image.open(path) as src:
        im = src.convert("RGBA")
    bg = Image.new("RGBA", im.size, (110, 110, 110, 255))
    bg.alpha_composite(im)
    bg.convert("RGB").save(out)
    im.close()
    bg.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gallery")
    ap.add_argument("--apply", action="store_true", help="perform file swaps (default: dry-run)")
    ap.add_argument("--preview", metavar="DIR", help="write before/after preview PNGs here")
    args = ap.parse_args()

    bases = load_sprite_bases()
    f2c = folder_to_cid()
    if args.preview:
        os.makedirs(args.preview, exist_ok=True)

    changes = []       # (folder, old_name, new_name)  — auto-fixed
    cantfix = []       # (folder, name)                — non-standard but not safely fixable
    scanned = unmapped = 0
    for folder in sorted(os.listdir(args.gallery)):
        fp = os.path.join(args.gallery, folder)
        if not os.path.isdir(fp):
            continue
        cid = f2c.get(folder)
        if cid is None or cid not in bases:
            unmapped += 1
            print(f"  [skip] no base for folder: {folder}")
            continue
        base = bases[cid]
        for fname in sorted(os.listdir(fp)):
            if not fname.lower().endswith(".png"):
                continue
            scanned += 1
            if scanned % 200 == 0:
                gc.collect()
            src = os.path.join(fp, fname)
            status, result = restandardize_one(src, base)
            if status == 'unchanged':
                continue
            if status == 'cantfix':
                cantfix.append((folder, fname))
                continue
            new_hash = get_palette_hash(result)
            prefix, old_hash, desc = split_name(fname)
            if old_hash is None or new_hash == old_hash:
                result.close()
                continue
            new_name = build_output_name(prefix, new_hash, desc)
            changes.append((folder, fname, new_name))
            if args.preview:
                # BEFORE = original (broken) file; AFTER = corrected render
                preview(src, os.path.join(args.preview, f"{folder}__{fname}__BEFORE.png"))
                tmp = os.path.join(args.preview, "__tmp.png")
                result.save(tmp)
                preview(tmp, os.path.join(args.preview, f"{folder}__{new_name}__AFTER.png"))
                os.remove(tmp)
            if args.apply:
                result.save(os.path.join(fp, new_name))
                os.remove(src)
            result.close()

    if args.preview and os.path.exists(os.path.join(args.preview, "__tmp.png")):
        os.remove(os.path.join(args.preview, "__tmp.png"))

    print(f"\nScanned {scanned} skins across {len(f2c)} characters.")
    print(f"{'APPLIED' if args.apply else 'WOULD CHANGE'} (auto-fixed): {len(changes)} skins\n")
    for folder, old, new in changes:
        print(f"  {folder}/")
        print(f"      - {old}")
        print(f"      + {new}")
    if cantfix:
        print(f"\nNON-STANDARD, NOT SAFELY AUTO-FIXABLE (left untouched; "
              f"manual/re-source if broken in-game): {len(cantfix)} skins")
        for folder, name in cantfix:
            print(f"  {folder}/{name}")


if __name__ == "__main__":
    main()

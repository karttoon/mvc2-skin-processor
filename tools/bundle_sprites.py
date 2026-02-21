#!/usr/bin/env python3
"""
Bundle all 56 MvC2 character base sprites into self-contained .npz files.

Run once to extract sprites from img2020.dat and composite_bases/ into sprite_bases/.
After this, img2020.dat is no longer needed.

Usage:
    python bundle_sprites.py [--imgdat PATH]
"""
import argparse
import os
import shutil
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mvc2_extract.characters import (
    CHARACTERS, PLAYABLE_CHARS, PALETTE_ROWS, safe_name, palette_rows,
)
from mvc2_extract.sprites import ImgDat

COMPOSITE_BASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "composite_bases")
SPRITE_BASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprite_bases")


def main():
    parser = argparse.ArgumentParser(description="Bundle MvC2 base sprites into sprite_bases/")
    parser.add_argument("--imgdat", default=os.environ.get("MVC2_IMGDAT"),
                        help="Path to img2020.dat (or set MVC2_IMGDAT env var)")
    args = parser.parse_args()

    if not args.imgdat:
        parser.error("--imgdat is required (or set MVC2_IMGDAT env var)")

    os.makedirs(SPRITE_BASES_DIR, exist_ok=True)

    print("=" * 60)
    print("Bundling MvC2 base sprites")
    print("=" * 60)
    print(f"img2020.dat: {args.imgdat}")
    print(f"Composite bases: {COMPOSITE_BASES_DIR}")
    print(f"Output: {SPRITE_BASES_DIR}")
    print()

    # Load img2020.dat for single-row characters
    imgdat = ImgDat(args.imgdat)

    bundled = 0
    for cid in sorted(PLAYABLE_CHARS):
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        num_rows = palette_rows(cid)

        npz_path = os.path.join(SPRITE_BASES_DIR, f"{sname}.npz")

        if num_rows > 1:
            # Multi-row: copy from composite_bases/
            src = os.path.join(COMPOSITE_BASES_DIR, f"{sname}.npz")
            if os.path.exists(src):
                # Load and re-save to ensure consistent format
                base = np.load(src)
                np.savez_compressed(
                    npz_path,
                    pixels=base['pixels'],
                    width=int(base['width']),
                    height=int(base['height']),
                    num_rows=int(base['num_rows']),
                    default_palette=base['default_palette'],
                )
                w, h = int(base['width']), int(base['height'])
                print(f"  0x{cid:02X} {cname:<25} {w:4d}x{h:<4d}  {num_rows} rows (composite)")
                bundled += 1
            else:
                print(f"  0x{cid:02X} {cname:<25} MISSING composite base!")
        else:
            # Single-row: extract from img2020.dat
            sprite = imgdat.get_sprite(cid, img_id=0)
            if not sprite:
                print(f"  0x{cid:02X} {cname:<25} MISSING in img2020.dat!")
                continue

            raw_pixels, w, h = sprite
            pixels_2d = np.frombuffer(raw_pixels, dtype=np.uint8).reshape(h, w)

            # Extract default palette from the sprite by rendering with a known palette
            # For single-row chars, default palette is just 16 black entries
            # (no meaningful default — each skin provides its own)
            default_pal = np.zeros((16, 3), dtype=np.uint8)

            np.savez_compressed(
                npz_path,
                pixels=pixels_2d,
                width=w,
                height=h,
                num_rows=1,
                default_palette=default_pal,
            )
            print(f"  0x{cid:02X} {cname:<25} {w:4d}x{h:<4d}  1 row  (img2020.dat)")
            bundled += 1

    print(f"\nBundled {bundled}/56 character sprites into {SPRITE_BASES_DIR}")

    # Report total size
    total_size = sum(
        os.path.getsize(os.path.join(SPRITE_BASES_DIR, f))
        for f in os.listdir(SPRITE_BASES_DIR)
        if f.endswith('.npz')
    )
    print(f"Total size: {total_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()

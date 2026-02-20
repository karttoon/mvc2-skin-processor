#!/usr/bin/env python3
"""
Extract composite base sprites from zachd.com Default skins.

For characters with multi-row palettes (accessories like shields, animals, etc.),
the Default skins contain the composite sprite layout with pixel indices spanning
multiple palette rows. These serve as the "base sprite" for re-rendering mix
skins with proper accessory support.

Output: composite_bases/{safe_name}.npz per character, containing:
  - pixels: 2D numpy array of palette indices (uint8)
  - width, height: image dimensions
  - num_rows: number of palette rows
  - default_palette: full default palette (for fallback accessory colors)

For characters with only 1 palette row, no base is needed (img2020.dat img_id=0 suffices).
"""
import os
import sys
import numpy as np
from pathlib import Path
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mvc2_extract.characters import (
    CHARACTERS, PLAYABLE_CHARS, PALETTE_ROWS, safe_name, palette_rows,
)

# Map from merged folder names to character IDs
# (merged uses slightly different naming from safe_name)
MERGED_FOLDER_TO_CID = {}
for cid in PLAYABLE_CHARS:
    sn = safe_name(CHARACTERS[cid])
    MERGED_FOLDER_TO_CID[sn] = cid


def find_default_skin(char_dir):
    """Find a Default skin PNG in a character directory. Prefer LP."""
    defaults = sorted(char_dir.glob("*Default*"))
    if not defaults:
        return None
    # Prefer LP variant
    for d in defaults:
        if "_LP_" in d.name or d.name.endswith("_Default.png"):
            return d
    return defaults[0]


def main():
    merged_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\Storage\MvC2Modding\MvC2_Skins\Mixes\merged"
    )
    out_dir = Path(__file__).parent / "composite_bases"
    out_dir.mkdir(exist_ok=True)

    print("=" * 60)
    print("Extracting composite base sprites from Default skins")
    print("=" * 60)
    print(f"Source: {merged_dir}")
    print(f"Output: {out_dir}")
    print()

    extracted = 0
    skipped = 0

    for cid in sorted(PALETTE_ROWS.keys()):
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        num_rows = palette_rows(cid)

        char_dir = merged_dir / sname
        if not char_dir.is_dir():
            print(f"  {cname:<25} MISSING (no folder)")
            continue

        default_skin = find_default_skin(char_dir)
        if not default_skin:
            print(f"  {cname:<25} NO DEFAULT SKIN")
            continue

        # Load the Default skin
        img = Image.open(default_skin)
        pixels = np.array(img)
        pal = img.getpalette()
        pal_len = len(pal) // 3 if pal else 0
        h, w = pixels.shape
        max_idx = int(pixels.max())
        actual_rows = (max_idx // 16) + 1

        # Extract full palette as list of (R, G, B) tuples
        full_palette = []
        if pal:
            for i in range(min(pal_len, num_rows * 16)):
                full_palette.append((pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]))

        img.close()

        # Verify the Default skin actually has multi-row data
        if max_idx < 16:
            print(f"  {cname:<25} Default only uses indices 0-{max_idx} (expected multi-row)")
            # Still save it — might be useful

        # Save composite base
        out_path = out_dir / f"{sname}.npz"
        np.savez_compressed(
            out_path,
            pixels=pixels.astype(np.uint8),
            width=w,
            height=h,
            num_rows=num_rows,
            default_palette=np.array(full_palette, dtype=np.uint8),
        )

        extracted += 1
        print(f"  {cname:<25} {w}x{h}  rows={num_rows}  max_idx={max_idx}  "
              f"pal_entries={pal_len}  -> {out_path.name}")

    print(f"\nDone! Extracted {extracted} composite bases, skipped {skipped}")
    print(f"Output: {out_dir}")


if __name__ == "__main__":
    main()

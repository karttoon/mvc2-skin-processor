#!/usr/bin/env python3
"""
Generate default palette hashes for all 56 MvC2 characters.

Uses the bundled sprite_bases/ NPZ files (which contain default_palette)
to compute palette hashes for each character's 6 default button colors.
Outputs default_hashes.json to the project root.

Usage:
    python tools/generate_default_hashes.py
"""
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mvc2_extract.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS, safe_name, palette_rows, palette_slot_map,
)

SPRITE_BASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sprite_bases")
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "default_hashes.json")


def compute_palette_hash(pal_flat, num_rows):
    """Compute SHA256 hash of palette bytes (matching get_palette_hash in mvc2_skin_processor)."""
    pal_bytes = bytes(pal_flat[:num_rows * 48])
    return hashlib.sha256(pal_bytes).hexdigest()


def main():
    print("=" * 60)
    print("Generate Default Palette Hashes")
    print("=" * 60)

    all_hashes = {}
    total = 0

    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        npz_path = os.path.join(SPRITE_BASES_DIR, f"{sname}.npz")

        if not os.path.exists(npz_path):
            continue

        base = np.load(npz_path)
        default_pal = base.get('default_palette')
        if default_pal is None:
            continue

        num_rows = int(base.get('num_rows', 1))
        slot_map_fn = palette_slot_map(cid)

        char_hashes = {}

        for bi, btn in enumerate(BUTTON_NAMES):
            # Build the flat RGB palette for this button color
            pal_flat = []

            if num_rows > 1:
                # Multi-row: each row uses a different palette slot
                for row in range(num_rows):
                    slot_offset = slot_map_fn[row] if row < len(slot_map_fn) else row
                    pal_base = bi * 8 + slot_offset

                    for ci in range(16):
                        idx = pal_base * 16 + ci
                        # Default palette is stored as the full indexed palette
                        # from the default skin image
                        pal_idx = row * 16 + ci
                        if pal_idx < len(default_pal):
                            r, g, b = int(default_pal[pal_idx][0]), int(default_pal[pal_idx][1]), int(default_pal[pal_idx][2])
                        else:
                            r, g, b = 0, 0, 0
                        pal_flat.extend([r, g, b])
            else:
                # Single-row: straightforward
                for ci in range(16):
                    if ci < len(default_pal):
                        r, g, b = int(default_pal[ci][0]), int(default_pal[ci][1]), int(default_pal[ci][2])
                    else:
                        r, g, b = 0, 0, 0
                    pal_flat.extend([r, g, b])

            h = compute_palette_hash(pal_flat, num_rows)
            char_hashes[btn] = h
            total += 1

        all_hashes[sname] = char_hashes
        print(f"  {sname:<25} {len(char_hashes)} button hashes")

    # Write output
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(all_hashes, f, indent=2, sort_keys=True)

    print(f"\n{'=' * 60}")
    print(f"Generated {total} hashes for {len(all_hashes)} characters")
    print(f"Output: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()

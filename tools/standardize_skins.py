#!/usr/bin/env python3
"""
Standardize existing MvC2 skin images.

Reads the 16-color palette from each existing skin PNG (regardless of its
source or dimensions), then re-renders it on the canonical base sprite from
img2020.dat. This ensures all skins for a character are the same size,
format (indexed-color mode P), and base sprite.

For combined folders (Iron_Men, Wolverines), the script guesses which
character a skin belongs to based on filename keywords and aspect ratio.
"""
import os
import sys
import struct

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from mvc2_extract.sprites import ImgDat
from mvc2_extract.renderer import render_sprite, render_composite
from mvc2_extract.characters import CHARACTERS, safe_name, palette_rows

DEFAULT_IMGDAT = r"C:\Program Files (x86)\PalMod\img2020.dat"
SKINS_ROOT = r"D:\Storage\MvC2Modding\MvC2_Skins"

# Map from folder name -> list of (character_id, keywords_for_disambiguation)
# Most folders map to exactly one character.
# Combined folders need keywords to disambiguate.
FOLDER_TO_CHARS = {}

# Build the simple 1:1 mappings from CHARACTERS dict
_SAFE_TO_CID = {}
for cid, name in CHARACTERS.items():
    if cid in (0x18, 0x19, 0x1A):
        continue
    sn = safe_name(name)
    _SAFE_TO_CID[sn] = cid

# Folder names in the skins directory use slightly different conventions
FOLDER_MAP = {
    # Direct matches (folder name -> character ID)
    "Akuma": [0x1E],
    "Amingo": [0x15],
    "Anakaris": [0x04],
    "BB_Hood": [0x1F],
    "Blackheart": [0x35],
    "Cable": [0x17],
    "Cammy": [0x24],
    "Captain_America": [0x0B],
    "Captain_Commando": [0x38],
    "Charlie_Nash": [0x21],
    "Chun-Li": [0x1B],
    "Colossus": [0x32],
    "Cyclops": [0x06],
    "Dan": [0x23],
    "Dhalsim": [0x25],
    "Doctor_Doom": [0x0F],
    "Felicia": [0x20],
    "Gambit": [0x28],
    "Guile": [0x02],
    "Hayato": [0x12],
    "Hulk": [0x0D],
    "Iceman": [0x09],
    "Jill": [0x11],
    "Jin": [0x37],
    "Juggernaut": [0x29],
    "Ken": [0x27],
    "M_Bison": [0x26],
    "Magneto": [0x2C],
    "Marrow": [0x16],
    "Mega_Man": [0x1C],
    "Morrigan": [0x03],
    "Omega_Red": [0x30],
    "Psylocke": [0x08],
    "Rogue": [0x0A],
    "Roll": [0x1D],
    "Ruby_Heart": [0x13],
    "Ryu": [0x00],
    "Sabretooth": [0x2B],
    "Sakura": [0x22],
    "Sentinel": [0x34],
    "Servbot": [0x3A],
    "Shuma-Gorath": [0x2D],
    "Silver_Samurai": [0x2F],
    "SonSon": [0x14],
    "Spider-Man": [0x0C],
    "Spiral": [0x31],
    "Storm": [0x2A],
    "Strider": [0x05],
    "Thanos": [0x36],
    "Tron_Bonne": [0x10],
    "Venom": [0x0E],
    "Zangief": [0x01],
    # Combined folders
    "Iron_Men": [0x33, 0x2E],      # Iron Man + War Machine
    "Wolverines": [0x07, 0x39],     # Wolverine + Wolverine (Bone Claw)
}

# Keywords to disambiguate combined folders
WAR_MACHINE_KEYWORDS = ["war", "warmachine", "wm_", "warm"]
BONE_CLAW_KEYWORDS = ["bone", "boneclaw", "bonerine", "bc_", "bone_claw"]


def guess_character_id(folder_name, filename, char_ids, existing_size, imgdat):
    """For combined folders, guess which character a skin belongs to."""
    if len(char_ids) == 1:
        return char_ids[0]

    fname_lower = filename.lower()

    if folder_name == "Iron_Men":
        # Check for War Machine keywords first
        for kw in WAR_MACHINE_KEYWORDS:
            if kw in fname_lower:
                return 0x2E  # War Machine
        # Check if existing size exactly matches a known canonical
        # Iron Man = 645x233, War Machine = 633x233
        if existing_size:
            w, h = existing_size
            if (w, h) == (633, 233):
                return 0x2E  # Exact War Machine size
            if (w, h) == (645, 233):
                return 0x33  # Exact Iron Man size
            # Check for clean multiples of canonical sizes
            for scale in [2, 3, 4, 6]:
                if (w, h) == (633 * scale, 233 * scale):
                    return 0x2E
                if (w, h) == (645 * scale, 233 * scale):
                    return 0x33
        # Default: combo sheets and unknowns -> Iron Man
        return 0x33

    elif folder_name == "Wolverines":
        for kw in BONE_CLAW_KEYWORDS:
            if kw in fname_lower:
                return 0x39  # Bone Claw
        if existing_size:
            w, h = existing_size
            wol_sprite = imgdat.get_sprite(0x07, img_id=0)
            bc_sprite = imgdat.get_sprite(0x39, img_id=0)
            if wol_sprite and bc_sprite:
                wol_ar = wol_sprite[1] / wol_sprite[2]
                bc_ar = bc_sprite[1] / bc_sprite[2]
                skin_ar = w / h
                if abs(skin_ar - bc_ar) < abs(skin_ar - wol_ar):
                    return 0x39
        return 0x07  # Default to regular Wolverine

    return char_ids[0]


def extract_palette_from_png(filepath):
    """Extract the 16-color RGBA palette from an existing skin PNG.

    Works with both indexed-color (mode P) and RGBA/RGB images.
    For indexed images, reads the palette directly.
    For RGBA images, extracts unique colors from pixel data.
    """
    img = Image.open(filepath)

    if img.mode == "P":
        # Get the raw palette (768 bytes = 256 RGB entries)
        raw_palette = img.getpalette()
        if not raw_palette:
            return None

        # Get transparency info
        transparency = img.info.get("transparency", None)

        # Find how many colors are actually used
        pixels = list(img.getdata())
        used_indices = sorted(set(pixels))

        palette = []
        for i in range(min(16, max(used_indices) + 1 if used_indices else 16)):
            r = raw_palette[i * 3]
            g = raw_palette[i * 3 + 1]
            b = raw_palette[i * 3 + 2]
            # Index 0 is always transparent
            a = 0 if i == 0 else 255
            if isinstance(transparency, int) and i == transparency:
                a = 0
            palette.append((r, g, b, a))

        # Pad to 16 if needed
        while len(palette) < 16:
            palette.append((0, 0, 0, 0))

        return palette

    elif img.mode in ("RGBA", "RGB"):
        # Convert to RGBA if needed
        if img.mode == "RGB":
            img = img.convert("RGBA")

        # Get unique colors (up to 16)
        pixels = list(img.getdata())
        unique = []
        seen = set()
        for p in pixels:
            if p not in seen and len(unique) < 256:
                seen.add(p)
                unique.append(p)

        # First color should be the transparent/background one
        palette = unique[:16]
        # Ensure index 0 has alpha 0
        if palette:
            r, g, b, a = palette[0]
            palette[0] = (r, g, b, 0)

        while len(palette) < 16:
            palette.append((0, 0, 0, 0))

        return palette

    return None


def main():
    skins_root = sys.argv[1] if len(sys.argv) > 1 else SKINS_ROOT

    print("=" * 60)
    print("MvC2 Skin Standardizer")
    print("=" * 60)
    print(f"Skins: {skins_root}")
    print()

    # Load sprite database
    print("Loading sprite database...")
    imgdat = ImgDat(DEFAULT_IMGDAT)

    # Cache base sprites
    base_sprites = {}
    for cid in CHARACTERS:
        if cid in (0x18, 0x19, 0x1A):
            continue
        sprite = imgdat.get_sprite(cid, img_id=0)
        if sprite:
            base_sprites[cid] = sprite

    # Load composite base sprites for multi-row characters
    composite_bases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "composite_bases")
    composite_bases = {}
    if os.path.isdir(composite_bases_dir):
        for cid in CHARACTERS:
            if cid in (0x18, 0x19, 0x1A):
                continue
            num_rows = palette_rows(cid)
            if num_rows <= 1:
                continue
            sname = safe_name(CHARACTERS[cid])
            npz_path = os.path.join(composite_bases_dir, f"{sname}.npz")
            if os.path.isfile(npz_path):
                data = np.load(npz_path)
                composite_bases[cid] = (
                    data["pixels"],
                    int(data["width"]),
                    int(data["height"]),
                    int(data["num_rows"]),
                    data["default_palette"],
                )
        print(f"  Loaded {len(composite_bases)} composite bases for multi-row characters")

    standardized = 0
    skipped = 0
    errors = []

    for folder_name in sorted(os.listdir(skins_root)):
        folder_path = os.path.join(skins_root, folder_name)
        if not os.path.isdir(folder_path):
            continue
        if folder_name.endswith("_BACKUP") or folder_name == "Mixes":
            continue
        if folder_name not in FOLDER_MAP:
            continue

        char_ids = FOLDER_MAP[folder_name]
        pngs = [f for f in os.listdir(folder_path) if f.lower().endswith(".png")]
        if not pngs:
            continue

        # Get the canonical sizes for characters in this folder
        canonical = {}
        for cid in char_ids:
            if cid in base_sprites:
                _, w, h = base_sprites[cid]
                canonical[cid] = (w, h)

        folder_standardized = 0
        folder_already_ok = 0

        for fname in pngs:
            fpath = os.path.join(folder_path, fname)
            try:
                existing_img = Image.open(fpath)
                existing_size = existing_img.size
                existing_mode = existing_img.mode

                # Determine which character this skin belongs to
                cid = guess_character_id(folder_name, fname, char_ids, existing_size, imgdat)

                if cid not in base_sprites:
                    errors.append((folder_name, fname, "no base sprite"))
                    continue

                target_size = canonical.get(cid)
                if not target_size:
                    errors.append((folder_name, fname, "no canonical size"))
                    continue

                # Determine if this character uses composite rendering
                num_rows = palette_rows(cid)
                use_composite = (num_rows > 1 and cid in composite_bases)

                if use_composite:
                    base_pixels, cw, ch, _, default_pal = composite_bases[cid]
                    composite_size = (cw, ch)
                else:
                    composite_size = None

                # Check if already correct format and size
                expected_size = composite_size if use_composite else target_size
                if existing_mode == "P" and existing_size == expected_size:
                    # For composite chars, also check if the image has multi-row data
                    if use_composite:
                        pix_data = existing_img.tobytes()
                        max_idx = max(pix_data) if pix_data else 0
                        if max_idx >= 16:
                            # Already has multi-row data — already OK
                            folder_already_ok += 1
                            continue
                        # Single-row on composite base → needs re-rendering with default accessories
                    else:
                        folder_already_ok += 1
                        continue

                # Extract palette from existing image
                palette = extract_palette_from_png(fpath)
                if not palette:
                    errors.append((folder_name, fname, "could not extract palette"))
                    continue

                if use_composite:
                    # Re-render with composite base sprite
                    # The original skin only has body colors (row 0),
                    # so use default palette for accessory rows
                    filled = [palette]  # Row 0 = body from the skin
                    for row_i in range(1, num_rows):
                        start = row_i * 16
                        row_pal = []
                        for ci in range(16):
                            idx = start + ci
                            if idx < len(default_pal):
                                r, g, b = int(default_pal[idx][0]), int(default_pal[idx][1]), int(default_pal[idx][2])
                                a = 0 if ci == 0 else 255
                                row_pal.append((r, g, b, a))
                            else:
                                row_pal.append((0, 0, 0, 0))
                        filled.append(row_pal)

                    new_img = render_composite(base_pixels, cw, ch, filled, num_rows, default_pal)
                else:
                    # Re-render on canonical base sprite (single-row)
                    pixels, w, h = base_sprites[cid]
                    new_img = render_sprite(pixels, w, h, palette)

                new_img.save(fpath)
                folder_standardized += 1
                standardized += 1

            except Exception as e:
                errors.append((folder_name, fname, str(e)))

        total_in_folder = len(pngs)
        if folder_standardized > 0:
            print(f"  {folder_name:<25} {folder_standardized}/{total_in_folder} re-rendered"
                  f" ({folder_already_ok} already OK)")
        elif folder_already_ok == total_in_folder:
            print(f"  {folder_name:<25} all {total_in_folder} already correct")
        else:
            print(f"  {folder_name:<25} {total_in_folder} files ({folder_already_ok} OK, "
                  f"{total_in_folder - folder_already_ok} other)")

    print(f"\n{'=' * 60}")
    print(f"Done! Standardized {standardized} skins")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for folder, fname, reason in errors:
            print(f"  {folder}/{fname}: {reason}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
MvC2 Skin Processor — Unified tool for converting skins to standardized sprite sheets.

Accepts Dreamcast CDI disc images, PS3 PKG packages, individual PNG skin images,
or folders containing any mix of the above.

Usage:
    python mvc2_skin_processor.py <input>              # CDI, PKG, PNG, or folder
    python mvc2_skin_processor.py skin.png             # auto-detect character
    python mvc2_skin_processor.py skin.png -c Venom    # force character
    python mvc2_skin_processor.py mix.cdi -o my_out    # custom output dir

Output: output/<CharName>/<CharName>_<hash>_<descriptor>.png
"""
import argparse
import hashlib
import os
import re
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mvc2_extract.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS,
    safe_name, palette_rows, palette_slot_map,
)
from mvc2_extract.palettes import extract_palette_files, parse_palettes
from mvc2_extract.renderer import render_sprite, render_composite
from mvc2_extract.cdi import parse_cdi

SPRITE_BASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprite_bases")


# ── Sprite base loading ───────────────────────────────────────────────────────

def load_sprite_bases():
    """Load all 56 bundled base sprites. Returns dict[char_id] -> sprite data."""
    bases = {}
    name_to_cid = {}
    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        name_to_cid[sname] = cid
        npz_path = os.path.join(SPRITE_BASES_DIR, f"{sname}.npz")
        if not os.path.exists(npz_path):
            continue
        base = np.load(npz_path)
        bases[cid] = {
            'pixels': base['pixels'],
            'width': int(base['width']),
            'height': int(base['height']),
            'num_rows': int(base['num_rows']),
            'default_palette': base['default_palette'],
        }
    return bases


def build_dimension_lookup(bases):
    """Build (w, h) -> char_id lookup for auto-detection."""
    dim_to_cid = {}
    for cid, b in bases.items():
        dim_to_cid[(b['width'], b['height'])] = cid
    return dim_to_cid


# ── Naming helpers ────────────────────────────────────────────────────────────

def get_palette_hash(img):
    """SHA256 first 8 hex chars of the palette rows used by the image."""
    pal = img.getpalette()
    if not pal:
        return "00000000"
    pixels = img.tobytes()
    max_idx = max(pixels) if pixels else 0
    num_rows = (max_idx // 16) + 1
    pal_bytes = bytes(pal[:num_rows * 48])
    return hashlib.sha256(pal_bytes).hexdigest()[:8]


def make_descriptor(filename):
    """Build a clean descriptor from an input filename."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    # Replace underscores and spaces with hyphens
    desc = stem.replace('_', '-').replace(' ', '-')
    # Remove parentheses
    desc = desc.replace('(', '').replace(')', '')
    # Collapse multiple hyphens
    desc = re.sub(r'-+', '-', desc).strip('-')
    # Truncate to 30 chars at word boundary
    if len(desc) > 30:
        truncated = desc[:30]
        last_sep = truncated.rfind('-')
        if last_sep > 15:
            truncated = truncated[:last_sep]
        desc = truncated
    return desc


def build_output_name(char_name, pal_hash, descriptor):
    """Build standardized output filename."""
    parts = [char_name, pal_hash]
    if descriptor:
        parts.append(descriptor)
    return "_".join(parts) + ".png"


# ── Character detection ───────────────────────────────────────────────────────

def detect_character(img_w, img_h, dim_lookup, bases):
    """Detect character from image dimensions.

    Returns (char_id, scale_factor) or (None, None) with suggestions printed.
    """
    # Exact match
    if (img_w, img_h) in dim_lookup:
        return dim_lookup[(img_w, img_h)], 1

    # Integer downscale match
    for scale in [2, 3, 4, 6, 8]:
        if img_w % scale == 0 and img_h % scale == 0:
            base_w, base_h = img_w // scale, img_h // scale
            if (base_w, base_h) in dim_lookup:
                return dim_lookup[(base_w, base_h)], scale

    # No match — find closest by aspect ratio
    ar = img_w / img_h
    suggestions = []
    for cid, b in bases.items():
        bw, bh = b['width'], b['height']
        bar = bw / bh
        if abs(ar - bar) < 0.05:
            xscale = img_w / bw
            suggestions.append((cid, CHARACTERS[cid], bw, bh, xscale))

    if suggestions:
        print(f"  Could not auto-detect character for {img_w}x{img_h}.")
        print(f"  Closest matches by aspect ratio:")
        for cid, cname, bw, bh, sc in sorted(suggestions, key=lambda x: abs(x[4] - round(x[4]))):
            print(f"    {cname:<25} {bw}x{bh}  (scale ~{sc:.2f}x)")
        print(f"  Use --character to specify manually.")
    else:
        print(f"  Could not auto-detect character for {img_w}x{img_h}. No aspect ratio matches.")
        print(f"  Use --character to specify manually.")

    return None, None


def resolve_character_name(name):
    """Resolve a user-provided character name to a char_id."""
    name_lower = name.lower().replace(' ', '').replace('_', '').replace('-', '').replace('.', '')
    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        cname_lower = cname.lower().replace(' ', '').replace('_', '').replace('-', '').replace('.', '')
        if name_lower == cname_lower:
            return cid
        sname_lower = safe_name(cname).lower()
        if name_lower == sname_lower:
            return cid
    # Partial match
    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        cname_lower = cname.lower().replace(' ', '').replace('_', '').replace('-', '').replace('.', '')
        if name_lower in cname_lower or cname_lower in name_lower:
            return cid
    return None


# ── Core rendering ────────────────────────────────────────────────────────────

def render_character(cid, palettes, base, suffix=""):
    """Render all 6 button colors for a character.

    Returns list of (filename_stem, PIL.Image) tuples.
    """
    num_rows = base['num_rows']
    slot_map = palette_slot_map(cid)
    sname = safe_name(CHARACTERS[cid])
    results = []

    for bi, btn in enumerate(BUTTON_NAMES):
        pal_base = bi * 8
        if pal_base >= len(palettes):
            continue

        if num_rows > 1:
            # Composite rendering
            button_palettes = []
            for row in range(num_rows):
                slot_offset = slot_map[row] if row < len(slot_map) else row
                pal_idx = pal_base + slot_offset
                if pal_idx < len(palettes):
                    button_palettes.append(palettes[pal_idx])
                else:
                    button_palettes.append(None)

            # Fill missing rows from default palette
            filled = []
            default_pal = base['default_palette']
            for row_i, pal in enumerate(button_palettes):
                if pal is not None:
                    filled.append(pal)
                elif default_pal is not None:
                    start = row_i * 16
                    row_pal = []
                    for ci in range(16):
                        idx = start + ci
                        if idx < len(default_pal):
                            r, g, b = int(default_pal[idx][0]), int(default_pal[idx][1]), int(default_pal[idx][2])
                            a = 0 if ci == 0 and row_i == 0 else 255
                            row_pal.append((r, g, b, a))
                        else:
                            row_pal.append((0, 0, 0, 0))
                    filled.append(row_pal)
                else:
                    filled.append([(0, 0, 0, 0)] * 16)

            img = render_composite(
                base['pixels'], base['width'], base['height'],
                filled, num_rows, default_pal
            )
        else:
            # Single-row rendering
            img = render_sprite(
                base['pixels'].tobytes(), base['width'], base['height'],
                palettes[pal_base]
            )

        results.append((btn, img))

    return results


# ── Input processors ──────────────────────────────────────────────────────────

def process_cdi(cdi_path, bases, out_dir):
    """Process a Dreamcast CDI disc image."""
    descriptor = make_descriptor(os.path.basename(cdi_path))
    print(f"  Parsing CDI...")

    iso_data = parse_cdi(cdi_path, quiet=True)
    pal_data = extract_palette_files(iso_data, quiet=True)
    print(f"  Found {len(pal_data)} character palette files")

    rendered = 0
    for cid in PLAYABLE_CHARS:
        if cid not in pal_data or cid not in bases:
            continue

        try:
            palettes = parse_palettes(pal_data[cid])
        except Exception:
            continue

        if not palettes:
            continue

        base = bases[cid]
        cname = safe_name(CHARACTERS[cid])
        char_dir = os.path.join(out_dir, cname)
        os.makedirs(char_dir, exist_ok=True)

        button_imgs = render_character(cid, palettes, base)
        for btn, img in button_imgs:
            pal_hash = get_palette_hash(img)
            btn_desc = f"{descriptor}-{btn}" if descriptor else btn
            fname = build_output_name(cname, pal_hash, btn_desc)
            img.save(os.path.join(char_dir, fname))
            rendered += 1

    return rendered


def process_pkg(pkg_path, bases, out_dir):
    """Process a PS3 PKG package."""
    from ps3_pkg_extract import PKGExtractor

    descriptor = make_descriptor(os.path.basename(pkg_path))
    print(f"  Extracting PS3 PKG...")

    pkg = PKGExtractor(pkg_path)

    with tempfile.TemporaryDirectory() as tmp:
        pal_files = pkg.extract_palette_files(tmp, verbose=False)
        if not pal_files:
            print(f"  No palette files found in PKG")
            return 0

        # Parse palette files into pal_data dict
        pal_data = {}
        for pf in pal_files:
            fname = os.path.basename(pf).upper()
            # Match PL??_DAT.BIN or PL??PAK.BIN
            m = re.match(r'^PL([0-9A-F]{2})', fname)
            if m:
                cid = int(m.group(1), 16)
                with open(pf, 'rb') as f:
                    pal_data[cid] = f.read()

        print(f"  Found {len(pal_data)} character palette files")

        rendered = 0
        for cid in PLAYABLE_CHARS:
            if cid not in pal_data or cid not in bases:
                continue

            try:
                palettes = parse_palettes(pal_data[cid])
            except Exception:
                continue

            if not palettes:
                continue

            base = bases[cid]
            cname = safe_name(CHARACTERS[cid])
            char_dir = os.path.join(out_dir, cname)
            os.makedirs(char_dir, exist_ok=True)

            button_imgs = render_character(cid, palettes, base)
            for btn, img in button_imgs:
                pal_hash = get_palette_hash(img)
                btn_desc = f"{descriptor}-{btn}" if descriptor else btn
                fname = build_output_name(cname, pal_hash, btn_desc)
                img.save(os.path.join(char_dir, fname))
                rendered += 1

        return rendered


def process_image(png_path, bases, dim_lookup, out_dir, force_character=None):
    """Process an individual PNG skin image."""
    descriptor = make_descriptor(os.path.basename(png_path))

    img = Image.open(png_path)
    if img.mode != 'P':
        print(f"  WARNING: {png_path} is not palette-indexed (mode={img.mode}), skipping")
        img.close()
        return 0

    img_w, img_h = img.size

    # Detect or force character
    if force_character:
        cid = resolve_character_name(force_character)
        if cid is None:
            print(f"  ERROR: Unknown character '{force_character}'")
            img.close()
            return 0
        scale = 1  # doesn't matter, we use the palette not pixels
        print(f"  Forced character: {CHARACTERS[cid]}")
    else:
        cid, scale = detect_character(img_w, img_h, dim_lookup, bases)
        if cid is None:
            img.close()
            return 0
        if scale > 1:
            print(f"  Detected: {CHARACTERS[cid]} ({scale}x scale)")
        else:
            print(f"  Detected: {CHARACTERS[cid]}")

    if cid not in bases:
        print(f"  ERROR: No sprite base for {CHARACTERS[cid]}")
        img.close()
        return 0

    base = bases[cid]
    num_rows = base['num_rows']
    cname = safe_name(CHARACTERS[cid])

    # Extract palette from the input PNG
    pal = img.getpalette()
    if not pal:
        print(f"  ERROR: No palette in {png_path}")
        img.close()
        return 0

    pixels = img.tobytes()
    max_idx = max(pixels) if pixels else 0
    img.close()

    # Build palette rows from the input image
    # Row 0 (body) always comes from the input
    body_pal = []
    for i in range(16):
        r, g, b = pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]
        a = 0 if i == 0 else 255
        body_pal.append((r, g, b, a))

    if max_idx >= 16 and num_rows > 1:
        # Image has multi-row data — extract all provided rows
        input_rows = (max_idx // 16) + 1
        all_rows = []
        for row in range(min(input_rows, num_rows)):
            row_pal = []
            for ci in range(16):
                idx = row * 16 + ci
                r, g, b = pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]
                a = 0 if ci == 0 and row == 0 else 255
                row_pal.append((r, g, b, a))
            all_rows.append(row_pal)

        # Fill any missing rows from default
        default_pal = base['default_palette']
        while len(all_rows) < num_rows:
            row_i = len(all_rows)
            row_pal = []
            for ci in range(16):
                idx = row_i * 16 + ci
                if idx < len(default_pal):
                    r, g, b = int(default_pal[idx][0]), int(default_pal[idx][1]), int(default_pal[idx][2])
                    a = 0 if ci == 0 and row_i == 0 else 255
                    row_pal.append((r, g, b, a))
                else:
                    row_pal.append((0, 0, 0, 0))
            all_rows.append(row_pal)

        # Render composite
        result_img = render_composite(
            base['pixels'], base['width'], base['height'],
            all_rows, num_rows, default_pal
        )
    elif num_rows > 1:
        # Single-row input for a multi-row character — use body + defaults
        default_pal = base['default_palette']
        all_rows = [body_pal]
        for row_i in range(1, num_rows):
            row_pal = []
            for ci in range(16):
                idx = row_i * 16 + ci
                if idx < len(default_pal):
                    r, g, b = int(default_pal[idx][0]), int(default_pal[idx][1]), int(default_pal[idx][2])
                    a = 255
                    row_pal.append((r, g, b, a))
                else:
                    row_pal.append((0, 0, 0, 0))
            all_rows.append(row_pal)

        result_img = render_composite(
            base['pixels'], base['width'], base['height'],
            all_rows, num_rows, default_pal
        )
    else:
        # Single-row character, single-row image — simple render
        result_img = render_sprite(
            base['pixels'].tobytes(), base['width'], base['height'],
            body_pal
        )

    # Save
    char_dir = os.path.join(out_dir, cname)
    os.makedirs(char_dir, exist_ok=True)

    pal_hash = get_palette_hash(result_img)
    fname = build_output_name(cname, pal_hash, descriptor)
    result_img.save(os.path.join(char_dir, fname))

    return 1


# ── Main entry point ──────────────────────────────────────────────────────────

def process_input(input_path, bases, dim_lookup, out_dir, force_character=None):
    """Route input to the appropriate processor."""
    if os.path.isfile(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        name = os.path.basename(input_path)
        print(f"\n[{name}]")

        if ext == '.cdi':
            return process_cdi(input_path, bases, out_dir)
        elif ext == '.pkg':
            return process_pkg(input_path, bases, out_dir)
        elif ext == '.png':
            return process_image(input_path, bases, dim_lookup, out_dir, force_character)
        else:
            print(f"  Unsupported file type: {ext}")
            return 0

    elif os.path.isdir(input_path):
        total = 0
        for item in sorted(os.listdir(input_path)):
            item_path = os.path.join(input_path, item)
            if os.path.isfile(item_path):
                ext = os.path.splitext(item)[1].lower()
                if ext in ('.cdi', '.pkg', '.png'):
                    total += process_input(item_path, bases, dim_lookup, out_dir, force_character)
            elif os.path.isdir(item_path):
                # Recurse into subdirectories
                total += process_input(item_path, bases, dim_lookup, out_dir, force_character)
        return total

    else:
        print(f"ERROR: {input_path} not found")
        return 0


def main():
    parser = argparse.ArgumentParser(
        description="MvC2 Skin Processor — Convert CDI, PKG, or PNG skins to standardized sprites",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s mix.cdi                    Process a Dreamcast disc image
  %(prog)s colors.pkg                 Process a PS3 package
  %(prog)s skin.png                   Process a single skin (auto-detect character)
  %(prog)s skin.png -c Venom          Process with forced character
  %(prog)s ./my_skins/                Process all files in a folder
  %(prog)s mix.cdi -o ./my_output     Custom output directory
""")
    parser.add_argument("input", help="CDI file, PKG file, PNG image, or folder")
    parser.add_argument("-o", "--output", default="./output", help="Output directory (default: ./output)")
    parser.add_argument("-c", "--character", help="Force character for PNG input (e.g. 'Venom', 'Storm')")
    args = parser.parse_args()

    print("=" * 60)
    print("MvC2 Skin Processor")
    print("=" * 60)

    # Load sprite bases
    print("Loading sprite bases...")
    bases = load_sprite_bases()
    dim_lookup = build_dimension_lookup(bases)
    print(f"  {len(bases)} characters loaded")

    out_dir = os.path.abspath(args.output)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Output: {out_dir}")

    # Process input
    rendered = process_input(
        os.path.abspath(args.input), bases, dim_lookup, out_dir, args.character
    )

    print(f"\n{'=' * 60}")
    print(f"Done! {rendered} skins rendered to {out_dir}")


if __name__ == "__main__":
    main()

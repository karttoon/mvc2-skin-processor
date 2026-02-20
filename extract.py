#!/usr/bin/env python3
"""
MvC2 Skin Extractor — Extract character skin palettes from Dreamcast disc images.

Usage:
    python extract.py <input.cdi>                  Extract single CDI
    python extract.py <mix_directory>               Auto-find CDI in directory
    python extract.py <input.cdi> --prefix MixName  Prefix output filenames
    python extract.py <input.cdi> -o /path/to/out   Custom output directory

Output: Indexed-color PNGs (mode P, type 3) compatible with PalMod import.
Each character gets 6 files (LP, LK, HP, HK, A1, A2).
"""
import argparse
import os
import sys
import glob

import numpy as np

from mvc2_extract.cdi import parse_cdi
from mvc2_extract.palettes import extract_palette_files, parse_palettes
from mvc2_extract.sprites import ImgDat
from mvc2_extract.renderer import render_sprite, render_composite
from mvc2_extract.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS, safe_name, palette_rows,
    palette_slot_map,
)

# Default path to PalMod's sprite database
DEFAULT_IMGDAT = r"C:\Program Files (x86)\PalMod\img2020.dat"

# Composite base sprites directory (extracted from zachd.com Default skins)
COMPOSITE_BASES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "composite_bases")


def load_composite_bases():
    """Load composite base sprites for multi-row characters.

    Returns:
        dict: char_id -> (pixels_2d, w, h, num_rows, default_palette)
    """
    bases = {}
    if not os.path.isdir(COMPOSITE_BASES_DIR):
        return bases

    for cid in PLAYABLE_CHARS:
        num_rows = palette_rows(cid)
        if num_rows <= 1:
            continue
        sname = safe_name(CHARACTERS[cid])
        npz_path = os.path.join(COMPOSITE_BASES_DIR, f"{sname}.npz")
        if os.path.isfile(npz_path):
            data = np.load(npz_path)
            bases[cid] = (
                data["pixels"],
                int(data["width"]),
                int(data["height"]),
                int(data["num_rows"]),
                data["default_palette"],
            )
    return bases


def find_cdi(input_path):
    """Resolve input to a CDI file path and mix directory."""
    if os.path.isdir(input_path):
        cdis = glob.glob(os.path.join(input_path, "*.cdi"))
        if not cdis:
            cdis = glob.glob(os.path.join(input_path, "**/*.cdi"), recursive=True)
        if not cdis:
            print(f"Error: No .cdi file found in {input_path}")
            sys.exit(1)
        return cdis[0], input_path
    else:
        return input_path, os.path.dirname(input_path) or "."


def extract_mix(cdi_path, out_dir, imgdat_path=DEFAULT_IMGDAT, suffix=None, quiet=False,
                composite_bases=None):
    """Full pipeline: CDI -> palette extraction -> sprite rendering -> PNGs.

    Args:
        cdi_path: Path to CDI disc image.
        out_dir: Output directory for rendered PNGs.
        imgdat_path: Path to PalMod's img2020.dat.
        suffix: Optional suffix for output filenames (e.g. mix name).
        quiet: If True, suppress progress output.
        composite_bases: Optional pre-loaded composite base data (from load_composite_bases).

    Returns:
        tuple: (rendered_count, error_list)
    """
    def log(msg):
        if not quiet:
            print(msg)

    # Step 1: Parse CDI
    log("[1/4] Parsing CDI disc image...")
    iso_data = parse_cdi(cdi_path, quiet=quiet)

    # Step 2: Extract palette files
    log("\n[2/4] Extracting palette files...")
    pal_data = extract_palette_files(iso_data, quiet=quiet)
    del iso_data  # free ~900MB

    # Step 3: Load sprite database
    log("\n[3/4] Loading sprite database...")
    imgdat = ImgDat(imgdat_path)
    total = sum(len(v) for v in imgdat.sprites.values())
    log(f"  {total} sprites indexed")

    # Load composite bases if not provided
    if composite_bases is None:
        composite_bases = load_composite_bases()

    # Step 4: Render all characters
    log("\n[4/4] Rendering sprites...")
    os.makedirs(out_dir, exist_ok=True)

    rendered = 0
    errors = []

    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        num_rows = palette_rows(cid)

        if cid not in pal_data:
            errors.append((cname, "no palette file"))
            continue

        palettes = parse_palettes(pal_data[cid])

        # Determine rendering approach
        use_composite = (num_rows > 1 and cid in composite_bases)

        if use_composite:
            base_pixels, bw, bh, _, default_pal = composite_bases[cid]
            w, h = bw, bh
        else:
            sprite = imgdat.get_sprite(cid, img_id=0)
            if not sprite:
                errors.append((cname, "no sprite in img2020.dat"))
                continue
            pixels, w, h = sprite

        char_dir = os.path.join(out_dir, sname)
        os.makedirs(char_dir, exist_ok=True)

        for bi, btn in enumerate(BUTTON_NAMES):
            pal_base = bi * 8
            if pal_base >= len(palettes):
                continue

            if use_composite:
                # Gather multi-row palettes for this button using PalMod slot map
                slot_map = palette_slot_map(cid)
                button_palettes = []
                for row in range(num_rows):
                    slot_offset = slot_map[row] if row < len(slot_map) else row
                    pal_idx = pal_base + slot_offset
                    if pal_idx < len(palettes):
                        button_palettes.append(palettes[pal_idx])
                    else:
                        # Fallback: use default palette for this row
                        button_palettes.append(None)

                # Fill in missing rows from default palette
                filled = []
                for row_i, pal in enumerate(button_palettes):
                    if pal is not None:
                        filled.append(pal)
                    elif default_pal is not None:
                        # Build RGBA tuples from default RGB
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

                img = render_composite(base_pixels, w, h, filled, num_rows, default_pal)
            else:
                img = render_sprite(pixels, w, h, palettes[pal_base])

            if suffix:
                filename = f"{sname}_{btn}_{suffix}.png"
            else:
                filename = f"{sname}_{btn}.png"
            img.save(os.path.join(char_dir, filename))
            rendered += 1

        if use_composite:
            log(f"  {cname:<25} {w}x{h}  [6 colors x {num_rows} rows]")
        else:
            log(f"  {cname:<25} {w}x{h}  [6 colors]")

    return rendered, errors


def main():
    parser = argparse.ArgumentParser(
        description="MvC2 Skin Extractor — CDI disc image to character sprite PNGs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python extract.py "MVC2 Kest 2025"
  python extract.py game.cdi --suffix Kest2025
  python extract.py game.cdi -o ./output --imgdat /path/to/img2020.dat
        """,
    )
    parser.add_argument("input", help="CDI file or directory containing one")
    parser.add_argument("-o", "--output", help="Output directory (default: singles/ next to CDI)")
    parser.add_argument("--suffix", help="Suffix for output filenames (e.g. mix name)")
    parser.add_argument("--imgdat", default=DEFAULT_IMGDAT,
                        help=f"Path to PalMod img2020.dat (default: {DEFAULT_IMGDAT})")
    parser.add_argument("-q", "--quiet", action="store_true", help="Suppress progress output")
    args = parser.parse_args()

    cdi_path, mix_dir = find_cdi(args.input)
    mix_name = os.path.basename(mix_dir) or os.path.splitext(os.path.basename(cdi_path))[0]
    out_dir = args.output or os.path.join(mix_dir, "singles")

    if not args.quiet:
        print("=" * 60)
        print("MvC2 Skin Extractor")
        print("=" * 60)
        print(f"Mix:    {mix_name}")
        print(f"CDI:    {cdi_path}")
        print(f"Output: {out_dir}")
        if args.suffix:
            print(f"Suffix: {args.suffix}")
        print()

    if not os.path.isfile(args.imgdat):
        print(f"Error: img2020.dat not found at {args.imgdat}")
        print("Install PalMod or specify the path with --imgdat")
        sys.exit(1)

    rendered, errors = extract_mix(
        cdi_path, out_dir,
        imgdat_path=args.imgdat,
        suffix=args.suffix,
        quiet=args.quiet,
    )

    if not args.quiet:
        print(f"\n{'=' * 60}")
        print(f"Done! Rendered {rendered} sprites ({rendered // 6} characters x 6 colors)")
        print(f"Output: {out_dir}")
        if errors:
            print(f"\nSkipped ({len(errors)}):")
            for name, reason in errors:
                print(f"  {name}: {reason}")


if __name__ == "__main__":
    main()

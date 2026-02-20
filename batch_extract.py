#!/usr/bin/env python3
"""
Batch MvC2 Mix Extractor — Process multiple mixes, merge, and deduplicate.

Scans a directory tree for CDI files, extracts all character sprites from
each mix, merges into per-character folders, and removes duplicates.

Usage:
    python batch_extract.py <mixes_directory>
    python batch_extract.py <mixes_directory> -o ./merged_output
    python batch_extract.py <mixes_directory> --skip-merge
"""
import argparse
import hashlib
import os
import shutil
import sys
import glob

# Add parent directory to path so we can import mvc2_extract
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image

from mvc2_extract.cdi import parse_cdi
from mvc2_extract.palettes import extract_palette_files, parse_palettes
from mvc2_extract.sprites import ImgDat
from mvc2_extract.renderer import render_sprite
from mvc2_extract.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS, safe_name,
)

DEFAULT_IMGDAT = r"C:\Program Files (x86)\PalMod\img2020.dat"


def sanitize_mix_name(name):
    """Convert a mix folder name to a clean prefix."""
    # Remove common suffixes and clean up
    name = name.strip()
    # Replace problematic characters
    for ch in " .-()[]{}!@#$%^&+=,'":
        name = name.replace(ch, "_")
    # Collapse multiple underscores
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_")


def find_all_cdis(root_dir):
    """Find all CDI files under root_dir, return list of (cdi_path, mix_name)."""
    cdis = []
    for cdi_path in glob.glob(os.path.join(root_dir, "**/*.cdi"), recursive=True):
        # Derive mix name from the parent directory name
        parent = os.path.dirname(cdi_path)
        mix_name = os.path.basename(parent)
        # If CDI is directly in root, use the CDI filename
        if os.path.normpath(parent) == os.path.normpath(root_dir):
            mix_name = os.path.splitext(os.path.basename(cdi_path))[0]
        cdis.append((cdi_path, mix_name))
    return cdis


def extract_single_mix(cdi_path, mix_name, out_dir, imgdat, suffix=None):
    """Extract all sprites from a single CDI file.

    Returns:
        tuple: (rendered_count, errors_list, output_dir)
    """
    clean_suffix = suffix or sanitize_mix_name(mix_name)
    mix_out = os.path.join(out_dir, clean_suffix)

    print(f"\n{'-' * 60}")
    print(f"Mix: {mix_name}")
    print(f"CDI: {os.path.basename(cdi_path)}")
    print(f"Suffix: {clean_suffix}")
    print(f"{'-' * 60}")

    try:
        # Step 1: Parse CDI
        print("  Parsing CDI...")
        iso_data = parse_cdi(cdi_path, quiet=True)

        # Step 2: Extract palettes
        print("  Extracting palettes...")
        pal_data = extract_palette_files(iso_data, quiet=True)
        print(f"  Found {len(pal_data)} palette files")
        del iso_data

        # Step 3: Render
        print("  Rendering sprites...")
        os.makedirs(mix_out, exist_ok=True)
        rendered = 0
        errors = []

        for cid in PLAYABLE_CHARS:
            cname = CHARACTERS[cid]
            sname = safe_name(cname)

            if cid not in pal_data:
                errors.append((cname, "no palette file"))
                continue

            sprite = imgdat.get_sprite(cid, img_id=0)
            if not sprite:
                errors.append((cname, "no sprite in img2020.dat"))
                continue

            pixels, w, h = sprite
            palettes = parse_palettes(pal_data[cid])

            char_dir = os.path.join(mix_out, sname)
            os.makedirs(char_dir, exist_ok=True)

            for bi, btn in enumerate(BUTTON_NAMES):
                pal_idx = bi * 8
                if pal_idx >= len(palettes):
                    continue
                img = render_sprite(pixels, w, h, palettes[pal_idx])
                filename = f"{sname}_{btn}_{clean_suffix}.png"
                img.save(os.path.join(char_dir, filename))
                rendered += 1

        print(f"  OK: {rendered} sprites ({rendered // 6} characters)")
        if errors:
            print(f"  Skipped: {len(errors)} characters")
        return rendered, errors, mix_out

    except Exception as e:
        print(f"  ERROR: {e}")
        return 0, [(mix_name, str(e))], mix_out


def merge_into_characters(mix_dirs, merged_dir):
    """Copy all sprites from individual mix outputs into per-character folders.

    Returns:
        int: Total files copied.
    """
    print(f"\n{'=' * 60}")
    print("Merging into character folders...")
    print(f"{'=' * 60}")

    os.makedirs(merged_dir, exist_ok=True)
    total_copied = 0

    for mix_dir in mix_dirs:
        if not os.path.isdir(mix_dir):
            continue
        for char_folder in os.listdir(mix_dir):
            src_char = os.path.join(mix_dir, char_folder)
            if not os.path.isdir(src_char):
                continue
            dst_char = os.path.join(merged_dir, char_folder)
            os.makedirs(dst_char, exist_ok=True)
            for fname in os.listdir(src_char):
                if fname.lower().endswith(".png"):
                    src = os.path.join(src_char, fname)
                    dst = os.path.join(dst_char, fname)
                    shutil.copy2(src, dst)
                    total_copied += 1

    print(f"  Copied {total_copied} files into {merged_dir}")
    return total_copied


def deduplicate(merged_dir):
    """Remove duplicate files (identical content) within each character folder.

    Returns:
        tuple: (total_files, unique_files, duplicates_removed)
    """
    print(f"\n{'=' * 60}")
    print("Deduplicating...")
    print(f"{'=' * 60}")

    total = 0
    unique = 0
    removed = 0

    for char_folder in sorted(os.listdir(merged_dir)):
        char_dir = os.path.join(merged_dir, char_folder)
        if not os.path.isdir(char_dir):
            continue

        # Hash all files by image content (palette + pixels)
        hash_to_files = {}
        for fname in os.listdir(char_dir):
            fpath = os.path.join(char_dir, fname)
            if not fname.lower().endswith(".png"):
                continue
            total += 1
            img = Image.open(fpath)
            pal = img.getpalette() or []
            pixels = img.tobytes()
            content = bytes(pal[:48]) + pixels
            fhash = hashlib.sha256(content).hexdigest()
            hash_to_files.setdefault(fhash, []).append(fpath)

        # Keep one file per hash, remove the rest
        char_removed = 0
        for fhash, files in hash_to_files.items():
            unique += 1
            # Keep the first file alphabetically, remove the rest
            files.sort()
            for dup in files[1:]:
                os.remove(dup)
                char_removed += 1
                removed += 1

        if char_removed > 0:
            remaining = len(os.listdir(char_dir))
            print(f"  {char_folder}: removed {char_removed} dupes, {remaining} unique")

    print(f"\n  Total files:  {total}")
    print(f"  Unique files: {unique}")
    print(f"  Duplicates:   {removed}")
    return total, unique, removed


def main():
    parser = argparse.ArgumentParser(
        description="Batch extract MvC2 mixes, merge, and deduplicate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", help="Directory containing mix folders with CDI files")
    parser.add_argument("-o", "--output", help="Merged output directory (default: <input>/merged)")
    parser.add_argument("--imgdat", default=DEFAULT_IMGDAT,
                        help="Path to PalMod img2020.dat")
    parser.add_argument("--skip-merge", action="store_true",
                        help="Only extract, don't merge or deduplicate")
    parser.add_argument("--skip-extract", action="store_true",
                        help="Skip extraction, only merge and deduplicate existing outputs")
    args = parser.parse_args()

    root = args.input
    merged_dir = args.output or os.path.join(root, "merged")
    extract_out = os.path.join(root, "_extracted")

    if not os.path.isfile(args.imgdat):
        print(f"Error: img2020.dat not found at {args.imgdat}")
        sys.exit(1)

    mix_dirs = []

    if not args.skip_extract:
        # Find all CDI files
        cdis = find_all_cdis(root)
        if not cdis:
            print(f"No CDI files found under {root}")
            sys.exit(1)

        print(f"{'=' * 60}")
        print(f"MvC2 Batch Extractor")
        print(f"{'=' * 60}")
        print(f"Found {len(cdis)} CDI file(s):")
        for cdi_path, mix_name in cdis:
            print(f"  {mix_name}: {os.path.basename(cdi_path)}")
        print(f"Output: {extract_out}")
        print(f"Merged: {merged_dir}")

        # Load sprite database once
        print(f"\nLoading sprite database...")
        imgdat = ImgDat(args.imgdat)
        total_sprites = sum(len(v) for v in imgdat.sprites.values())
        print(f"  {total_sprites} sprites indexed")

        # Extract each mix
        total_rendered = 0
        all_errors = []

        for cdi_path, mix_name in cdis:
            rendered, errors, mix_out = extract_single_mix(
                cdi_path, mix_name, extract_out, imgdat
            )
            total_rendered += rendered
            all_errors.extend(errors)
            mix_dirs.append(mix_out)

        print(f"\n{'=' * 60}")
        print(f"Extraction complete: {total_rendered} sprites from {len(cdis)} mixes")
        if all_errors:
            print(f"Total errors/skips: {len(all_errors)}")
    else:
        # Gather existing extracted mix directories
        if os.path.isdir(extract_out):
            for d in os.listdir(extract_out):
                dp = os.path.join(extract_out, d)
                if os.path.isdir(dp):
                    mix_dirs.append(dp)
        print(f"Found {len(mix_dirs)} existing extracted mixes")

    # Merge and deduplicate
    if not args.skip_merge and mix_dirs:
        merge_into_characters(mix_dirs, merged_dir)
        deduplicate(merged_dir)

    print(f"\n{'=' * 60}")
    print("All done!")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()

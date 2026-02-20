#!/usr/bin/env python3
"""
Merge curated skin palettes into a personal collection folder.

After extracting and triaging skins (via mvc2_skin_processor + gallery + apply_verdicts),
this script copies the curated skins into your personal palette collection, handling
deduplication and naming.

Usage:
    python merge_palettes.py <curated_folder> <collection_folder>
    python merge_palettes.py ./output ./my_palettes --dry-run
    python merge_palettes.py ./output ./my_palettes --skip-defaults
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

from PIL import Image


def get_full_palette_hash(filepath):
    """Get SHA256 hash of the full palette data from an indexed PNG."""
    img = Image.open(filepath)
    if img.mode != 'P':
        return None
    pal = img.getpalette()
    if not pal:
        return None
    pixels = img.tobytes()
    max_idx = max(pixels) if pixels else 0
    num_rows = (max_idx // 16) + 1
    pal_bytes = bytes(pal[:num_rows * 48])
    return hashlib.sha256(pal_bytes).hexdigest()


def load_default_hashes(defaults_path):
    """Load default palette hashes from JSON file."""
    if not os.path.isfile(defaults_path):
        return set()
    with open(defaults_path, 'r') as f:
        data = json.load(f)
    # Flatten all hashes into a set
    hashes = set()
    for char_hashes in data.values():
        hashes.update(char_hashes.values() if isinstance(char_hashes, dict) else char_hashes)
    return hashes


def scan_collection(collection_dir):
    """Build a set of palette hashes already in the collection."""
    existing = set()
    if not os.path.isdir(collection_dir):
        return existing
    for char_dir in os.listdir(collection_dir):
        char_path = os.path.join(collection_dir, char_dir)
        if not os.path.isdir(char_path):
            continue
        for f in os.listdir(char_path):
            if not f.lower().endswith('.png'):
                continue
            try:
                h = get_full_palette_hash(os.path.join(char_path, f))
                if h:
                    existing.add(h)
            except Exception:
                pass
    return existing


def main():
    parser = argparse.ArgumentParser(
        description="Merge curated skins into a personal palette collection"
    )
    parser.add_argument("input", help="Curated skins folder (output of extraction + triage)")
    parser.add_argument("collection", help="Personal palette collection folder")
    parser.add_argument("--dry-run", action="store_true", help="Preview without copying")
    parser.add_argument("--skip-defaults", action="store_true",
                        help="Skip skins matching default game palettes")
    parser.add_argument("--defaults-file", default=None,
                        help="Path to default_hashes.json (default: auto-detect)")
    args = parser.parse_args()

    input_dir = os.path.abspath(args.input)
    collection_dir = os.path.abspath(args.collection)

    # Auto-detect defaults file
    defaults_file = args.defaults_file
    if defaults_file is None:
        defaults_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "default_hashes.json")

    print("=" * 60)
    print("MvC2 Palette Merger")
    print("=" * 60)
    print(f"Source:     {input_dir}")
    print(f"Collection: {collection_dir}")
    if args.skip_defaults:
        print(f"Defaults:   {defaults_file}")
    if args.dry_run:
        print("MODE:       DRY RUN")
    print()

    # Load default hashes if requested
    default_hashes = set()
    if args.skip_defaults:
        default_hashes = load_default_hashes(defaults_file)
        if default_hashes:
            print(f"Loaded {len(default_hashes)} default palette hashes")
        else:
            print("WARNING: No default hashes found — skipping default detection")
        print()

    # Scan existing collection for dedup
    print("Scanning existing collection...")
    existing_hashes = scan_collection(collection_dir)
    print(f"  {len(existing_hashes)} existing palettes indexed")
    print()

    # Process input
    copied = 0
    skipped_dup = 0
    skipped_default = 0
    errors = 0

    for char_name in sorted(os.listdir(input_dir)):
        char_path = os.path.join(input_dir, char_name)
        if not os.path.isdir(char_path):
            continue

        pngs = sorted(f for f in os.listdir(char_path) if f.lower().endswith('.png'))
        if not pngs:
            continue

        char_copied = 0
        for fname in pngs:
            fpath = os.path.join(char_path, fname)
            try:
                pal_hash = get_full_palette_hash(fpath)
            except Exception as e:
                print(f"  ERROR reading {char_name}/{fname}: {e}")
                errors += 1
                continue

            if pal_hash is None:
                errors += 1
                continue

            # Skip defaults
            if args.skip_defaults and pal_hash in default_hashes:
                skipped_default += 1
                continue

            # Skip duplicates
            if pal_hash in existing_hashes:
                skipped_dup += 1
                continue

            # Copy to collection
            dest_dir = os.path.join(collection_dir, char_name)
            dest_path = os.path.join(dest_dir, fname)

            # Handle filename collision
            if os.path.exists(dest_path):
                base, ext = os.path.splitext(fname)
                dest_path = os.path.join(dest_dir, f"{base}_{pal_hash[:6]}{ext}")

            if args.dry_run:
                char_copied += 1
            else:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(fpath, dest_path)
                char_copied += 1

            existing_hashes.add(pal_hash)
            copied += 1

        if char_copied > 0:
            print(f"  {char_name:<25} +{char_copied} new")

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"Dry run: {copied} would be copied")
    else:
        print(f"Done! {copied} new skins merged into collection")
    print(f"  Skipped: {skipped_dup} duplicates, {skipped_default} defaults")
    if errors:
        print(f"  Errors: {errors}")


if __name__ == "__main__":
    main()

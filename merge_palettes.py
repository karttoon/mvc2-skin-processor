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
    python merge_palettes.py ./output ./my_palettes --skip-defaults --clean
"""
import argparse
import hashlib
import json
import os
import shutil
import sys

from PIL import Image


def has_shifted_palette(img):
    """Check if an indexed PNG has colors at wrong palette indices.

    Returns True if body palette (indices 1-15) is all black but pixels
    use higher indices, indicating colors were placed at the wrong offset
    (e.g. 240-255 instead of 0-15).
    """
    if img.mode != 'P':
        return False
    pal = img.getpalette()
    if not pal or len(pal) < 48:
        return False
    pixels = img.tobytes()
    max_idx = max(pixels) if pixels else 0
    if max_idx <= 15:
        return False
    return all(pal[i * 3] == 0 and pal[i * 3 + 1] == 0 and pal[i * 3 + 2] == 0
               for i in range(1, 16))


def get_full_palette_hash(filepath):
    """Get SHA256 hash of the full palette data from an indexed PNG.

    Index 0 is always forced to transparent at render time, so its RGB
    value is irrelevant.  Zero it out before hashing so that palettes
    differing only in the transparent slot are treated as identical.
    """
    img = Image.open(filepath)
    if img.mode != 'P':
        return None
    pal = img.getpalette()
    if not pal:
        return None
    # Zero out index 0 (transparent) so junk values don't affect hash
    pal[0] = pal[1] = pal[2] = 0
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


def scan_collection(collection_dir, only_chars=None):
    """Build a set of palette hashes already in the collection.

    If only_chars is provided, only scan those character folders (much faster
    when merging a small batch into a large collection).
    """
    existing = set()
    if not os.path.isdir(collection_dir):
        return existing
    dirs_to_scan = only_chars if only_chars else os.listdir(collection_dir)
    for char_dir in dirs_to_scan:
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
    parser.add_argument("--clean", action="store_true",
                        help="Remove merged files and verdicts.tsv from source after successful merge")
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

    # Determine which characters we're merging
    source_chars = [d for d in os.listdir(input_dir)
                    if os.path.isdir(os.path.join(input_dir, d))]
    print(f"Source characters: {len(source_chars)}")

    # Scan only the matching character folders in the collection for dedup
    print("Scanning existing collection...")
    existing_hashes = scan_collection(collection_dir, only_chars=source_chars)
    print(f"  {len(existing_hashes)} existing palettes indexed (from {len(source_chars)} character folders)")
    print()

    # Process input
    copied = 0
    skipped_dup = 0
    skipped_default = 0
    errors = 0
    merged_files = []  # source paths that were successfully merged

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
                # Reject PNGs with shifted palette indices (body all-black,
                # colors at wrong offsets like 240-255)
                check_img = Image.open(fpath)
                if has_shifted_palette(check_img):
                    print(f"  WARNING: {char_name}/{fname} has shifted palette "
                          f"(body indices all black) — skipping")
                    errors += 1
                    check_img.close()
                    continue
                check_img.close()

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
                merged_files.append(fpath)  # defaults are "handled" — safe to clean
                continue

            # Skip duplicates
            if pal_hash in existing_hashes:
                skipped_dup += 1
                merged_files.append(fpath)  # dupes are "handled" — safe to clean
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
            merged_files.append(fpath)
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

    # --clean: remove merged source files and verdicts
    if args.clean and not args.dry_run and merged_files:
        print(f"\nCleaning source folder...")
        removed = 0
        for fpath in merged_files:
            if os.path.isfile(fpath):
                os.remove(fpath)
                removed += 1

        # Remove verdicts.tsv if present
        verdicts_path = os.path.join(input_dir, "verdicts.tsv")
        if os.path.isfile(verdicts_path):
            os.remove(verdicts_path)
            print(f"  Removed verdicts.tsv")

        # Clean empty character directories
        for char_name in os.listdir(input_dir):
            char_path = os.path.join(input_dir, char_name)
            if os.path.isdir(char_path) and not os.listdir(char_path):
                os.rmdir(char_path)

        print(f"  Removed {removed} source files")
        if errors:
            print(f"  {errors} file(s) with errors left in source")


if __name__ == "__main__":
    main()

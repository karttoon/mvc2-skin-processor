#!/usr/bin/env python3
"""
Apply gallery verdicts — remove skins marked as 'skip' in the verdicts file.

After reviewing skins in the gallery (gallery.py), this script processes
the verdicts.tsv and deletes files marked as 'skip'.

Usage:
    python apply_verdicts.py <skins_folder>                     # uses <folder>/verdicts.tsv
    python apply_verdicts.py <skins_folder> --verdicts my.tsv   # custom verdicts file
    python apply_verdicts.py <skins_folder> --dry-run           # preview without deleting
"""
import argparse
import os
import sys


def load_verdicts(verdicts_file):
    """Load verdicts from TSV. Last entry per key wins."""
    verdicts = {}
    with open(verdicts_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) == 2:
                verdicts[parts[0]] = parts[1]
    return verdicts


def main():
    parser = argparse.ArgumentParser(
        description="Apply gallery verdicts — remove skins marked as 'skip'"
    )
    parser.add_argument("input", help="Skins folder (same folder passed to gallery.py)")
    parser.add_argument("--verdicts", help="Verdicts file (default: <input>/verdicts.tsv)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = parser.parse_args()

    root_dir = os.path.abspath(args.input)
    verdicts_file = args.verdicts or os.path.join(root_dir, "verdicts.tsv")

    if not os.path.isfile(verdicts_file):
        print(f"Error: verdicts file not found: {verdicts_file}")
        print("Run gallery.py first to review and create verdicts.")
        sys.exit(1)

    verdicts = load_verdicts(verdicts_file)
    kept = sum(1 for v in verdicts.values() if v == 'keep')
    skipped = sum(1 for v in verdicts.values() if v == 'skip')
    total = len(verdicts)

    print("=" * 60)
    print("Apply Gallery Verdicts")
    print("=" * 60)
    print(f"Source:   {root_dir}")
    print(f"Verdicts: {verdicts_file}")
    print(f"Total:    {total} reviewed ({kept} kept, {skipped} to remove)")
    if args.dry_run:
        print("MODE:     DRY RUN (no files will be deleted)")
    print()

    removed = 0
    not_found = 0
    errors = 0

    for key, verdict in sorted(verdicts.items()):
        if verdict != 'skip':
            continue

        filepath = os.path.join(root_dir, key)
        if not os.path.isfile(filepath):
            not_found += 1
            continue

        if args.dry_run:
            print(f"  [DRY] Would remove: {key}")
        else:
            try:
                os.remove(filepath)
                print(f"  Removed: {key}")
                removed += 1
            except Exception as e:
                print(f"  ERROR removing {key}: {e}")
                errors += 1

    # Clean up empty character directories
    if not args.dry_run:
        for char_dir in sorted(os.listdir(root_dir)):
            char_path = os.path.join(root_dir, char_dir)
            if os.path.isdir(char_path):
                remaining = [f for f in os.listdir(char_path) if f.lower().endswith('.png')]
                if not remaining:
                    os.rmdir(char_path)
                    print(f"  Removed empty directory: {char_dir}/")

    print(f"\n{'=' * 60}")
    if args.dry_run:
        print(f"Dry run: {skipped} files would be removed")
    else:
        print(f"Done! Removed {removed} files")
        if not_found:
            print(f"  ({not_found} already gone)")
        if errors:
            print(f"  ({errors} errors)")


if __name__ == "__main__":
    main()

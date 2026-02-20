#!/usr/bin/env python3
"""
Re-render only multi-row characters from all mix archives.

This is a targeted script that:
1. Iterates over all archives in the mixes directory
2. Extracts ONLY multi-row character palette files
3. Re-renders with corrected palette slot mapping
4. Merges into per-character folders with deduplication
"""
import os
import sys
import struct
import shutil
import hashlib
import subprocess
import tempfile
import glob
import io

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np

from mvc2_extract.cdi import parse_cdi
from mvc2_extract.palettes import extract_palette_files, parse_palettes
from mvc2_extract.sprites import ImgDat
from mvc2_extract.renderer import render_sprite, render_composite
from mvc2_extract.characters import (
    CHARACTERS, BUTTON_NAMES, PLAYABLE_CHARS, safe_name, palette_rows,
    palette_slot_map, PALETTE_ROWS,
)

# Only process these characters
MULTI_ROW_CHARS = set(PALETTE_ROWS.keys())

DEFAULT_IMGDAT = r"C:\Program Files (x86)\PalMod\img2020.dat"
SEVENZ = r"C:\Program Files\NVIDIA Corporation\NVIDIA App\7z.exe"

# Items to skip
SKIP_ITEMS = {
    "_extracted", "_unsupported", "merged", "unpacked", "mvc2-skin-extractor",
    "MVC2 Kest 2025",
    "cdi_extract.py", "explore.py", "extract_mix.py", "img2020_probe.py",
    "mix2sprites.py", "palette_debug.py", "palette_probe.py", "palette_probe_v2.py",
    "sprite_renderer.py", "download_all_mixes.py", "download_mixes.py",
    "cluster_viewer.py", "extract_composite_bases.py",
}


def load_composite_bases():
    """Load composite base sprites for multi-row characters."""
    bases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "composite_bases")
    composite_bases = {}

    for cid in sorted(PALETTE_ROWS.keys()):
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        npz_path = os.path.join(bases_dir, f"{sname}.npz")
        if not os.path.exists(npz_path):
            print(f"  WARNING: No composite base for {cname}")
            continue
        base = np.load(npz_path)
        pixels = base['pixels']
        w = int(base['width'])
        h = int(base['height'])
        num_rows = int(base['num_rows'])
        default_pal = base['default_palette'] if 'default_palette' in base else None
        composite_bases[cid] = (pixels, w, h, num_rows, default_pal)

    return composite_bases


def render_multirow(pal_data, out_dir, suffix, composite_bases):
    """Render only multi-row characters from palette data."""
    os.makedirs(out_dir, exist_ok=True)
    rendered = 0

    for cid in MULTI_ROW_CHARS:
        if cid not in pal_data:
            continue

        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        num_rows = palette_rows(cid)

        if cid not in composite_bases:
            continue

        try:
            palettes = parse_palettes(pal_data[cid])
        except Exception:
            continue

        base_pixels, w, h, _, default_pal = composite_bases[cid]
        slot_map = palette_slot_map(cid)

        char_dir = os.path.join(out_dir, sname)
        os.makedirs(char_dir, exist_ok=True)

        for bi, btn in enumerate(BUTTON_NAMES):
            pal_base = bi * 8
            if pal_base >= len(palettes):
                continue

            button_palettes = []
            for row in range(num_rows):
                slot_offset = slot_map[row] if row < len(slot_map) else row
                pal_idx = pal_base + slot_offset
                if pal_idx < len(palettes):
                    button_palettes.append(palettes[pal_idx])
                else:
                    button_palettes.append(None)

            # Fill in missing rows from default palette
            filled = []
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

            img = render_composite(base_pixels, w, h, filled, num_rows, default_pal)
            filename = f"{sname}_{btn}_{suffix}.png"
            img.save(os.path.join(char_dir, filename))
            rendered += 1

    return rendered


def extract_palettes_from_cdi_data(iso_data):
    """Extract multi-row character palette files from CDI ISO data."""
    pal_data = extract_palette_files(iso_data)
    # Filter to only multi-row characters
    return {cid: data for cid, data in pal_data.items() if cid in MULTI_ROW_CHARS}


def make_suffix(name):
    """Create a filesystem-safe suffix from an item name."""
    base = os.path.splitext(name)[0]
    safe = base.replace(" ", "_").replace("(", "").replace(")", "")
    safe = safe.replace(".", "_").replace(",", "").replace("'", "")
    safe = safe.replace("-", "_").replace("&", "and")
    safe = safe.replace("__", "_").strip("_")
    return safe


def process_archive(item_path, item_name, extracted_dir, composite_bases):
    """Process a single archive, rendering only multi-row characters."""
    suffix = make_suffix(item_name)
    out_dir = os.path.join(extracted_dir, suffix)

    ext = os.path.splitext(item_name)[1].lower()

    if ext == '.pkg':
        # PS3 PKG
        try:
            from ps3_pkg_extract import PKGExtractor
            pkg = PKGExtractor(item_path)
            with tempfile.TemporaryDirectory() as tmp:
                pal_files = pkg.extract_palette_files(tmp, verbose=False)
                if not pal_files:
                    return 0
                pal_data = {}
                for pf in pal_files:
                    fname = os.path.basename(pf).upper()
                    if fname.startswith("PL") and (fname.endswith("_DAT.BIN") or fname.endswith("PAK.BIN")):
                        hex_str = fname[2:4]
                        try:
                            cid = int(hex_str, 16)
                        except ValueError:
                            continue
                        if cid in MULTI_ROW_CHARS:
                            with open(pf, 'rb') as f:
                                pal_data[cid] = f.read()
                if not pal_data:
                    return 0
                return render_multirow(pal_data, out_dir, suffix, composite_bases)
        except Exception as e:
            print(f"  ERROR (PKG): {e}")
            return 0

    # Archive (zip, rar, 7z, etc.)
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [SEVENZ, "x", "-y", f"-o{tmp}", item_path],
                capture_output=True, timeout=120
            )
        except Exception as e:
            print(f"  ERROR extracting: {e}")
            return 0

        # Find CDI files
        cdis = glob.glob(os.path.join(tmp, "**/*.cdi"), recursive=True)
        cdis += glob.glob(os.path.join(tmp, "**/*.CDI"), recursive=True)

        total_rendered = 0
        for cdi_path in cdis:
            try:
                iso_data = parse_cdi(cdi_path)
                pal_data = extract_palettes_from_cdi_data(iso_data)
                if not pal_data:
                    continue
                rendered = render_multirow(pal_data, out_dir, suffix, composite_bases)
                total_rendered += rendered
            except Exception as e:
                print(f"  ERROR (CDI): {e}")
                continue

        return total_rendered


def deduplicate(merged_dir):
    """Remove duplicate skins based on palette+pixel content hash."""
    removed = 0
    for char_dir in sorted(os.listdir(merged_dir)):
        char_path = os.path.join(merged_dir, char_dir)
        if not os.path.isdir(char_path):
            continue

        seen_hashes = {}
        files = sorted(os.listdir(char_path))
        for fname in files:
            if not fname.lower().endswith('.png'):
                continue
            fpath = os.path.join(char_path, fname)
            try:
                with open(fpath, 'rb') as f:
                    content = f.read()
                h = hashlib.sha256(content).hexdigest()
                if h in seen_hashes:
                    os.remove(fpath)
                    removed += 1
                else:
                    seen_hashes[h] = fname
            except Exception:
                continue

    return removed


def main():
    mixes_dir = sys.argv[1] if len(sys.argv) > 1 else r"D:\Storage\MvC2Modding\MvC2_Skins\Mixes"
    extracted_dir = os.path.join(mixes_dir, "_extracted")
    merged_dir = os.path.join(mixes_dir, "merged")

    print("=" * 60)
    print("Re-rendering multi-row characters with corrected slot mapping")
    print("=" * 60)

    # Load composite bases
    print("\nLoading composite bases...")
    composite_bases = load_composite_bases()
    print(f"  Loaded {len(composite_bases)} composite bases")

    # Find all archives
    items = []
    for item in sorted(os.listdir(mixes_dir)):
        if item in SKIP_ITEMS:
            continue
        if item.startswith("_") or item.startswith("."):
            continue
        if item.endswith(".py") or item.endswith(".txt") or item.endswith(".png"):
            continue
        item_path = os.path.join(mixes_dir, item)
        if os.path.isfile(item_path):
            items.append((item_path, item))

    print(f"\nProcessing {len(items)} archives...")
    total_rendered = 0
    processed = 0

    for item_path, item_name in items:
        ext = os.path.splitext(item_name)[1].lower()
        if ext not in ('.zip', '.rar', '.7z', '.pkg'):
            continue

        processed += 1
        print(f"\n[{processed}] {item_name}")
        rendered = process_archive(item_path, item_name, extracted_dir, composite_bases)
        total_rendered += rendered
        if rendered > 0:
            print(f"  -> {rendered} skins rendered")

    print(f"\n{'=' * 60}")
    print(f"Rendered {total_rendered} multi-row character skins from {processed} archives")

    # Merge into per-character folders
    print("\nMerging into character folders...")
    for sname in sorted(set(safe_name(CHARACTERS[cid]) for cid in MULTI_ROW_CHARS)):
        dest_dir = os.path.join(merged_dir, sname)
        os.makedirs(dest_dir, exist_ok=True)

    merged_count = 0
    for mix_dir in sorted(os.listdir(extracted_dir)):
        mix_path = os.path.join(extracted_dir, mix_dir)
        if not os.path.isdir(mix_path):
            continue
        for sname in os.listdir(mix_path):
            if sname not in set(safe_name(CHARACTERS[cid]) for cid in MULTI_ROW_CHARS):
                continue
            src_dir = os.path.join(mix_path, sname)
            dest_dir = os.path.join(merged_dir, sname)
            if not os.path.isdir(src_dir):
                continue
            for fname in os.listdir(src_dir):
                if not fname.lower().endswith('.png'):
                    continue
                src = os.path.join(src_dir, fname)
                dst = os.path.join(dest_dir, fname)
                shutil.copy2(src, dst)
                merged_count += 1

    print(f"  Merged {merged_count} files into character folders")

    # Deduplicate
    print("\nDeduplicating...")
    removed = deduplicate(merged_dir)
    print(f"  Removed {removed} duplicates")

    # Final count
    final_count = 0
    for sname in sorted(set(safe_name(CHARACTERS[cid]) for cid in MULTI_ROW_CHARS)):
        char_dir = os.path.join(merged_dir, sname)
        if os.path.isdir(char_dir):
            count = len([f for f in os.listdir(char_dir) if f.endswith('.png')])
            final_count += count
            print(f"  {sname:<25} {count} skins")

    print(f"\nTotal: {final_count} unique multi-row character skins")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Mass MvC2 Mix Processor — Unpack, extract, merge, deduplicate, clean up.

Processes all archives/CDIs in a directory:
1. Extracts each archive to find CDI files
2. Extracts palettes from each CDI
3. Renders sprites with mix name as suffix
4. Merges into per-character folders
5. Deduplicates identical files
6. Cleans up archives that were successfully processed
7. Sets aside non-CDI formats (PS3, etc.)

Usage:
    python process_all_mixes.py <mixes_directory>
"""
import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys
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
    palette_slot_map,
)

DEFAULT_IMGDAT = os.environ.get("MVC2_IMGDAT")
SEVENZ = os.environ.get("SEVENZ") or shutil.which("7z")

# Items to skip
SKIP_ITEMS = {
    "_extracted", "_unsupported", "merged", "unpacked", "mvc2-skin-extractor",
    "MVC2 Kest 2025",
    "cdi_extract.py", "explore.py", "extract_mix.py", "img2020_probe.py",
    "mix2sprites.py", "palette_debug.py", "palette_probe.py", "palette_probe_v2.py",
    "sprite_renderer.py", "download_all_mixes.py", "download_mixes.py",
    "MVC2 Mix Archive-20260218T152414Z-1-010.zip",
}


def sanitize_name(name):
    """Clean up a mix name for use as filename suffix."""
    # Remove extension
    name = os.path.splitext(name)[0]
    # Remove common prefixes/suffixes
    for remove in ["(DC) ", "(dc) ", "MVC2 ", "mvc2 ", "Marvel vs Capcom 2 ",
                    "Marvel vs. Capcom 2 ", "Marvel Vs Capcom 2 ", "Mvc2 "]:
        if name.startswith(remove):
            name = name[len(remove):]
    name = name.strip()
    # Replace problematic chars
    for ch in " .-()[]{}!@#$%^&+=,':":
        name = name.replace(ch, "_")
    while "__" in name:
        name = name.replace("__", "_")
    return name.strip("_")


def extract_archive(archive_path, dest_dir, sevenz_path=None):
    """Extract an archive using 7z. Returns True if successful."""
    sz = sevenz_path or SEVENZ
    try:
        result = subprocess.run(
            [sz, "x", archive_path, f"-o{dest_dir}", "-y"],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception as e:
        print(f"    Extract error: {e}")
        return False


def find_cdis(directory):
    """Find all CDI files recursively under a directory."""
    cdis = []
    for root, dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith('.cdi'):
                cdis.append(os.path.join(root, f))
    return cdis


def _is_palette_file(filename):
    """Check if a filename matches MvC2 palette file patterns.

    Supports both Dreamcast (PL??_DAT.BIN) and PS2/PS3 (PL??PAK.BIN) naming.
    Returns the character ID (int) if matched, or None.
    """
    fn = filename.upper()
    if not fn.startswith("PL"):
        return None
    # Dreamcast: PL??_DAT.BIN (12 chars)
    if fn.endswith("_DAT.BIN") and len(fn) == 12:
        hex_id = fn[2:4]
    # PS2/PS3: PL??PAK.BIN (11 chars)
    elif fn.endswith("PAK.BIN") and len(fn) == 11:
        hex_id = fn[2:4]
    else:
        return None
    try:
        return int(hex_id, 16)
    except ValueError:
        return None


def find_palette_files_in_dir(directory):
    """Find palette files in an already-extracted directory (no CDI needed).

    Supports both Dreamcast (PL??_DAT.BIN) and PS2/PS3 (PL??PAK.BIN) naming.
    """
    palettes = {}
    for root, dirs, files in os.walk(directory):
        for f in files:
            char_id = _is_palette_file(f)
            if char_id is not None:
                fpath = os.path.join(root, f)
                with open(fpath, "rb") as fp:
                    palettes[char_id] = fp.read()
    return palettes


def has_palette_files(directory):
    """Check if a directory tree contains MvC2 palette files."""
    for root, dirs, files in os.walk(directory):
        for f in files:
            if _is_palette_file(f) is not None:
                return True
    return False


def try_extract_cdi_with_fallback(cdi_path):
    """Try to extract palettes from a CDI, with fallback for malformed ISOs."""
    try:
        iso_data = parse_cdi(cdi_path, quiet=True)
    except Exception as e:
        return None, str(e)

    # Try pycdlib first
    try:
        pal_data = extract_palette_files(iso_data, quiet=True)
        if pal_data:
            return pal_data, None
    except Exception:
        pass

    # Fallback: manual ISO directory parsing for malformed path tables
    try:
        pal_data = extract_palettes_manual(iso_data, cdi_path)
        if pal_data:
            return pal_data, None
    except Exception as e:
        return None, f"Both pycdlib and manual extraction failed: {e}"

    return None, "No palette files found"


def extract_palettes_manual(iso_data, cdi_path):
    """Manual ISO9660 directory parsing — fallback for malformed ISOs."""
    sector_size = 2048

    # Determine LBA offset from CDI track info
    lba_offset = get_track_start_lba(cdi_path)

    pvd_off = 16 * sector_size
    if pvd_off + sector_size > len(iso_data):
        return None
    pvd = iso_data[pvd_off:pvd_off + sector_size]
    if pvd[0] != 1 or pvd[1:6] != b'CD001':
        return None

    root_rec = pvd[156:156 + 34]
    root_lba = struct.unpack_from('<I', root_rec, 2)[0]
    root_size = struct.unpack_from('<I', root_rec, 10)[0]

    palettes = {}

    def parse_dir(dir_lba, dir_size):
        buf_sector = dir_lba - lba_offset
        offset = buf_sector * sector_size
        end = offset + dir_size
        entries = []

        while offset < end and offset < len(iso_data):
            rec_len = iso_data[offset]
            if rec_len == 0:
                next_sector = ((offset // sector_size) + 1) * sector_size
                if next_sector >= end:
                    break
                offset = next_sector
                continue
            if rec_len < 34 or offset + rec_len > len(iso_data):
                offset += max(rec_len, 1)
                continue

            record = iso_data[offset:offset + rec_len]
            ext_lba = struct.unpack_from('<I', record, 2)[0]
            data_len = struct.unpack_from('<I', record, 10)[0]
            flags = record[25]
            fn_len = record[32]
            if fn_len > 0 and 33 + fn_len <= len(record):
                try:
                    filename = record[33:33 + fn_len].decode('ascii')
                except UnicodeDecodeError:
                    filename = ""
                if ';' in filename:
                    filename = filename.split(';')[0]
                is_dir = bool(flags & 0x02)
                entries.append((filename, ext_lba, data_len, is_dir))
            offset += rec_len
        return entries

    def walk_dirs(entries):
        for filename, lba, size, is_dir in entries:
            if is_dir and filename not in ('\x00', '\x01'):
                sub = parse_dir(lba, size)
                walk_dirs(sub)
            elif not is_dir and filename.startswith("PL") and filename.endswith("_DAT.BIN"):
                hex_id = filename[2:4]
                try:
                    char_id = int(hex_id, 16)
                except ValueError:
                    continue
                buf_sector = lba - lba_offset
                file_offset = buf_sector * sector_size
                if 0 <= file_offset and file_offset + size <= len(iso_data):
                    palettes[char_id] = iso_data[file_offset:file_offset + size]

    root_entries = parse_dir(root_lba, root_size)
    walk_dirs(root_entries)
    return palettes if palettes else None


def get_track_start_lba(cdi_path):
    """Parse CDI to get the data track's start LBA for manual ISO extraction."""
    CDI_V2, CDI_V3, CDI_V35 = 0x80000004, 0x80000005, 0x80000006
    with open(cdi_path, "rb") as f:
        f.seek(0, 2)
        file_length = f.tell()
        f.seek(file_length - 8)
        version = struct.unpack("<I", f.read(4))[0]
        header_offset = struct.unpack("<I", f.read(4))[0]

        if version == CDI_V35:
            f.seek(file_length - header_offset)
        elif version in (CDI_V2, CDI_V3):
            f.seek(header_offset)
        else:
            return 0

        num_sessions = struct.unpack("<H", f.read(2))[0]
        track_position = 0
        start_lba = 0

        for _ in range(num_sessions):
            num_tracks = struct.unpack("<H", f.read(2))[0]
            for _ in range(num_tracks):
                pos = track_position
                temp = struct.unpack("<I", f.read(4))[0]
                if temp != 0:
                    f.seek(8, 1)
                f.read(10); f.read(10)
                f.seek(4, 1)
                fn_len = struct.unpack("B", f.read(1))[0]
                f.seek(fn_len, 1)
                f.seek(11, 1); f.seek(4, 1); f.seek(4, 1)
                temp = struct.unpack("<I", f.read(4))[0]
                if temp == 0x80000000:
                    f.seek(8, 1)
                f.seek(2, 1)
                pregap = struct.unpack("<I", f.read(4))[0]
                length = struct.unpack("<i", f.read(4))[0]
                f.seek(6, 1)
                mode = struct.unpack("<I", f.read(4))[0]
                f.seek(12, 1)
                slba = struct.unpack("<I", f.read(4))[0]
                total_length = struct.unpack("<I", f.read(4))[0]
                f.seek(16, 1)
                ss_val = struct.unpack("<I", f.read(4))[0]
                sector_size = {0: 2048, 1: 2336, 2: 2352}[ss_val]
                f.seek(29, 1)
                if version != CDI_V2:
                    f.seek(5, 1)
                    temp = struct.unpack("<I", f.read(4))[0]
                    if temp == 0xffffffff:
                        f.seek(78, 1)
                track_position += total_length * sector_size
                if mode > 0 and length > 1000:
                    start_lba = slba
            f.seek(4, 1); f.seek(8, 1)
            if version != CDI_V2:
                f.seek(1, 1)

    return start_lba


def render_mix(pal_data, imgdat, out_dir, suffix, composite_bases=None):
    """Render all characters from palette data. Returns (rendered, errors).

    For characters with multi-row palettes (accessories like shields, animals, etc.),
    uses composite base sprites and gathers multiple palette rows per button.
    """
    os.makedirs(out_dir, exist_ok=True)
    rendered = 0
    errors = []
    if composite_bases is None:
        composite_bases = {}

    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        sname = safe_name(cname)
        num_rows = palette_rows(cid)

        if cid not in pal_data:
            continue  # Not an error — many mixes only change a few characters

        try:
            palettes = parse_palettes(pal_data[cid])
        except Exception:
            errors.append((cname, "bad palette data"))
            continue

        # Determine rendering approach
        use_composite = (num_rows > 1 and cid in composite_bases)

        if use_composite:
            base_pixels, w, h, _, default_pal = composite_bases[cid]
        else:
            sprite = imgdat.get_sprite(cid, img_id=0)
            if not sprite:
                errors.append((cname, "no sprite"))
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
            else:
                img = render_sprite(pixels, w, h, palettes[pal_base])

            filename = f"{sname}_{btn}_{suffix}.png"
            img.save(os.path.join(char_dir, filename))
            rendered += 1

    return rendered, errors


def process_item(item_path, item_name, imgdat, extracted_dir, unsupported_dir,
                  composite_bases=None):
    """Process a single archive/CDI. Returns (status, rendered_count, suffix).

    Status: 'ok', 'no_skins', 'error', 'unsupported'
    """
    suffix = sanitize_name(item_name)
    out_dir = os.path.join(extracted_dir, suffix)

    ext = os.path.splitext(item_name)[1].lower()

    # PS3 packages — extract palette files using our PKG extractor
    # PS3 MvC2 mods contain PL??PAK.BIN palette files in USRDIR/gdrom/
    # Same internal format as Dreamcast PL??_DAT.BIN (ARGB4444 LE uint16)
    if ext == '.pkg':
        print(f"  PS3 package — extracting palettes...")
        try:
            from ps3_pkg_extract import PKGExtractor
            pkg = PKGExtractor(item_path)
            print(f"    Content: {pkg.content_id} ({'Debug' if pkg.is_debug else 'Retail'})")

            # Extract palette files to temp directory
            tmp_dir = os.path.join(extracted_dir, f"_tmp_{suffix}")
            os.makedirs(tmp_dir, exist_ok=True)
            extracted_files = pkg.extract_palette_files(tmp_dir, verbose=False)

            if extracted_files:
                print(f"    Found {len(extracted_files)} palette files")
                pal_data = find_palette_files_in_dir(tmp_dir)
                if pal_data:
                    rendered, errors = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    if rendered > 0:
                        return 'ok', rendered, suffix
            shutil.rmtree(tmp_dir, ignore_errors=True)

        except Exception as e:
            print(f"    PKG extraction error: {e}")

        # If extraction failed or no palette files found
        print(f"  Could not extract palettes from PS3 package")
        dest = os.path.join(unsupported_dir, item_name)
        if not os.path.exists(dest):
            shutil.move(item_path, dest)
        return 'unsupported', 0, suffix

    # Standalone CDI
    if ext == '.cdi':
        print(f"  Extracting CDI directly...")
        pal_data, err = try_extract_cdi_with_fallback(item_path)
        if pal_data:
            rendered, errors = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
            if rendered > 0:
                return 'ok', rendered, suffix
            else:
                return 'no_skins', 0, suffix
        else:
            print(f"    CDI error: {err}")
            return 'error', 0, suffix

    # Archive — extract to temp, find CDI or palette files
    if ext in ('.zip', '.rar', '.7z'):
        tmp_dir = os.path.join(extracted_dir, f"_tmp_{suffix}")
        os.makedirs(tmp_dir, exist_ok=True)

        print(f"  Extracting archive...")
        if not extract_archive(item_path, tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return 'error', 0, suffix

        # Look for CDI files
        cdis = find_cdis(tmp_dir)
        if cdis:
            total_rendered = 0
            for cdi_path in cdis:
                print(f"  Found CDI: {os.path.basename(cdi_path)}")
                pal_data, err = try_extract_cdi_with_fallback(cdi_path)
                if pal_data:
                    rendered, errors = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
                    total_rendered += rendered
                else:
                    print(f"    CDI error: {err}")

            shutil.rmtree(tmp_dir, ignore_errors=True)
            if total_rendered > 0:
                return 'ok', total_rendered, suffix
            else:
                return 'no_skins', 0, suffix

        # No CDI — check for loose palette files (pre-extracted disc)
        if has_palette_files(tmp_dir):
            print(f"  No CDI, but found palette files directly")
            pal_data = find_palette_files_in_dir(tmp_dir)
            if pal_data:
                rendered, errors = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if rendered > 0:
                    return 'ok', rendered, suffix

        # Check if it contains nested archives
        nested = []
        for root, dirs, files in os.walk(tmp_dir):
            for f in files:
                if f.lower().endswith(('.zip', '.rar', '.7z')):
                    nested.append(os.path.join(root, f))

        if nested:
            total_rendered = 0
            for nested_archive in nested:
                print(f"  Nested archive: {os.path.basename(nested_archive)}")
                nested_tmp = os.path.join(extracted_dir, f"_tmp_{suffix}_nested")
                os.makedirs(nested_tmp, exist_ok=True)
                if extract_archive(nested_archive, nested_tmp):
                    nested_cdis = find_cdis(nested_tmp)
                    for cdi_path in nested_cdis:
                        print(f"    Found CDI: {os.path.basename(cdi_path)}")
                        pal_data, err = try_extract_cdi_with_fallback(cdi_path)
                        if pal_data:
                            rendered, _ = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
                            total_rendered += rendered
                    # Also check for loose palette files
                    if total_rendered == 0 and has_palette_files(nested_tmp):
                        pal_data = find_palette_files_in_dir(nested_tmp)
                        if pal_data:
                            rendered, _ = render_mix(pal_data, imgdat, out_dir, suffix, composite_bases)
                            total_rendered += rendered
                shutil.rmtree(nested_tmp, ignore_errors=True)

            shutil.rmtree(tmp_dir, ignore_errors=True)
            if total_rendered > 0:
                return 'ok', total_rendered, suffix
            else:
                return 'no_skins', 0, suffix

        shutil.rmtree(tmp_dir, ignore_errors=True)
        return 'no_skins', 0, suffix

    return 'error', 0, suffix


def merge_extracted(extracted_dir, merged_dir):
    """Merge all extracted character folders into the merged output."""
    total = 0
    for mix_folder in os.listdir(extracted_dir):
        mix_path = os.path.join(extracted_dir, mix_folder)
        if not os.path.isdir(mix_path) or mix_folder.startswith("_tmp"):
            continue
        for char_folder in os.listdir(mix_path):
            src_char = os.path.join(mix_path, char_folder)
            if not os.path.isdir(src_char):
                continue
            dst_char = os.path.join(merged_dir, char_folder)
            os.makedirs(dst_char, exist_ok=True)
            for fname in os.listdir(src_char):
                if fname.lower().endswith('.png'):
                    src = os.path.join(src_char, fname)
                    dst = os.path.join(dst_char, fname)
                    if not os.path.exists(dst):
                        shutil.copy2(src, dst)
                        total += 1
    return total


def deduplicate(merged_dir):
    """Remove duplicate files by image content within each character folder.

    Compares palette + pixel data rather than raw file bytes, so PNGs with
    identical colors but different compression are correctly identified as dupes.
    """
    from PIL import Image

    total = 0
    unique = 0
    removed = 0

    for char_folder in sorted(os.listdir(merged_dir)):
        char_dir = os.path.join(merged_dir, char_folder)
        if not os.path.isdir(char_dir):
            continue

        hash_to_files = {}
        for fname in os.listdir(char_dir):
            fpath = os.path.join(char_dir, fname)
            if not fname.lower().endswith('.png'):
                continue
            total += 1
            img = Image.open(fpath)
            pal = img.getpalette() or []
            pixels = img.tobytes()
            max_idx = max(pixels) if pixels else 0
            # Hash full multi-row palette based on actual pixel index usage
            pal_rows_used = (max_idx // 16) + 1
            pal_bytes = bytes(pal[:pal_rows_used * 48])  # N rows x 16 colors x 3 channels
            content = pal_bytes + pixels
            fhash = hashlib.sha256(content).hexdigest()
            hash_to_files.setdefault(fhash, []).append(fpath)

        char_removed = 0
        for fhash, files in hash_to_files.items():
            unique += 1
            files.sort()
            # Keep Default suffix files preferentially
            default_files = [f for f in files if '_Default.png' in f]
            if default_files:
                keep = default_files[0]
                for dup in files:
                    if dup != keep:
                        os.remove(dup)
                        char_removed += 1
                        removed += 1
            else:
                for dup in files[1:]:
                    os.remove(dup)
                    char_removed += 1
                    removed += 1

    return total, unique, removed


def main():
    global SEVENZ

    parser = argparse.ArgumentParser(description="Mass MvC2 mix processor")
    parser.add_argument("input", help="Mixes directory")
    parser.add_argument("-o", "--output", help="Merged output directory")
    parser.add_argument("--imgdat", default=DEFAULT_IMGDAT,
                        help="Path to img2020.dat (or set MVC2_IMGDAT env var)")
    parser.add_argument("--7z", dest="sevenz", default=SEVENZ,
                        help="Path to 7z executable (or set SEVENZ env var; auto-detected from PATH)")
    parser.add_argument("--no-cleanup", action="store_true",
                        help="Don't delete archives after processing")
    args = parser.parse_args()

    if not args.imgdat:
        parser.error("--imgdat is required (or set MVC2_IMGDAT env var)")
    if not args.sevenz:
        parser.error("--7z is required (or set SEVENZ env var, or install 7z on PATH)")

    SEVENZ = args.sevenz

    mixes_dir = args.input
    merged_dir = args.output or os.path.join(mixes_dir, "merged")
    extracted_dir = os.path.join(mixes_dir, "_extracted")
    unsupported_dir = os.path.join(mixes_dir, "_unsupported")
    os.makedirs(extracted_dir, exist_ok=True)
    os.makedirs(unsupported_dir, exist_ok=True)

    print("=" * 60)
    print("MvC2 Mass Mix Processor")
    print("=" * 60)

    # Load sprite database once
    print("Loading sprite database...")
    imgdat = ImgDat(args.imgdat)
    print(f"  {sum(len(v) for v in imgdat.sprites.values())} sprites indexed")

    # Load composite base sprites for multi-row characters
    composite_bases_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "composite_bases")
    composite_bases = {}
    if os.path.isdir(composite_bases_dir):
        for cid in PLAYABLE_CHARS:
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
        print(f"  Loaded {len(composite_bases)} composite base sprites for multi-row characters")
    else:
        print("  WARNING: No composite_bases/ directory found — multi-row rendering disabled")

    # Collect all items to process
    items = []
    for entry in sorted(os.listdir(mixes_dir)):
        if entry in SKIP_ITEMS:
            continue
        fpath = os.path.join(mixes_dir, entry)
        if os.path.isfile(fpath):
            ext = os.path.splitext(entry)[1].lower()
            if ext in ('.zip', '.rar', '.7z', '.cdi', '.pkg'):
                items.append((fpath, entry))

    print(f"\nFound {len(items)} items to process")

    # Process each
    results = {'ok': [], 'no_skins': [], 'error': [], 'unsupported': []}
    total_rendered = 0

    for i, (fpath, name) in enumerate(items):
        print(f"\n[{i+1}/{len(items)}] {name}")
        status, rendered, suffix = process_item(
            fpath, name, imgdat, extracted_dir, unsupported_dir, composite_bases
        )
        results[status].append(name)
        total_rendered += rendered

        if status == 'ok':
            print(f"  -> {rendered} sprites rendered")
            if not args.no_cleanup:
                os.remove(fpath)
                print(f"  -> Archive removed")
        elif status == 'no_skins':
            print(f"  -> No skin changes (music/stage only)")
            if not args.no_cleanup:
                os.remove(fpath)
                print(f"  -> Archive removed")
        elif status == 'unsupported':
            print(f"  -> Moved to _unsupported/")
        else:
            print(f"  -> ERROR (keeping archive)")

    # Merge
    print(f"\n{'=' * 60}")
    print("Merging into character folders...")
    merged_count = merge_extracted(extracted_dir, merged_dir)
    print(f"  Added {merged_count} new files to merged/")

    # Deduplicate
    print("\nDeduplicating...")
    total, unique, removed = deduplicate(merged_dir)
    print(f"  Total: {total}, Unique: {unique}, Removed: {removed}")

    # Final report
    print(f"\n{'=' * 60}")
    print("FINAL REPORT")
    print(f"{'=' * 60}")
    print(f"Processed: {len(items)} items")
    print(f"  Skins extracted: {len(results['ok'])} mixes, {total_rendered} sprites")
    print(f"  No skin changes: {len(results['no_skins'])} mixes (music/stage only)")
    print(f"  Errors:          {len(results['error'])} mixes")
    print(f"  Unsupported:     {len(results['unsupported'])} items")

    if results['error']:
        print(f"\nFailed mixes (archives kept for review):")
        for name in results['error']:
            print(f"  {name}")

    remaining = sum(1 for d in os.listdir(merged_dir) if os.path.isdir(os.path.join(merged_dir, d))
        for f in os.listdir(os.path.join(merged_dir, d)) if f.endswith('.png'))
    print(f"\nTotal unique skins in merged/: {remaining}")


if __name__ == "__main__":
    main()

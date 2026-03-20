#!/usr/bin/env python3
"""
MvC2 Skin Processor — Unified tool for converting skins to standardized sprite sheets.

Accepts Dreamcast CDI disc images, PS3 PKG packages, NAOMI arcade ROMs (.bin),
individual PNG skin images, or folders containing any mix of the above.

Usage:
    python mvc2_skin_processor.py                      # process ./queue -> ./output
    python mvc2_skin_processor.py skin.png             # auto-detect character
    python mvc2_skin_processor.py skin.png -c Venom    # force character
    python mvc2_skin_processor.py mix.cdi -o my_out    # custom output dir
    python mvc2_skin_processor.py arcade.bin           # process NAOMI arcade ROM
    python mvc2_skin_processor.py --clean              # process and remove successful inputs

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
from mvc2_extract.naomi import validate_naomi_rom, parse_naomi_palettes

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
    """SHA256 first 8 hex chars of the palette rows used by the image.

    Index 0 is always transparent, so zero it out before hashing to
    avoid junk values causing different hashes for identical palettes.
    """
    pal = img.getpalette()
    if not pal:
        return "00000000"
    # Zero out index 0 (transparent) so junk values don't affect hash
    pal[0] = pal[1] = pal[2] = 0
    pixels = img.tobytes()
    max_idx = max(pixels) if pixels else 0
    num_rows = (max_idx // 16) + 1
    pal_bytes = bytes(pal[:num_rows * 48])
    return hashlib.sha256(pal_bytes).hexdigest()[:8]


def make_descriptor(filename):
    """Build a clean descriptor from an input filename."""
    stem = os.path.splitext(os.path.basename(filename))[0]
    # Strip leading ISO-like timestamp prefixes (e.g. 2026-03-19T18-38-50)
    stem = re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}[_-]?', '', stem)
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

def detect_character(img_w, img_h, dim_lookup, bases, img=None):
    """Detect character from image dimensions.

    Returns (char_id, scale_factor) or (None, None) with suggestions printed.
    Falls back to content bounding-box matching for images with padding
    (e.g. PalMod exports with black backgrounds and embedded swatches).
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

    # Content bounding-box fallback: strip padding, match inner dimensions
    if img is not None:
        arr = np.array(img)
        mask = arr > 0
        rows_any = np.any(mask, axis=1)
        cols_any = np.any(mask, axis=0)
        if np.any(rows_any) and np.any(cols_any):
            rmin, rmax = np.where(rows_any)[0][[0, -1]]
            cmin, cmax = np.where(cols_any)[0][[0, -1]]
            content_w = cmax - cmin + 1
            content_h = rmax - rmin + 1

            candidates = []
            for scale in [1, 2, 3, 4]:
                for cid, b in bases.items():
                    bw, bh = b['width'] * scale, b['height'] * scale
                    diff = abs(content_w - bw) + abs(content_h - bh)
                    # Penalize higher scales to prefer 2x over 3x/4x
                    weighted = diff + max(0, scale - 2) * 20
                    if weighted <= 50:
                        candidates.append((weighted, cid, scale))

            if candidates:
                candidates.sort()
                best_weighted = candidates[0][0]
                # If best match is exact or clearly dominant, use it directly
                close = [c for c in candidates if c[0] <= best_weighted + 20]
                if len(close) == 1 or best_weighted == 0:
                    return candidates[0][1], candidates[0][2]

                # Multiple close candidates — use pixel structure (IoU) to disambiguate
                content = arr[rmin:rmax+1, cmin:cmax+1]
                best_iou, best_cid, best_scale = -1, None, None
                for _, cid, scale in close:
                    b = bases[cid]
                    bw, bh = b['width'], b['height']
                    bp = b['pixels']
                    if len(bp.shape) == 1:
                        bp = bp.reshape(bh, bw)
                    # Downscale content to base dimensions
                    content_img = Image.fromarray(content)
                    down = np.array(content_img.resize((bw, bh), Image.NEAREST))
                    # IoU of non-zero pixel masks
                    cm = down > 0
                    bm = bp > 0
                    inter = np.sum(cm & bm)
                    union = np.sum(cm | bm)
                    iou = inter / union if union > 0 else 0
                    if iou > best_iou:
                        best_iou = iou
                        best_cid = cid
                        best_scale = scale
                if best_cid is not None:
                    return best_cid, best_scale

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


def _normalize(name):
    """Strip all punctuation/whitespace for comparison."""
    return name.lower().replace(' ', '').replace('_', '').replace('-', '').replace('.', '')


def resolve_character_name(name):
    """Resolve a user-provided character name to a char_id, or None."""
    norm = _normalize(name)
    for cid in PLAYABLE_CHARS:
        if norm == _normalize(CHARACTERS[cid]):
            return cid
        if norm == _normalize(safe_name(CHARACTERS[cid])):
            return cid
    return None


def suggest_characters(name):
    """Print characters starting with the same letter as a failed lookup."""
    first = _normalize(name)[0] if name else ''
    matches = []
    for cid in PLAYABLE_CHARS:
        cname = CHARACTERS[cid]
        if _normalize(cname).startswith(first):
            sname = safe_name(cname)
            matches.append(f"{sname:<25} ({cname})" if sname != cname else cname)
    if matches:
        print(f"  Characters starting with '{first.upper()}':")
        for m in sorted(matches):
            print(f"    {m}")
    else:
        # Show all if no letter match
        all_names = []
        for cid in PLAYABLE_CHARS:
            cname = CHARACTERS[cid]
            sname = safe_name(cname)
            all_names.append(f"{sname:<25} ({cname})" if sname != cname else cname)
        print(f"  Valid character names:")
        for n in sorted(all_names):
            print(f"    {n}")


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


def process_naomi(bin_path, bases, out_dir):
    """Process a NAOMI arcade ROM (.bin) file."""
    descriptor = make_descriptor(os.path.basename(bin_path))
    print(f"  Validating NAOMI ROM...")

    with open(bin_path, "rb") as f:
        rom_data = f.read()

    valid, msg = validate_naomi_rom(rom_data)
    if not valid:
        print(f"  ERROR: {msg}")
        return 0

    print(f"  {msg} ({len(rom_data):,} bytes)")
    print(f"  Extracting palettes for {len(PLAYABLE_CHARS)} characters...")

    rendered = 0
    chars_done = 0
    for cid in PLAYABLE_CHARS:
        if cid not in bases:
            continue

        palettes = parse_naomi_palettes(rom_data, cid)
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

        chars_done += 1

    print(f"  Rendered {rendered} skins across {chars_done} characters")
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
            print(f"  ERROR: '{force_character}' not recognized")
            suggest_characters(force_character)
            img.close()
            return 0
        scale = 1  # doesn't matter, we use the palette not pixels
        print(f"  Forced character: {CHARACTERS[cid]}")
    else:
        cid, scale = detect_character(img_w, img_h, dim_lookup, bases, img=img)
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

    if num_rows > 1 and max_idx < 16:
        # Multi-row character but input only has single-row indices — palette
        # was flattened/quantized so row mapping is lost. Skip to avoid garbled output.
        print(f"  WARNING: {os.path.basename(png_path)} has flattened palette "
              f"(max index {max_idx}, needs {num_rows} rows) — skipping")
        return 0

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
    """Route input to the appropriate processor.

    Returns (total_rendered, succeeded_files) where succeeded_files is a list
    of absolute paths to input files that produced at least one output.
    """
    succeeded = []

    if os.path.isfile(input_path):
        ext = os.path.splitext(input_path)[1].lower()
        name = os.path.basename(input_path)
        print(f"\n[{name}]")

        if ext == '.cdi':
            count = process_cdi(input_path, bases, out_dir)
        elif ext == '.pkg':
            count = process_pkg(input_path, bases, out_dir)
        elif ext == '.bin':
            # .bin is generic — validate NAOMI header before processing
            with open(input_path, "rb") as f:
                magic = f.read(5)
            if magic == b"NAOMI":
                count = process_naomi(input_path, bases, out_dir)
            else:
                print(f"  Not a NAOMI ROM (magic={magic!r}), skipping")
                return 0, []
        elif ext == '.png':
            count = process_image(input_path, bases, dim_lookup, out_dir, force_character)
        else:
            print(f"  Unsupported file type: {ext}")
            return 0, []

        if count > 0:
            succeeded.append(os.path.abspath(input_path))
        return count, succeeded

    elif os.path.isdir(input_path):
        total = 0
        for item in sorted(os.listdir(input_path)):
            item_path = os.path.join(input_path, item)
            if os.path.isfile(item_path):
                ext = os.path.splitext(item)[1].lower()
                if ext in ('.cdi', '.pkg', '.bin', '.png'):
                    count, files = process_input(item_path, bases, dim_lookup, out_dir, force_character)
                    total += count
                    succeeded.extend(files)
            elif os.path.isdir(item_path):
                # Recurse into subdirectories
                count, files = process_input(item_path, bases, dim_lookup, out_dir, force_character)
                total += count
                succeeded.extend(files)
        return total, succeeded

    else:
        print(f"ERROR: {input_path} not found")
        return 0, []


def validate_output(out_dir):
    """Validate that output directory contains valid indexed-color PNGs.

    Returns (valid_count, invalid_files) where invalid_files is a list of
    paths to output files that failed validation.
    """
    valid = 0
    invalid = []
    for root, _dirs, files in os.walk(out_dir):
        for f in files:
            if not f.lower().endswith('.png'):
                continue
            fpath = os.path.join(root, f)
            try:
                img = Image.open(fpath)
                if img.mode != 'P':
                    invalid.append(fpath)
                else:
                    img.load()  # force full decode
                    valid += 1
                img.close()
            except Exception:
                invalid.append(fpath)
    return valid, invalid


def clean_succeeded_inputs(succeeded_files):
    """Remove successfully processed input files and clean empty parent dirs."""
    removed = 0
    dirs_to_check = set()
    for fpath in succeeded_files:
        if os.path.isfile(fpath):
            dirs_to_check.add(os.path.dirname(fpath))
            os.remove(fpath)
            removed += 1
            print(f"  Removed: {os.path.basename(fpath)}")

    # Clean empty directories (bottom-up), but never remove the input root
    for d in sorted(dirs_to_check, key=len, reverse=True):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass

    return removed


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_input = os.path.join(script_dir, "queue")
    default_output = os.path.join(script_dir, "output")

    parser = argparse.ArgumentParser(
        description="MvC2 Skin Processor — Convert CDI, PKG, or PNG skins to standardized sprites",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                            Process ./queue -> ./output
  %(prog)s mix.cdi                    Process a Dreamcast disc image
  %(prog)s arcade.bin                 Process a NAOMI arcade ROM
  %(prog)s skin.png -c Venom          Process with forced character
  %(prog)s ./my_skins/ -o ./my_out    Custom input and output directories
  %(prog)s --clean                    Process and remove successful inputs
""")
    parser.add_argument("input", nargs="?", default=default_input,
                        help="CDI file, PKG file, NAOMI ROM (.bin), PNG image, or folder (default: ./queue)")
    parser.add_argument("-o", "--output", default=default_output,
                        help="Output directory (default: ./output)")
    parser.add_argument("-c", "--character",
                        help="Force character for PNG input (e.g. 'Venom', 'Storm')")
    parser.add_argument("--clean", action="store_true",
                        help="Remove successfully converted input files after validating output")
    args = parser.parse_args()

    print("=" * 60)
    print("MvC2 Skin Processor")
    print("=" * 60)

    # Load sprite bases
    print("Loading sprite bases...")
    bases = load_sprite_bases()
    dim_lookup = build_dimension_lookup(bases)
    print(f"  {len(bases)} characters loaded")

    in_path = os.path.abspath(args.input)
    out_dir = os.path.abspath(args.output)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Input:  {in_path}")
    print(f"Output: {out_dir}")

    # Process input
    rendered, succeeded = process_input(
        in_path, bases, dim_lookup, out_dir, args.character
    )

    print(f"\n{'=' * 60}")
    print(f"Done! {rendered} skins rendered to {out_dir}")

    # --clean: validate output then remove successful inputs
    if args.clean and succeeded:
        print(f"\nValidating output...")
        valid, invalid = validate_output(out_dir)
        print(f"  {valid} valid output files")
        if invalid:
            print(f"  {len(invalid)} invalid output files — skipping clean")
            for f in invalid:
                print(f"    INVALID: {f}")
        else:
            print(f"\nCleaning {len(succeeded)} successful input(s)...")
            removed = clean_succeeded_inputs(succeeded)
            remaining = []
            if os.path.isdir(in_path):
                for root, _dirs, files in os.walk(in_path):
                    for f in files:
                        ext = os.path.splitext(f)[1].lower()
                        if ext in ('.cdi', '.pkg', '.bin', '.png'):
                            remaining.append(os.path.join(root, f))
            if remaining:
                print(f"\n  {len(remaining)} input(s) remaining (failed/unsupported):")
                for f in remaining:
                    print(f"    {os.path.relpath(f, in_path)}")
            else:
                print(f"  All inputs processed successfully")
    elif args.clean and not succeeded:
        print(f"\nNo successful conversions — nothing to clean")


if __name__ == "__main__":
    main()

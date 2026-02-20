"""
MvC2 palette extraction and parsing.

Extracts PL??_DAT.BIN files from ISO filesystem data and parses their
ARGB4444 little-endian palette entries.

Palette format:
  - PL{hex_id}_DAT.BIN files on the disc contain all color data per character
  - Header at 0x08: uint32 LE offset to palette data start
  - Header at 0x0C: uint32 LE offset to palette data end
  - Palette entries: ARGB4444, 16-bit little-endian
  - 16 colors per palette (32 bytes per palette)
  - Index 0 is always transparent (alpha forced to 0)
  - 6 buttons x 8 slots = 48 core palettes; button N uses palette N*8
"""
import struct
import io


def extract_palette_files(iso_data, quiet=False):
    """Extract PL??_DAT.BIN palette files from ISO9660 data.

    Args:
        iso_data: Raw ISO9660 filesystem bytes.
        quiet: If True, suppress output.

    Returns:
        dict: Mapping of character_id (int) -> raw palette file bytes.
    """
    import pycdlib

    def log(msg):
        if not quiet:
            print(msg)

    iso = pycdlib.PyCdlib()
    iso.open_fp(io.BytesIO(iso_data))

    palettes = {}
    for dirpath, _, filenames in iso.walk(iso_path="/"):
        for fname in filenames:
            clean = fname.split(";")[0]
            if clean.startswith("PL") and clean.endswith("_DAT.BIN") and len(clean) == 12:
                hex_id = clean[2:4]
                try:
                    char_id = int(hex_id, 16)
                except ValueError:
                    continue
                full_path = f"{dirpath}/{fname}" if dirpath != "/" else f"/{fname}"
                data = io.BytesIO()
                iso.get_file_from_iso_fp(data, iso_path=full_path)
                palettes[char_id] = data.getvalue()

    iso.close()
    log(f"  Found {len(palettes)} palette files")
    return palettes


def parse_palettes(data):
    """Parse ARGB4444 palettes from raw PL??_DAT.BIN bytes.

    Args:
        data: Raw bytes of a PL??_DAT.BIN file.

    Returns:
        list: List of palettes, each a list of 16 (R, G, B, A) tuples.
    """
    pal_start = struct.unpack_from("<I", data, 0x08)[0]
    pal_end = struct.unpack_from("<I", data, 0x0C)[0]
    raw = data[pal_start:pal_end]

    num_uint16 = len(raw) // 2
    colors = struct.unpack(f"<{num_uint16}H", raw)

    palettes = []
    for p in range(num_uint16 // 16):
        palette = []
        for c in range(16):
            c16 = colors[p * 16 + c]
            a = ((c16 >> 12) & 0xF) * 17
            r = ((c16 >> 8) & 0xF) * 17
            g = ((c16 >> 4) & 0xF) * 17
            b = (c16 & 0xF) * 17
            # Index 0 is always transparent
            rgba = (r, g, b, 0) if c == 0 else (r, g, b, a)
            palette.append(rgba)
        palettes.append(palette)
    return palettes

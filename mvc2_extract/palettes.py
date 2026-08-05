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


def extract_palette_files_manual(iso_data, lba_offset=0, quiet=False):
    """Extract PL??_DAT.BIN files by walking ISO9660 directory records directly.

    Fallback for discs pycdlib rejects (e.g. mismatched little/big-endian path
    tables in hand-patched mixes). Directory records store absolute disc LBAs,
    so pass the data track's start LBA (see cdi.get_track_start_lba).

    Returns:
        dict: Mapping of character_id (int) -> raw palette file bytes,
        or None if no palette files were found.
    """
    sector_size = 2048

    def log(msg):
        if not quiet:
            print(msg)

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
        offset = (dir_lba - lba_offset) * sector_size
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
                walk_dirs(parse_dir(lba, size))
            elif not is_dir and filename.startswith("PL") and filename.endswith("_DAT.BIN"):
                try:
                    char_id = int(filename[2:4], 16)
                except ValueError:
                    continue
                file_offset = (lba - lba_offset) * sector_size
                if 0 <= file_offset and file_offset + size <= len(iso_data):
                    palettes[char_id] = iso_data[file_offset:file_offset + size]

    walk_dirs(parse_dir(root_lba, root_size))
    log(f"  Found {len(palettes)} palette files (manual ISO parse)")
    return palettes if palettes else None


def parse_palettes(data):
    """Parse ARGB4444 palettes from raw PL??_DAT.BIN bytes.

    Args:
        data: Raw bytes of a PL??_DAT.BIN file.

    Returns:
        list: List of palettes, each a list of 16 (R, G, B, A) tuples.
    """
    pal_start = struct.unpack_from("<I", data, 0x08)[0]
    pal_end = struct.unpack_from("<I", data, 0x0C)[0]
    # Some builds (e.g. MVC2 TE-based mixes) place the palette block at the
    # tail of the file and leave a stale end offset below the start; the block
    # runs to EOF in that layout.
    if pal_end <= pal_start:
        pal_end = len(data)
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

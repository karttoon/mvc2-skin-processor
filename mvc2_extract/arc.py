"""
MvC2 Steam ARC file palette extraction.

Reads palette data from a Steam game_50.arc file. The ARC contains a zlib-
compressed ROM; once decompressed, palettes sit at fixed offsets identical in
format to the NAOMI version (ARGB4444 little-endian, 16 colors per palette,
32 bytes per palette) but at different addresses.

Offset table sourced from PalMod's baseSteamShiftTable (Game_MVC2_A.cpp).
ARC parsing logic ported from mvc2-randomizer/mvc2_data/steam.py.
"""
import struct
import zlib


# Steam palette base offsets — from PalMod's baseSteamShiftTable (Game_MVC2_A.cpp)
# Each value is the ROM offset of the LP "Main" palette for that character.
STEAM_PALETTE_OFFSETS = {
    0x00: 0x82CC60,    # Ryu
    0x01: 0x9049A0,    # Zangief
    0x02: 0x997A20,    # Guile
    0x03: 0xA5D320,    # Morrigan
    0x04: 0xB77B60,    # Anakaris
    0x05: 0xC4FE20,    # Strider
    0x06: 0xD36F80,    # Cyclops
    0x07: 0xE32D20,    # Wolverine
    0x08: 0xF34940,    # Psylocke
    0x09: 0x10201E0,   # Iceman
    0x0A: 0x1107480,   # Rogue
    0x0B: 0x11F58A0,   # Captain America
    0x0C: 0x12D44C0,   # Spider-Man
    0x0D: 0x13EEF80,   # Hulk
    0x0E: 0x1512E20,   # Venom
    0x0F: 0x1625A40,   # Doctor Doom
    0x10: 0x173D7C0,   # Tron Bonne
    0x11: 0x1819B40,   # Jill
    0x12: 0x19176C0,   # Hayato
    0x13: 0x1A20D00,   # Ruby Heart
    0x14: 0x1B3A8C0,   # SonSon
    0x15: 0x1C53DE0,   # Amingo
    0x16: 0x1D48E20,   # Marrow
    0x17: 0x1E55220,   # Cable
    0x18: 0x1F41800,   # Abyss (Form 1)
    0x19: 0x1FCED80,   # Abyss (Form 2)
    0x1A: 0x20A8DA0,   # Abyss (Form 3)
    0x1B: 0x2129520,   # Chun-Li
    0x1C: 0x21BC920,   # Mega Man
    0x1D: 0x2230DE0,   # Roll
    0x1E: 0x22BA5A0,   # Akuma
    0x1F: 0x23D4AA0,   # BB Hood
    0x20: 0x24FC100,   # Felicia
    0x21: 0x25691A0,   # Charlie Nash
    0x22: 0x2630380,   # Sakura
    0x23: 0x267EB60,   # Dan
    0x24: 0x271EBE0,   # Cammy
    0x25: 0x27D5F20,   # Dhalsim
    0x26: 0x285B0A0,   # M. Bison
    0x27: 0x28E1AE0,   # Ken
    0x28: 0x29CB740,   # Gambit
    0x29: 0x2AF8860,   # Juggernaut
    0x2A: 0x2C070E0,   # Storm
    0x2B: 0x2D089E0,   # Sabretooth
    0x2C: 0x2E1FF80,   # Magneto
    0x2D: 0x2F08560,   # Shuma-Gorath
    0x2E: 0x30091A0,   # War Machine
    0x2F: 0x3124EC0,   # Silver Samurai
    0x30: 0x3223B60,   # Omega Red
    0x31: 0x3339480,   # Spiral
    0x32: 0x34652C0,   # Colossus
    0x33: 0x35683A0,   # Iron Man
    0x34: 0x368E840,   # Sentinel
    0x35: 0x37B5EE0,   # Blackheart
    0x36: 0x38A4C80,   # Thanos
    0x37: 0x3989460,   # Jin
    0x38: 0x3A793A0,   # Captain Commando
    0x39: 0x3B78B00,   # Wolverine (Bone Claw)
    0x3A: 0x3BDEB20,   # Servbot
}

# ARC file constants
ARC_MAGIC = b"ARC\x00"
ARC_VERSION = 7
ARC_DATA_OFFSET = 0x8000

# ROM validation
IBIS_MAGIC = b"IBIS"
EXPECTED_ROM_SIZE = 112_635_968


def read_arc(arc_path):
    """Read a game_50.arc file and decompress the ROM.

    Returns a mutable bytearray of the decompressed ROM data.
    """
    with open(arc_path, "rb") as f:
        header = f.read(8)

    magic = header[:4]
    if magic != ARC_MAGIC:
        raise ValueError(f"Not an ARC file (magic: {magic!r}, expected {ARC_MAGIC!r})")

    version = struct.unpack_from("<H", header, 4)[0]
    if version != ARC_VERSION:
        raise ValueError(f"Unexpected ARC version {version} (expected {ARC_VERSION})")

    with open(arc_path, "rb") as f:
        f.seek(ARC_DATA_OFFSET)
        compressed = f.read()

    return bytearray(zlib.decompress(compressed))


def validate_arc_rom(rom):
    """Check that decompressed ROM data looks correct.

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if len(rom) < 4:
        return False, "Decompressed data too small"
    if rom[:4] != IBIS_MAGIC:
        return False, f"ROM missing IBIS header (got {rom[:4]!r})"
    if len(rom) != EXPECTED_ROM_SIZE:
        return False, f"Unexpected ROM size {len(rom):,} (expected {EXPECTED_ROM_SIZE:,})"
    return True, "Valid Steam MvC2 ROM"


def parse_arc_palettes(rom_data, char_id):
    """Extract palettes for one character from decompressed ARC ROM data.

    Reads 48 core palettes (6 buttons x 8 slots) at the character's fixed
    ROM offset. Format: ARGB4444 little-endian, 16 colors per palette.

    Args:
        rom_data: Decompressed ROM bytes/bytearray.
        char_id: Character ID (0x00-0x3A).

    Returns:
        list: List of 48 palettes, each a list of 16 (R, G, B, A) tuples.
              Compatible with parse_naomi_palettes() output format.
    """
    if char_id not in STEAM_PALETTE_OFFSETS:
        return []

    base_offset = STEAM_PALETTE_OFFSETS[char_id]
    num_palettes = 48  # 6 buttons x 8 slots
    bytes_needed = num_palettes * 32  # 32 bytes per palette

    if base_offset + bytes_needed > len(rom_data):
        return []

    raw = rom_data[base_offset:base_offset + bytes_needed]
    num_uint16 = len(raw) // 2
    colors = struct.unpack(f"<{num_uint16}H", raw)

    palettes = []
    for p in range(num_palettes):
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

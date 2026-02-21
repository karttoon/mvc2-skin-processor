"""
MvC2 NAOMI arcade ROM palette extraction.

Reads palette data from a NAOMI arcade ROM (.bin) at fixed offsets derived
from PalMod's open-source offset tables (github.com/Preppy/PalMod).

Palette format is identical to Dreamcast: ARGB4444 little-endian, 16 colors
per palette, 32 bytes per palette. The difference is storage: Dreamcast uses
separate PL??_DAT.BIN files with header pointers, while the NAOMI ROM stores
palettes at fixed offsets in the ROM file itself.

Character IDs (0x00-0x3A) are the same CPS2 unit IDs used across all versions.
"""
import struct


# Base palette offset for each character in the NAOMI ROM.
# Source: PalMod MVC2_A_DEF.h (palmod/Game/MVC2_A_DEF.h)
# Each character has 48 core palettes (6 buttons × 8 slots) starting here.
NAOMI_PALETTE_OFFSETS = {
    0x00: 0x0260A9C0,  # Ryu
    0x01: 0x026E2240,  # Zangief
    0x02: 0x02775160,  # Guile
    0x03: 0x0283A360,  # Morrigan
    0x04: 0x02954600,  # Anakaris
    0x05: 0x02A2C5E0,  # Strider
    0x06: 0x02B13440,  # Cyclops
    0x07: 0x02C0EBA0,  # Wolverine
    0x08: 0x02D104E0,  # Psylocke
    0x09: 0x02DFB5C0,  # Iceman
    0x0A: 0x02EE2140,  # Rogue
    0x0B: 0x02FD03E0,  # Captain America
    0x0C: 0x030AE9C0,  # Spider-Man
    0x0D: 0x031C9400,  # Hulk
    0x0E: 0x032ED120,  # Venom
    0x0F: 0x033FFA40,  # Doctor Doom
    0x10: 0x035175C0,  # Tron Bonne
    0x11: 0x035F3160,  # Jill
    0x12: 0x036F0740,  # Hayato
    0x13: 0x037F9CE0,  # Ruby Heart
    0x14: 0x039136C0,  # SonSon
    0x15: 0x03A2C760,  # Amingo
    0x16: 0x03B214A0,  # Marrow
    0x17: 0x03C2D5A0,  # Cable
    0x18: 0x03D19480,  # Abyss (Form 1)
    0x19: 0x03DA68E0,  # Abyss (Form 2)
    0x1A: 0x03E80560,  # Abyss (Form 3)
    0x1B: 0x03F00960,  # Chun-Li
    0x1C: 0x03F93960,  # Mega Man
    0x1D: 0x04007740,  # Roll
    0x1E: 0x04090CE0,  # Akuma
    0x1F: 0x041AAE60,  # BB Hood
    0x20: 0x042D2080,  # Felicia
    0x21: 0x0433F100,  # Charlie Nash
    0x22: 0x04405B60,  # Sakura
    0x23: 0x044540C0,  # Dan
    0x24: 0x044F3B80,  # Cammy
    0x25: 0x045AA820,  # Dhalsim
    0x26: 0x0462F340,  # M. Bison
    0x27: 0x046B5660,  # Ken
    0x28: 0x0479EC80,  # Gambit
    0x29: 0x048CB760,  # Juggernaut
    0x2A: 0x049D9E80,  # Storm
    0x2B: 0x04ADB360,  # Sabretooth
    0x2C: 0x04BF21C0,  # Magneto
    0x2D: 0x04CDA620,  # Shuma-Gorath
    0x2E: 0x04DDAB80,  # War Machine
    0x2F: 0x04EF6120,  # Silver Samurai
    0x30: 0x04FF4940,  # Omega Red
    0x31: 0x05109FA0,  # Spiral
    0x32: 0x05235A60,  # Colossus
    0x33: 0x053384C0,  # Iron Man
    0x34: 0x0545E420,  # Sentinel
    0x35: 0x05585400,  # Blackheart
    0x36: 0x05673E40,  # Thanos
    0x37: 0x05758480,  # Jin
    0x38: 0x05847EC0,  # Captain Commando
    0x39: 0x059472A0,  # Wolverine (Bone Claw)
    0x3A: 0x059ACDC0,  # Servbot
}

EXPECTED_ROM_SIZE = 0x0889B600  # 143,242,752 bytes


def validate_naomi_rom(data):
    """Check whether raw bytes are a valid NAOMI MvC2 arcade ROM.

    Returns:
        tuple: (is_valid: bool, message: str)
    """
    if len(data) < 0x200:
        return False, "File too small for NAOMI header"
    if data[:5] != b"NAOMI":
        return False, f"Bad magic: expected NAOMI, got {data[:5]!r}"
    if len(data) != EXPECTED_ROM_SIZE:
        return False, f"Unexpected size: {len(data):,} (expected {EXPECTED_ROM_SIZE:,})"
    return True, "Valid NAOMI MvC2 ROM"


def parse_naomi_palettes(rom_data, char_id):
    """Extract palettes for one character from NAOMI ROM data.

    Reads 48 core palettes (6 buttons × 8 slots) at the character's fixed
    ROM offset. Format: ARGB4444 little-endian, 16 colors per palette.

    Args:
        rom_data: Full ROM file bytes.
        char_id: Character ID (0x00-0x3A).

    Returns:
        list: List of 48 palettes, each a list of 16 (R, G, B, A) tuples.
              Compatible with mvc2_extract.palettes.parse_palettes() output.
    """
    if char_id not in NAOMI_PALETTE_OFFSETS:
        return []

    base_offset = NAOMI_PALETTE_OFFSETS[char_id]
    num_palettes = 48  # 6 buttons × 8 slots
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

"""MvC2 character ID/name mappings and constants."""

# Full MvC2 roster — CPS2 unit IDs map 1:1 to character IDs
CHARACTERS = {
    0x00: "Ryu", 0x01: "Zangief", 0x02: "Guile", 0x03: "Morrigan",
    0x04: "Anakaris", 0x05: "Strider", 0x06: "Cyclops", 0x07: "Wolverine",
    0x08: "Psylocke", 0x09: "Iceman", 0x0A: "Rogue", 0x0B: "Captain America",
    0x0C: "Spider-Man", 0x0D: "Hulk", 0x0E: "Venom", 0x0F: "Dr. Doom",
    0x10: "Tron Bonne", 0x11: "Jill", 0x12: "Hayato", 0x13: "Ruby Heart",
    0x14: "SonSon", 0x15: "Amingo", 0x16: "Marrow", 0x17: "Cable",
    0x18: "Abyss (Form 1)", 0x19: "Abyss (Form 2)", 0x1A: "Abyss (Form 3)",
    0x1B: "Chun-Li", 0x1C: "Megaman", 0x1D: "Roll", 0x1E: "Akuma",
    0x1F: "B.B. Hood", 0x20: "Felicia", 0x21: "Charlie", 0x22: "Sakura",
    0x23: "Dan", 0x24: "Cammy", 0x25: "Dhalsim", 0x26: "M.Bison",
    0x27: "Ken", 0x28: "Gambit", 0x29: "Juggernaut", 0x2A: "Storm",
    0x2B: "Sabretooth", 0x2C: "Magneto", 0x2D: "Shuma-Gorath",
    0x2E: "War Machine", 0x2F: "Silver Samurai", 0x30: "Omega Red",
    0x31: "Spiral", 0x32: "Colossus", 0x33: "Iron Man", 0x34: "Sentinel",
    0x35: "Blackheart", 0x36: "Thanos", 0x37: "Jin",
    0x38: "Captain Commando", 0x39: "Wolverine (Bone Claw)", 0x3A: "Servbot",
}

# Button color slots — each character has 6 palette variants
BUTTON_NAMES = ["LP", "LK", "HP", "HK", "A1", "A2"]

# Non-boss playable characters (excludes Abyss forms)
PLAYABLE_CHARS = [c for c in sorted(CHARACTERS.keys()) if c not in (0x18, 0x19, 0x1A)]


# Number of palette rows used in the composite sprite.
# Most characters use 1 row (body only). Characters with accessories
# (shields, projectiles, animals, etc.) use additional rows.
# Determined from zachd.com/palmod Default skin palette entry counts.
PALETTE_ROWS = {
    0x05: 3,   # Strider — Ouroboros orbs, tiger, eagle
    0x06: 2,   # Cyclops — optic blast
    0x07: 2,   # Wolverine — claws/effects
    0x0B: 2,   # Captain America — shield
    0x0C: 2,   # Spider-Man — web
    0x0E: 2,   # Venom — symbiote effects
    0x10: 4,   # Tron Bonne — Servbot helpers
    0x11: 2,   # Jill — zombie/effects
    0x12: 3,   # Hayato — plasma sword/motorcycle
    0x14: 7,   # SonSon — staff/monkey
    0x17: 2,   # Cable — viper beam
    0x1B: 2,   # Chun-Li — ki effects
    # Note: M.Bison Default has max_idx=255 but only 64 stray pixels, not a real composite
    0x2B: 3,   # Sabretooth — effects
    0x2D: 2,   # Shuma-Gorath — eye/tentacles
    0x34: 3,   # Sentinel — drones/rockets
    0x39: 2,   # Wolverine (Bone Claw) — claws/effects
    0x3A: 7,   # Servbot — servbot army
}


# Which PalMod slots (within each button's 8-slot block) map to which pixel row.
# Index in list = pixel row in composite sprite.
# Value = slot offset within the button's 8-slot block.
# PalMod slot names: Main=0, 02=1, 03=2, 04=3, 05=4, 06=5, 07=6, 08=7
# Example: Sentinel [0, 1, 3] means pixel row 0 → slot Main, row 1 → slot 02, row 2 → slot 04.
# Characters not listed here default to consecutive [0, 1, 2, ...num_rows-1].
PALETTE_SLOT_MAP = {
    0x17: [0, 3],           # Cable — main/04
    0x0B: [0, 1],           # Captain America — main/02
    0x1B: [0, 1],           # Chun-Li — main/02
    0x06: [0, 1],           # Cyclops — main/02
    0x12: [0, 1, 4],        # Hayato — main/02/05
    0x11: [0, 1],           # Jill — main/02
    0x2B: [0, 1, 2],        # Sabretooth — main/02/03
    0x34: [0, 1, 3],        # Sentinel — main/02/04
    0x3A: [0, 1, 2, 3, 4, 5, 6],  # Servbot — main/02/03/04/05/06/07
    0x2D: [0, 1],           # Shuma-Gorath — main/02
    0x14: [0, 1, 2, 3, 4, 5, 6],  # SonSon — main/02/03/04/05/06/07
    0x0C: [0, 2],           # Spider-Man — main/03
    0x05: [0, 1, 2],        # Strider — main/02/03
    0x10: [0, 1, 3, 4],     # Tron Bonne — main/02/04/05
    0x0E: [0, 1],           # Venom — main/02
    0x07: [0, 1],           # Wolverine — main/02
    0x39: [0, 1],           # Wolverine (Bone Claw) — main/02
}


def palette_slot_map(char_id):
    """Get the palette slot mapping for a character.

    Returns a list where index = pixel row, value = slot offset within button's 8-slot block.
    """
    if char_id in PALETTE_SLOT_MAP:
        return PALETTE_SLOT_MAP[char_id]
    # Default: consecutive slots [0, 1, 2, ...]
    return list(range(palette_rows(char_id)))


def palette_rows(char_id):
    """Get the number of palette rows for a character (1 = body only)."""
    return PALETTE_ROWS.get(char_id, 1)


def safe_name(name):
    """Convert character name to filesystem-safe format."""
    return (name.replace(" ", "_").replace(".", "")
            .replace("-", "_").replace("(", "").replace(")", ""))

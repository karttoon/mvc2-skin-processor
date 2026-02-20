# MvC2 Skin Processor

A self-contained tool for extracting and standardizing Marvel vs. Capcom 2 character skin sprite sheets from Dreamcast disc images (CDI), PS3 packages (PKG), or individual PalMod PNG palette swaps.

Point it at a CDI, PKG, PNG, or a folder of mixed inputs and it outputs standardized indexed-color sprite sheets organized by character.

## Features

- **Multi-format input** — Dreamcast CDI disc images, PS3 PKG packages, individual PNG skins, or folders containing any mix
- **Auto-detection** — Identifies characters from sprite dimensions (exact match or integer scale factors 2x/3x/4x/6x/8x)
- **Composite rendering** — Correctly handles multi-row palette characters (Sentinel, Strider, Cable, etc.) with proper PalMod slot mapping
- **Self-contained** — All 56 character base sprites bundled as compressed .npz files (~1.4 MB). No external dependencies like PalMod's img2020.dat
- **Standardized output** — Indexed-color PNGs with consistent naming: `CharName_hash_descriptor.png`

## Requirements

- Python 3.10+
- Pillow >= 10.0
- pycdlib >= 1.14
- NumPy

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/mvc2-skin-processor.git
cd mvc2-skin-processor
pip install -r requirements.txt
```

## Usage

```bash
# Process a Dreamcast CDI disc image (56 chars x 6 buttons = 336 skins)
python mvc2_skin_processor.py game.cdi

# Process a PS3 PKG package
python mvc2_skin_processor.py colors.pkg

# Process a single PNG skin (auto-detects character from dimensions)
python mvc2_skin_processor.py skin.png

# Force character when auto-detection can't match (non-standard dimensions)
python mvc2_skin_processor.py skin.png --character Venom

# Custom output directory
python mvc2_skin_processor.py game.cdi -o ./my_output

# Process an entire folder of mixed inputs
python mvc2_skin_processor.py ./my_skins_folder/
```

### Review & Curate

After extraction, review the output with the built-in gallery:

```bash
# Launch the gallery to triage skins (Y=keep, N=skip)
python gallery.py ./output

# Apply verdicts — remove all skins marked 'skip'
python apply_verdicts.py ./output
python apply_verdicts.py ./output --dry-run  # preview first

# Merge curated skins into your personal collection
python merge_palettes.py ./output ./my_palettes
python merge_palettes.py ./output ./my_palettes --skip-defaults  # auto-skip default game palettes
```

### Test with included samples

```bash
# Auto-detect M.Bison from exact dimensions (757x252)
python mvc2_skin_processor.py tests/sample_mbison.png

# Auto-detect Storm at 3x scale (2256x825 -> 752x275)
python mvc2_skin_processor.py tests/sample_storm_3x.png
```

## Output

```
output/
  Ryu/
    Ryu_a1b2c3d4_MixName-LP.png
    Ryu_e5f6a7b8_MixName-LK.png
    ...
  Storm/
    Storm_1a2b3c4d_MixName-LP.png
    ...
  ... (56 characters)
```

All PNGs are **indexed-color** (mode P) with the palette embedded. Index 0 is always transparent. Output filenames follow the convention `CharName_hash_descriptor.png` where the hash is derived from the palette data.

## How It Works

### Input Processing

1. **CDI** — Parses the DiscJuggler disc image, extracts the ISO9660 filesystem, and reads all `PL{xx}_DAT.BIN` palette files (one per character). Renders all 56 characters x 6 button colors.

2. **PKG** — Extracts PS3 package contents to a temp directory, locates palette files, and processes identically to CDI.

3. **PNG** — Reads the indexed-color palette from the input image, auto-detects the character by matching dimensions against the 56 bundled base sprites (supports exact match and integer downscale factors). Applies the input palette to the canonical base sprite for standardized output.

### Palette Format (ARGB4444)

- Each color: 16-bit little-endian, 4 bits per channel (Alpha, Red, Green, Blue)
- 16 colors per palette row, 32 bytes per palette row
- Index 0 is always forced transparent
- 6 button colors (LP, LK, HP, HK, A1, A2), each spaced 8 palette slots apart
- Multi-row characters (e.g., Sentinel body + flames) use non-consecutive PalMod slot indices

### Composite Characters

17 characters use multiple palette rows for accessories, weapons, or effects:

| Character | Rows | Parts |
|-----------|------|-------|
| Sentinel | 3 | Body, drones, flames |
| Strider | 3 | Body, animals, scarf |
| Tron Bonne | 4 | Tron, Servbot, mech parts |
| SonSon | 7 | Body, staff, monkey, effects |
| Servbot | 7 | Body, limbs, accessories |
| Cable | 2 | Body, gun glow |
| Captain America | 2 | Body, shield |
| Spider-Man | 2 | Body, webs |
| And others... | | |

Each row maps to a specific PalMod slot index (not necessarily consecutive), handled automatically by the processor.

### Bundled Sprite Bases

The `sprite_bases/` directory contains all 56 character base sprites as compressed NumPy archives (.npz). These were extracted from PalMod's img2020.dat sprite database and include:

- `pixels` — 8bpp indexed pixel data (uint8 2D array)
- `width`, `height` — Canonical sprite dimensions
- `num_rows` — Number of palette rows used
- `default_palette` — Stock palette for fallback rendering

Total size: ~1.4 MB for all 56 characters.

## Complete Workflow

```
1. Extract:    python mvc2_skin_processor.py game.cdi -o ./output
2. Review:     python gallery.py ./output         (Y/N each skin in browser)
3. Curate:     python apply_verdicts.py ./output   (delete skins marked 'skip')
4. Merge:      python merge_palettes.py ./output ./my_palettes --skip-defaults
```

## Project Structure

```
mvc2_skin_processor.py    # Main extraction tool (CDI/PKG/PNG → standardized sprites)
ps3_pkg_extract.py        # PS3 PKG decryption/extraction
gallery.py                # Browser-based skin triage gallery
apply_verdicts.py         # Apply gallery verdicts (remove skipped skins)
merge_palettes.py         # Merge curated skins into personal collection
default_hashes.json       # SHA256 hashes of all 336 default game palettes
mvc2_extract/             # Core library (CDI parsing, palettes, sprites, renderer)
sprite_bases/             # 56 character base sprites (.npz, ~1.4 MB total)
composite_bases/          # 17 multi-row character composites (.npz)
tools/                    # Build/maintenance tools (not part of user workflow)
```

## Where to Get MvC2 Mixes

Community palette mods ("mixes") for MvC2 are archived at:

- **[Biggs' MvC2 Mix Archive](https://drive.google.com/drive/folders/1z6QTVkJU8qNV2Ce9EbsHuVb9KX810VRS)** — Comprehensive collection of Dreamcast CDI and PS3 PKG mixes from the community

## Full Roster (56 Characters)

**Capcom:** Akuma, Amingo, Anakaris, B.B. Hood, Cammy, Captain Commando, Charlie, Chun-Li, Dan, Dhalsim, Felicia, Guile, Hayato, Jill, Jin, Ken, M. Bison, Mega Man, Morrigan, Roll, Ruby Heart, Ryu, Sakura, Servbot, SonSon, Strider, Tron Bonne, Zangief

**Marvel:** Blackheart, Cable, Captain America, Colossus, Cyclops, Doctor Doom, Gambit, Hulk, Iceman, Iron Man, Juggernaut, Magneto, Marrow, Omega Red, Psylocke, Rogue, Sabretooth, Sentinel, Shuma-Gorath, Silver Samurai, Spider-Man, Spiral, Storm, Thanos, Venom, War Machine, Wolverine, Wolverine (Bone Claw)

## Acknowledgments

- **[PalMod](https://github.com/palmod/palmod)** by Preppy — Sprite database and palette editing tool
- **[cdirip](https://github.com/jozip/cdirip)** by DeXT/Lawrence Williams — CDI format reference (GPL)
- The MvC2 modding community for palette mixes and documentation

## License

MIT

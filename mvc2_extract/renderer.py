"""
Sprite renderer — applies palettes to indexed pixel data and outputs PNGs.

Produces indexed-color (mode P / PNG type 3) images that PalMod can import.
Index 0 is treated as transparent via the PNG tRNS chunk.

Supports both single-row (16 color) and multi-row composite palettes for
characters with accessories (shields, projectiles, animals, etc.).
"""
from PIL import Image
import numpy as np


def render_sprite(pixels, w, h, palette):
    """Render an indexed-color sprite as a Pillow Image (single palette row).

    Args:
        pixels: Raw 8bpp indexed pixel data (bytes).
        w: Sprite width in pixels.
        h: Sprite height in pixels.
        palette: List of 16 (R, G, B, A) tuples.

    Returns:
        PIL.Image: Indexed-color (mode P) image with embedded palette.
    """
    img = Image.new("P", (w, h))

    # Build 256-entry RGB palette (Pillow P mode needs flat [R,G,B,...] list)
    pal_flat = []
    for i in range(256):
        if i < len(palette):
            r, g, b, a = palette[i]
            pal_flat.extend([r, g, b])
        else:
            pal_flat.extend([0, 0, 0])
    img.putpalette(pal_flat)

    # Write raw indexed pixel data
    img.putdata(list(pixels[:w * h]))

    # Mark index 0 as transparent (PNG tRNS chunk)
    img.info["transparency"] = 0
    return img


def render_composite(base_pixels, w, h, palettes, num_rows, default_palette=None):
    """Render a composite sprite with multi-row palette.

    Uses a composite base sprite (from zachd.com Default) that has pixel indices
    spanning multiple palette rows. Body pixels (0-15) use the mix palette,
    accessory pixels (16+) use either the mix's accessory palettes or defaults.

    Args:
        base_pixels: 2D numpy array (h, w) of palette indices from the composite base.
        w: Sprite width.
        h: Sprite height.
        palettes: List of palette rows, each a list of 16 (R, G, B, A) tuples.
                  palettes[0] = body, palettes[1] = first accessory row, etc.
        num_rows: Number of palette rows expected.
        default_palette: Fallback palette as numpy array of (R, G, B) for indices
                        that aren't covered by the provided palettes. Shape: (N, 3).

    Returns:
        PIL.Image: Indexed-color (mode P) image with num_rows * 16 palette entries.
    """
    img = Image.new("P", (w, h))

    # Build flat RGB palette with num_rows * 16 entries
    total_colors = num_rows * 16
    pal_flat = []
    for i in range(256):
        if i < total_colors and i // 16 < len(palettes) and i % 16 < len(palettes[i // 16]):
            r, g, b, a = palettes[i // 16][i % 16]
            pal_flat.extend([r, g, b])
        elif default_palette is not None and i < len(default_palette):
            r, g, b = int(default_palette[i][0]), int(default_palette[i][1]), int(default_palette[i][2])
            pal_flat.extend([r, g, b])
        else:
            pal_flat.extend([0, 0, 0])
    img.putpalette(pal_flat)

    # Write the composite pixel data
    img.putdata(base_pixels.flatten().tolist())

    # Mark index 0 as transparent
    img.info["transparency"] = 0
    return img

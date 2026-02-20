"""
PalMod img2020.dat sprite archive parser.

Reads the CPS2 section of PalMod's img2020.dat file to extract MvC2
character sprite sheets. Each sprite is 8bpp indexed pixel data, often
ZLib-compressed.

img2020.dat format:
  - 6-byte header: 2B unknown, 4x 1B fields (last = num_sections)
  - Section table: num_sections x 7 bytes (1B id, 2B count, 4B offset)
  - CPS2 section (id=3) contains MvC2 sprites as a linked list
  - Each image record: 16-byte header + compressed pixel data
    - Bytes 0-1: unit_id (character ID)
    - Byte 2: image_id (0 = main sprite sheet)
    - Bytes 3-4: width
    - Bytes 5-6: height
    - Byte 7: compression (0=raw, 3=zlib)
    - Bytes 8-11: compressed data size
    - Bytes 12-15: next record offset (0 = end)
"""
import struct
import zlib


class ImgDat:
    """Parser for PalMod's img2020.dat sprite archive."""

    def __init__(self, filepath):
        self.filepath = filepath
        self.sprites = {}  # unit_id -> [(img_id, w, h, comp, data_size, data_offset)]
        self._parse()

    def _parse(self):
        with open(self.filepath, "rb") as f:
            _, _, _, _, num_sections = struct.unpack("<HBBBB", f.read(6))
            cps2_offset = cps2_count = 0
            for _ in range(num_sections):
                sec_id, num_imgs, first_offset = struct.unpack("<BHI", f.read(7))
                if sec_id == 3:  # CPS2 section
                    cps2_offset, cps2_count = first_offset, num_imgs

            # Walk the linked list of image records
            current, count = cps2_offset, 0
            while current and count < cps2_count + 10:
                f.seek(current)
                hdr = f.read(16)
                if len(hdr) < 16:
                    break
                uid = struct.unpack_from("<H", hdr, 0)[0]
                iid = hdr[2]
                w = struct.unpack_from("<H", hdr, 3)[0]
                h = struct.unpack_from("<H", hdr, 5)[0]
                comp = hdr[7]
                dsz = struct.unpack_from("<I", hdr, 8)[0]
                nxt = struct.unpack_from("<I", hdr, 12)[0]
                doff = f.tell()
                self.sprites.setdefault(uid, []).append((iid, w, h, comp, dsz, doff))
                count += 1
                current = nxt

    def get_sprite(self, unit_id, img_id=0):
        """Get decompressed pixel data for a sprite.

        Args:
            unit_id: Character/unit ID (e.g. 0x00 for Ryu).
            img_id: Image index (0 = main character sprite sheet).

        Returns:
            tuple: (pixels_bytes, width, height) or None if not found.
        """
        if unit_id not in self.sprites:
            return None
        match = [s for s in self.sprites[unit_id] if s[0] == img_id]
        if not match:
            return None

        _, w, h, comp, dsz, doff = match[0]
        with open(self.filepath, "rb") as f:
            f.seek(doff)
            raw = f.read(dsz)

        exp = w * h
        if comp == 3:  # ZLib compressed
            try:
                pixels = zlib.decompressobj(-15).decompress(raw)
            except zlib.error:
                pixels = zlib.decompress(raw)
        elif comp == 0:  # Raw/uncompressed
            pixels = raw
        else:
            return None

        return pixels[:exp], w, h

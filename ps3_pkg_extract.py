#!/usr/bin/env python3
"""
Extract files from PS3 .pkg archives.

Supports both debug (SHA1-XOR) and retail (AES-128-CTR) PS3 PKG files.
Used by the MvC2 skin extraction pipeline to get palette files from PS3 mixes.

Usage:
    python ps3_pkg_extract.py <pkg_file> <output_dir> [--filter PATTERN]
    python ps3_pkg_extract.py <pkg_file> --list   # List contents without extracting

References:
    - https://www.psdevwiki.com/ps3/PKG_files
    - https://github.com/windsurfer1122/PSN_get_pkg_info
"""
import os
import sys
import struct
import hashlib
import argparse
import fnmatch

# Try pycryptodomex first (installed as Cryptodome), fall back to pycryptodome
try:
    from Cryptodome.Cipher import AES
except ImportError:
    try:
        from Crypto.Cipher import AES
    except ImportError:
        AES = None  # Only needed for retail PKGs


# PS3 retail AES key
PS3_AES_KEY = bytes.fromhex("2E7B71D7C9C9A14EA3221F188828B8F8")


def decrypt_debug(data, digest, offset_in_data=0):
    """Decrypt data from a debug (non-finalized) PS3 PKG.

    Uses SHA1-based stream cipher:
    - Build a 0x40-byte key from the header digest
    - SHA1 hash it to get 20 bytes of keystream
    - XOR first 16 bytes against data, 16 bytes at a time
    - Increment counter at key[0x38] and re-hash every 16 bytes

    The counter must account for position in the data stream,
    so reading at offset N starts with counter = N // 16.
    """
    # Build the 0x40-byte key buffer
    key = bytearray(0x40)
    key[0x00:0x08] = digest[0:8]
    key[0x08:0x10] = digest[0:8]
    key[0x10:0x18] = digest[8:16]
    key[0x18:0x20] = digest[8:16]
    # key[0x20:0x40] stays zeroed (non-arcade)

    result = bytearray(len(data))
    data_len = len(data)
    pos = 0  # position in data array

    # Current absolute position in the stream
    abs_pos = offset_in_data

    while pos < data_len:
        block = abs_pos // 16
        byte_in_block = abs_pos % 16

        # Generate keystream for this block
        key[0x38:0x40] = struct.pack(">Q", block)
        bfr = hashlib.sha1(bytes(key)).digest()

        # XOR bytes in this block
        while byte_in_block < 16 and pos < data_len:
            result[pos] = data[pos] ^ bfr[byte_in_block]
            pos += 1
            byte_in_block += 1
            abs_pos += 1

    return bytes(result)


def decrypt_retail(data, iv_bytes, offset_in_data):
    """Decrypt data from a retail (finalized) PS3 PKG.

    Uses AES-128-CTR with a fixed key and the IV from the PKG header.
    The counter starts at IV + (byte_offset / 16).
    """
    if AES is None:
        raise RuntimeError("pycryptodomex or pycryptodome required for retail PKG decryption. "
                          "Install with: pip install pycryptodomex")

    # Convert IV to integer and add the block offset
    iv_int = int.from_bytes(iv_bytes, 'big')
    initial_counter = iv_int + (offset_in_data // 16)

    # AES-128-CTR
    cipher = AES.new(PS3_AES_KEY, AES.MODE_CTR,
                     nonce=b'',
                     initial_value=initial_counter.to_bytes(16, 'big'))
    return cipher.decrypt(data)


class PKGExtractor:
    """Extract files from a PS3 .pkg archive."""

    def __init__(self, pkg_path):
        self.pkg_path = pkg_path
        self.items = []
        self._parse_header()
        self._parse_items()

    def _parse_header(self):
        """Parse the PKG header."""
        with open(self.pkg_path, 'rb') as f:
            header = f.read(0xC0)

        magic = header[0:4]
        if magic != b'\x7fPKG':
            raise ValueError(f"Not a PKG file (magic: {magic})")

        self.revision = struct.unpack('>H', header[4:6])[0]
        self.pkg_type = struct.unpack('>H', header[6:8])[0]
        self.item_count = struct.unpack('>I', header[20:24])[0]
        self.total_size = struct.unpack('>Q', header[24:32])[0]
        self.data_offset = struct.unpack('>Q', header[32:40])[0]
        self.data_size = struct.unpack('>Q', header[40:48])[0]
        self.content_id = header[48:84].decode('ascii', errors='replace').rstrip('\x00')
        self.digest = header[0x60:0x70]
        self.iv = header[0x70:0x80]

        self.is_debug = (self.revision & 0x8000) == 0
        self.is_ps3 = (self.pkg_type == 1)

    def _read_encrypted(self, offset_in_data, size):
        """Read and decrypt data from the encrypted section."""
        with open(self.pkg_path, 'rb') as f:
            f.seek(self.data_offset + offset_in_data)
            encrypted = f.read(size)

        if self.is_debug:
            return decrypt_debug(encrypted, self.digest, offset_in_data)
        else:
            return decrypt_retail(encrypted, self.iv, offset_in_data)

    def _parse_items(self):
        """Parse the item table from the encrypted data section."""
        # Item table is at the start of the encrypted data
        table_size = self.item_count * 0x20
        table_data = self._read_encrypted(0, table_size)

        self.items = []
        for i in range(self.item_count):
            entry = table_data[i * 0x20:(i + 1) * 0x20]
            fname_offset = struct.unpack('>I', entry[0:4])[0]
            fname_size = struct.unpack('>I', entry[4:8])[0]
            file_offset = struct.unpack('>Q', entry[8:16])[0]
            file_size = struct.unpack('>Q', entry[16:24])[0]
            flags = struct.unpack('>I', entry[24:28])[0]

            item_type = flags & 0xFF
            is_dir = (item_type == 0x04)

            self.items.append({
                'fname_offset': fname_offset,
                'fname_size': fname_size,
                'file_offset': file_offset,
                'file_size': file_size,
                'flags': flags,
                'is_dir': is_dir,
                'name': None,  # Resolved lazily
            })

        # Resolve filenames
        self._resolve_filenames()

    def _resolve_filenames(self):
        """Decrypt and resolve all item filenames."""
        if not self.items:
            return

        # Find the range of filename data we need to decrypt
        min_offset = min(item['fname_offset'] for item in self.items)
        max_end = max(item['fname_offset'] + item['fname_size'] for item in self.items)

        # Decrypt the filename block
        fname_data = self._read_encrypted(min_offset, max_end - min_offset)

        for item in self.items:
            start = item['fname_offset'] - min_offset
            end = start + item['fname_size']
            item['name'] = fname_data[start:end].decode('utf-8', errors='replace').rstrip('\x00')

    def list_files(self):
        """Return list of (name, size, is_dir) tuples."""
        return [(item['name'], item['file_size'], item['is_dir']) for item in self.items]

    def extract_file(self, item):
        """Extract a single file item and return its data."""
        if item['is_dir'] or item['file_size'] == 0:
            return b''

        # For large files, decrypt in chunks to manage memory
        CHUNK_SIZE = 1024 * 1024  # 1MB chunks

        if item['file_size'] <= CHUNK_SIZE:
            return self._read_encrypted(item['file_offset'], item['file_size'])

        # Chunk-based decryption for large files
        result = bytearray()
        remaining = item['file_size']
        offset = item['file_offset']

        while remaining > 0:
            chunk_size = min(CHUNK_SIZE, remaining)
            chunk = self._read_encrypted(offset, chunk_size)
            result.extend(chunk)
            offset += chunk_size
            remaining -= chunk_size

        return bytes(result)

    def extract_all(self, output_dir, pattern=None, verbose=True):
        """Extract all (or filtered) files to output_dir.

        Args:
            output_dir: Directory to extract to
            pattern: Optional glob pattern to filter filenames (e.g., "PL*_DAT.BIN")
            verbose: Print progress

        Returns:
            List of extracted file paths
        """
        extracted = []

        for i, item in enumerate(self.items):
            name = item['name']

            if pattern and not fnmatch.fnmatch(os.path.basename(name), pattern):
                continue

            if item['is_dir']:
                dir_path = os.path.join(output_dir, name)
                os.makedirs(dir_path, exist_ok=True)
                continue

            # Create parent directories
            file_path = os.path.join(output_dir, name)
            os.makedirs(os.path.dirname(file_path), exist_ok=True)

            if verbose:
                size_str = f"{item['file_size']/1024:.0f}KB" if item['file_size'] < 1024*1024 else f"{item['file_size']/1024/1024:.1f}MB"
                print(f"  [{i+1}/{self.item_count}] {name} ({size_str})")

            data = self.extract_file(item)
            with open(file_path, 'wb') as f:
                f.write(data)

            extracted.append(file_path)

        return extracted

    def extract_palette_files(self, output_dir, verbose=True):
        """Extract only MvC2 palette files (PL??_DAT.BIN or PL??PAK.BIN).

        Returns:
            List of extracted file paths
        """
        extracted = []

        for item in self.items:
            name = os.path.basename(item['name'])
            name_upper = name.upper()
            # Match both DC-style and PS3-style palette filenames (case-insensitive)
            is_palette = False
            if name_upper.startswith("PL") and name_upper.endswith("_DAT.BIN"):
                is_palette = True
            elif name_upper.startswith("PL") and name_upper.endswith("PAK.BIN"):
                is_palette = True

            if not is_palette:
                continue

            file_path = os.path.join(output_dir, name)

            if verbose:
                print(f"  Extracting palette: {name} ({item['file_size']} bytes)")

            data = self.extract_file(item)
            with open(file_path, 'wb') as f:
                f.write(data)

            extracted.append(file_path)

        return extracted


def main():
    parser = argparse.ArgumentParser(description="Extract files from PS3 .pkg archives")
    parser.add_argument("pkg_file", help="Path to .pkg file")
    parser.add_argument("output_dir", nargs='?', help="Output directory (required for extraction)")
    parser.add_argument("--list", action="store_true", help="List contents without extracting")
    parser.add_argument("--filter", help="Glob pattern to filter files (e.g., 'PL*')")
    parser.add_argument("--palettes-only", action="store_true", help="Extract only palette files")
    args = parser.parse_args()

    print(f"Opening: {args.pkg_file}")
    pkg = PKGExtractor(args.pkg_file)

    print(f"Content ID: {pkg.content_id}")
    print(f"Type: {'Debug' if pkg.is_debug else 'Retail'} {'PS3' if pkg.is_ps3 else 'Other'}")
    print(f"Items: {pkg.item_count}")
    print(f"Data size: {pkg.data_size / 1024 / 1024:.1f} MB")
    print()

    if args.list:
        files = pkg.list_files()
        dirs = sum(1 for _, _, d in files if d)
        regular = sum(1 for _, _, d in files if not d)

        for name, size, is_dir in files:
            # Sanitize for console output
            safe_name = name.encode('ascii', errors='replace').decode('ascii')
            if is_dir:
                print(f"  [DIR]  {safe_name}")
            else:
                size_str = f"{size/1024:.0f}KB" if size < 1024*1024 else f"{size/1024/1024:.1f}MB"
                print(f"  {size_str:>8s}  {safe_name}")

        print(f"\n{dirs} directories, {regular} files")
        return

    if not args.output_dir:
        print("Error: output_dir required for extraction (or use --list)")
        sys.exit(1)

    os.makedirs(args.output_dir, exist_ok=True)

    if args.palettes_only:
        extracted = pkg.extract_palette_files(args.output_dir)
    else:
        extracted = pkg.extract_all(args.output_dir, pattern=args.filter)

    print(f"\nExtracted {len(extracted)} files to {args.output_dir}")


if __name__ == "__main__":
    main()

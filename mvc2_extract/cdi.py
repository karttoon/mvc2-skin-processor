"""
Pure Python CDI (DiscJuggler) disc image parser.

Parses CDI format, extracts the data track, and returns raw ISO9660 data.
Based on cdirip 0.6.4 source code (DeXT/Lawrence Williams, GPL).

CDI format overview:
  - Version & header offset stored at the last 8 bytes of file
  - Versions: V2 (0x80000004), V3 (0x80000005), V3.5 (0x80000006)
  - Header contains session/track descriptors
  - Each track has: mode, sector size, length, pregap, start LBA
  - Sector sizes: 2048 (raw ISO), 2336 (Mode2 subheader+data), 2352 (full)
  - ISO data extracted by stripping sector headers to get 2048-byte payloads
"""
import struct

CDI_V2 = 0x80000004
CDI_V3 = 0x80000005
CDI_V35 = 0x80000006

TRACK_START_MARK = bytes([0, 0, 0x01, 0, 0, 0, 0xFF, 0xFF, 0xFF, 0xFF])


def parse_cdi(cdi_path, quiet=False):
    """Parse a CDI disc image and return raw ISO data from the main data track.

    Args:
        cdi_path: Path to CDI disc image file.
        quiet: If True, suppress progress output.

    Returns:
        bytes: Raw ISO9660 filesystem data (2048 bytes/sector).

    Raises:
        ValueError: If CDI version is unsupported or no data track found.
    """
    def log(msg):
        if not quiet:
            print(msg)

    with open(cdi_path, "rb") as f:
        # Read version info from end of file
        f.seek(0, 2)
        file_length = f.tell()
        f.seek(file_length - 8)
        version = struct.unpack("<I", f.read(4))[0]
        header_offset = struct.unpack("<I", f.read(4))[0]

        if version == CDI_V35:
            f.seek(file_length - header_offset)
        elif version in (CDI_V2, CDI_V3):
            f.seek(header_offset)
        else:
            raise ValueError(f"Unsupported CDI version: 0x{version:08X}")

        version_name = {CDI_V2: "2.0", CDI_V3: "3.0", CDI_V35: "3.5"}[version]
        log(f"  CDI version: {version_name}")

        num_sessions = struct.unpack("<H", f.read(2))[0]
        track_position = 0
        data_track = None

        for _ in range(num_sessions):
            num_tracks = struct.unpack("<H", f.read(2))[0]
            for _ in range(num_tracks):
                pos = track_position

                # Parse track descriptor (matching cdirip's CDI_read_track)
                temp = struct.unpack("<I", f.read(4))[0]
                if temp != 0:
                    f.seek(8, 1)  # extra data (DJ 3.00.780+)

                f.read(10)  # track start mark 1
                f.read(10)  # track start mark 2
                f.seek(4, 1)
                fn_len = struct.unpack("B", f.read(1))[0]
                f.seek(fn_len, 1)
                f.seek(11, 1)
                f.seek(4, 1)
                f.seek(4, 1)

                temp = struct.unpack("<I", f.read(4))[0]
                if temp == 0x80000000:
                    f.seek(8, 1)  # DJ4

                f.seek(2, 1)
                pregap = struct.unpack("<I", f.read(4))[0]
                length = struct.unpack("<i", f.read(4))[0]  # signed
                f.seek(6, 1)
                mode = struct.unpack("<I", f.read(4))[0]
                f.seek(12, 1)
                start_lba = struct.unpack("<I", f.read(4))[0]
                total_length = struct.unpack("<I", f.read(4))[0]
                f.seek(16, 1)
                ss_val = struct.unpack("<I", f.read(4))[0]
                sector_size = {0: 2048, 1: 2336, 2: 2352}[ss_val]

                f.seek(29, 1)
                if version != CDI_V2:
                    f.seek(5, 1)
                    temp = struct.unpack("<I", f.read(4))[0]
                    if temp == 0xffffffff:
                        f.seek(78, 1)  # extra data (DJ 3.00.780+)

                track_position += total_length * sector_size

                # Keep the best data track (Mode > 0, substantial length)
                if mode > 0 and length > 1000:
                    data_track = (pos, pregap, length, mode, sector_size, total_length)

            # Session footer
            f.seek(4, 1)
            f.seek(8, 1)
            if version != CDI_V2:
                f.seek(1, 1)

        if not data_track:
            raise ValueError("No data track found in CDI image")

        pos, pregap, length, mode, sector_size, total_length = data_track
        mode_name = {0: "Audio", 1: "Mode1", 2: "Mode2"}[mode]
        log(f"  Data track: {mode_name}/{sector_size}, {length} sectors")

        # Extract ISO data — strip sector headers to get 2048-byte payloads
        start = pos + pregap * sector_size
        f.seek(start)
        iso_data = bytearray()
        for _ in range(length):
            sector = f.read(sector_size)
            if len(sector) < sector_size:
                break
            if sector_size == 2048:
                iso_data.extend(sector)
            elif sector_size == 2336:
                iso_data.extend(sector[8:8 + 2048])
            elif sector_size == 2352:
                hdr = 24 if mode == 2 else 16
                iso_data.extend(sector[hdr:hdr + 2048])

        log(f"  ISO data: {len(iso_data) / 1024 / 1024:.0f} MB")
        return bytes(iso_data)

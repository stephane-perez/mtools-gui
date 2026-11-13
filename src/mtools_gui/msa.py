"""Reader and writer for Atari MSA floppy images.

MSA stores a ten-byte, big-endian geometry header followed by one length-
prefixed block per track and side. Track data is either raw sectors or
byte-run encoded and can be passed to mtools after decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import struct
import tempfile


MAGIC = 0x0E0F
SECTOR_SIZE = 512
_ESCAPE = 0xE5
_MAX_TRACKS = 200
_MAX_SECTORS_PER_TRACK = 30
_MAX_IMAGE_SIZE = 64 * 1024 * 1024


class MsaError(ValueError):
    """Invalid or unsupported MSA image."""


@dataclass(frozen=True)
class MsaGeometry:
    sectors_per_track: int
    sides: int
    first_track: int
    last_track: int

    @property
    def track_count(self) -> int:
        return self.last_track - self.first_track + 1

    @property
    def image_size(self) -> int:
        """Size of the complete raw image, including any omitted leading tracks."""
        return (self.last_track + 1) * self.sides * self.sectors_per_track * SECTOR_SIZE

    @property
    def recorded_size(self) -> int:
        return self.track_count * self.sides * self.sectors_per_track * SECTOR_SIZE


def _validate_geometry(geometry: MsaGeometry) -> None:
    if not 1 <= geometry.sectors_per_track <= _MAX_SECTORS_PER_TRACK:
        raise MsaError("Unsupported MSA sectors-per-track value")
    if geometry.sides not in (1, 2):
        raise MsaError("Unsupported MSA side count")
    if not 0 <= geometry.first_track <= geometry.last_track < _MAX_TRACKS:
        raise MsaError("Unsupported MSA track range")
    if geometry.image_size > _MAX_IMAGE_SIZE:
        raise MsaError("MSA image is too large")


def _decode_track(encoded: bytes, expected_size: int) -> bytes:
    if len(encoded) == expected_size:
        return encoded
    if len(encoded) > expected_size:
        raise MsaError("MSA track block is larger than an uncompressed track")

    decoded = bytearray()
    pos = 0
    while pos < len(encoded):
        value = encoded[pos]
        pos += 1
        if value != _ESCAPE:
            decoded.append(value)
            continue
        if pos + 3 > len(encoded):
            raise MsaError("Truncated MSA run-length sequence")
        repeated = encoded[pos]
        count = int.from_bytes(encoded[pos + 1:pos + 3], "big")
        pos += 3
        if count == 0 or len(decoded) + count > expected_size:
            raise MsaError("Invalid MSA run length")
        decoded.extend(bytes((repeated,)) * count)

    if len(decoded) != expected_size:
        raise MsaError(
            f"Invalid decoded track size: expected {expected_size}, got {len(decoded)}"
        )
    return bytes(decoded)


def decode_msa(payload: bytes) -> tuple[bytes, MsaGeometry]:
    """Decode an MSA byte string into a complete raw sector image and geometry."""
    if len(payload) < 10:
        raise MsaError("MSA header is truncated")

    magic, sectors, sides_minus_one, first, last = struct.unpack(">5H", payload[:10])
    if magic != MAGIC:
        raise MsaError("Not an Atari MSA image")
    if sides_minus_one not in (0, 1):
        raise MsaError("Unsupported MSA side count")

    geometry = MsaGeometry(sectors, sides_minus_one + 1, first, last)
    _validate_geometry(geometry)

    pos = 10
    track_size = sectors * SECTOR_SIZE
    # MSA may omit tracks before first_track. They occupy zero-filled space
    # in the corresponding raw ST image so all subsequent sector offsets
    # remain absolute.
    tracks = bytearray(first * geometry.sides * track_size)
    for _track in range(geometry.track_count):
        for _side in range(geometry.sides):
            if pos + 2 > len(payload):
                raise MsaError("MSA track table is truncated")
            encoded_size = int.from_bytes(payload[pos:pos + 2], "big")
            pos += 2
            if encoded_size == 0 or pos + encoded_size > len(payload):
                raise MsaError("MSA track data is truncated")
            encoded = payload[pos:pos + encoded_size]
            pos += encoded_size
            tracks.extend(_decode_track(encoded, track_size))

    if pos != len(payload):
        raise MsaError("Unexpected trailing data in MSA image")
    return bytes(tracks), geometry


def _encode_track(raw: bytes) -> bytes:
    encoded = bytearray()
    pos = 0
    while pos < len(raw):
        value = raw[pos]
        end = pos + 1
        while end < len(raw) and raw[end] == value and end - pos < 0xFFFF:
            end += 1
        count = end - pos
        # E5 is always escaped, including runs of one; other values are
        # encoded only for runs of four bytes or more.
        if value == _ESCAPE or count >= 4:
            encoded.extend((_ESCAPE, value))
            encoded.extend(count.to_bytes(2, "big"))
        else:
            encoded.extend(raw[pos:end])
        pos = end
    return bytes(encoded)


def encode_msa(raw: bytes, geometry: MsaGeometry) -> bytes:
    """Encode a complete raw sector image as MSA, preserving its geometry."""
    _validate_geometry(geometry)
    if len(raw) != geometry.image_size:
        raise MsaError(
            f"Raw image size does not match geometry ({len(raw)} != {geometry.image_size})"
        )

    header = struct.pack(
        ">5H", MAGIC, geometry.sectors_per_track, geometry.sides - 1,
        geometry.first_track, geometry.last_track,
    )
    result = bytearray(header)
    track_size = geometry.sectors_per_track * SECTOR_SIZE
    track_stride = geometry.sides * track_size
    for track_number in range(geometry.first_track, geometry.last_track + 1):
        track_offset = track_number * track_stride
        for side in range(geometry.sides):
            start = track_offset + side * track_size
            track = raw[start:start + track_size]
            compressed = _encode_track(track)
            stored = compressed if len(compressed) < len(track) else track
            if len(stored) > 0xFFFF:
                raise MsaError("Encoded MSA track exceeds the format limit")
            result.extend(len(stored).to_bytes(2, "big"))
            result.extend(stored)
    return bytes(result)


def write_msa_atomic(
    path: str | os.PathLike[str], raw: bytes, geometry: MsaGeometry
) -> None:
    """Replace an MSA image atomically, leaving the original intact on failure."""
    destination = Path(path)
    encoded = encode_msa(raw, geometry)
    mode = destination.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise

"""Reader and writer for Atari MSA floppy images.

MSA stores one track/side at a time, optionally using a small byte-run
encoding. The decoded payload is a raw, sector-oriented FAT image suitable
for mtools.
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
        return self.track_count * self.sides * self.sectors_per_track * SECTOR_SIZE


def _decode_track(encoded: bytes, expected_size: int) -> bytes:
    if len(encoded) == expected_size:
        return encoded

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
    """Decode an MSA byte string into raw sectors and its disk geometry."""
    if len(payload) < 10:
        raise MsaError("MSA header is truncated")
    magic, sectors, sides_minus_one, first, last = struct.unpack(">HBBBB", payload[:6])
    if magic != MAGIC:
        raise MsaError("Not an Atari MSA image")
    sides = sides_minus_one + 1
    if not 1 <= sectors <= 255 or sides not in (1, 2) or first > last:
        raise MsaError("Unsupported MSA disk geometry")

    geometry = MsaGeometry(sectors, sides, first, last)
    if geometry.image_size > 64 * 1024 * 1024:
        raise MsaError("MSA image is too large")

    pos = 6
    tracks = bytearray()
    track_size = sectors * SECTOR_SIZE
    for _track in range(geometry.track_count):
        for _side in range(sides):
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
        if value == _ESCAPE or count >= 4:
            encoded.extend((_ESCAPE, value))
            encoded.extend(count.to_bytes(2, "big"))
        else:
            encoded.extend(raw[pos:end])
        pos = end
    return bytes(encoded)


def encode_msa(raw: bytes, geometry: MsaGeometry) -> bytes:
    """Encode raw sectors as MSA, preserving the original geometry."""
    if len(raw) != geometry.image_size:
        raise MsaError(
            f"Raw image size does not match geometry ({len(raw)} != {geometry.image_size})"
        )
    header = struct.pack(
        ">HBBBB", MAGIC, geometry.sectors_per_track, geometry.sides - 1,
        geometry.first_track, geometry.last_track,
    )
    result = bytearray(header)
    track_size = geometry.sectors_per_track * SECTOR_SIZE
    pos = 0
    for _track in range(geometry.track_count):
        for _side in range(geometry.sides):
            track = raw[pos:pos + track_size]
            pos += track_size
            compressed = _encode_track(track)
            stored = compressed if len(compressed) < len(track) else track
            if len(stored) > 0xFFFF:
                raise MsaError("Encoded MSA track exceeds the format limit")
            result.extend(len(stored).to_bytes(2, "big"))
            result.extend(stored)
    return bytes(result)


def write_msa_atomic(path: str | os.PathLike[str], raw: bytes, geometry: MsaGeometry) -> None:
    """Replace an MSA image atomically, leaving the original intact on failure."""
    destination = Path(path)
    encoded = encode_msa(raw, geometry)
    mode = destination.stat().st_mode & 0o777
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp",
                                     dir=destination.parent)
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

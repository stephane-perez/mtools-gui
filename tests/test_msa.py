from __future__ import annotations

import os
import struct

import pytest

from mtools_gui.msa import (
    MAGIC,
    MsaError,
    MsaGeometry,
    decode_msa,
    encode_msa,
    write_msa_atomic,
)


def _raw_track(sectors=9, value=0):
    return bytes((value,)) * (sectors * 512)


def test_round_trip_uncompressed_tracks_and_ten_byte_header():
    geometry = MsaGeometry(sectors_per_track=9, sides=2, first_track=0, last_track=1)
    raw = bytes((index * 37 + 11) % 256 for index in range(geometry.image_size))

    encoded = encode_msa(raw, geometry)
    decoded, decoded_geometry = decode_msa(encoded)

    assert len(encoded[:10]) == 10
    assert struct.unpack(">5H", encoded[:10]) == (MAGIC, 9, 1, 0, 1)
    assert decoded == raw
    assert decoded_geometry == geometry


def test_round_trip_compressed_tracks_and_nonzero_first_track():
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=2, last_track=3)
    track_size = geometry.sectors_per_track * 512
    raw = (b"\x00" * (2 * track_size)) + (b"\x11" * track_size) + (b"\x22" * track_size)

    encoded = encode_msa(raw, geometry)
    decoded, decoded_geometry = decode_msa(encoded)

    assert len(encoded) < len(raw)
    assert decoded == raw
    assert decoded_geometry == geometry


@pytest.mark.parametrize("value,count", [(0xE5, 1), (0xE5, 2), (0xE5, 3), (0xAA, 4)])
def test_rle_escape_and_minimum_run_lengths(value, count):
    geometry = MsaGeometry(9, 1, 0, 0)
    raw = bytearray(_raw_track())
    raw[:count] = bytes([value]) * count

    decoded, _ = decode_msa(encode_msa(bytes(raw), geometry))
    assert decoded == bytes(raw)


def test_decode_rejects_truncated_header():
    with pytest.raises(MsaError, match="header is truncated"):
        decode_msa(b"\x0e\x0f" + b"\x00" * 7)


def test_decode_rejects_invalid_magic():
    with pytest.raises(MsaError, match="Not an Atari MSA"):
        decode_msa(struct.pack(">5H", 0, 9, 0, 0, 0))


@pytest.mark.parametrize(
    "sectors,sides,first,last",
    [(0, 0, 0, 0), (31, 0, 0, 0), (9, 2, 0, 0), (9, 0, 2, 1), (9, 0, 0, 200)],
)
def test_decode_rejects_unsupported_geometry(sectors, sides, first, last):
    payload = struct.pack(">5H", MAGIC, sectors, sides, first, last)
    with pytest.raises(MsaError):
        decode_msa(payload)


def test_decode_rejects_truncated_track_table():
    header = struct.pack(">5H", MAGIC, 9, 0, 0, 0)
    with pytest.raises(MsaError, match="track table is truncated"):
        decode_msa(header)


def test_decode_rejects_truncated_track_data():
    header = struct.pack(">5H", MAGIC, 9, 0, 0, 0)
    with pytest.raises(MsaError, match="track data is truncated"):
        decode_msa(header + b"\x00\x10\x01")


@pytest.mark.parametrize(
    "block,message",
    [
        (b"\xe5", "Truncated MSA run-length"),
        (b"\xe5\x00\x00\x00", "Invalid MSA run length"),
        (b"\xe5\x00\x12\x01", "Invalid MSA run length"),
    ],
)
def test_decode_rejects_invalid_rle_blocks(block, message):
    header = struct.pack(">5H", MAGIC, 1, 0, 0, 0)
    payload = header + len(block).to_bytes(2, "big") + block
    with pytest.raises(MsaError, match=message):
        decode_msa(payload)


def test_decode_rejects_trailing_data():
    geometry = MsaGeometry(9, 1, 0, 0)
    with pytest.raises(MsaError, match="trailing data"):
        decode_msa(encode_msa(_raw_track(), geometry) + b"\x00")


def test_encode_rejects_wrong_raw_size():
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=0, last_track=0)
    with pytest.raises(MsaError, match="does not match geometry"):
        encode_msa(b"too short", geometry)


def test_write_msa_atomic_round_trip(tmp_path):
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=0, last_track=0)
    raw = _raw_track()
    image = tmp_path / "disk.msa"
    image.write_bytes(encode_msa(raw, geometry))
    image.chmod(0o640)

    changed = bytearray(raw)
    changed[512:520] = b"MSA TEST"
    write_msa_atomic(image, bytes(changed), geometry)

    decoded, decoded_geometry = decode_msa(image.read_bytes())
    assert decoded == bytes(changed)
    assert decoded_geometry == geometry
    assert os.stat(image).st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["disk.msa"]

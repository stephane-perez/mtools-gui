from __future__ import annotations

import os

import pytest

from mtools_gui.msa import (
    MsaError,
    MsaGeometry,
    decode_msa,
    encode_msa,
    write_msa_atomic,
)


def test_round_trip_uncompressed_tracks():
    geometry = MsaGeometry(sectors_per_track=9, sides=2, first_track=0, last_track=1)
    raw = bytes((index * 37 + 11) % 256 for index in range(geometry.image_size))

    encoded = encode_msa(raw, geometry)
    decoded, decoded_geometry = decode_msa(encoded)

    assert decoded == raw
    assert decoded_geometry == geometry


def test_round_trip_compressed_tracks_and_nonzero_first_track():
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=2, last_track=3)
    track_size = geometry.sectors_per_track * 512
    raw = (b"\x00" * track_size) + (b"\xe5" * track_size)

    encoded = encode_msa(raw, geometry)
    decoded, decoded_geometry = decode_msa(encoded)

    assert len(encoded) < len(raw)
    assert decoded == raw
    assert decoded_geometry == geometry


def test_decode_rejects_invalid_magic():
    with pytest.raises(MsaError, match="Not an Atari MSA"):
        decode_msa(b"\x00\x00" + b"\x00" * 20)


def test_decode_rejects_truncated_track():
    header = bytes.fromhex("0e0f09010000")
    with pytest.raises(MsaError, match="track table is truncated"):
        decode_msa(header)


def test_encode_rejects_wrong_raw_size():
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=0, last_track=0)
    with pytest.raises(MsaError, match="does not match geometry"):
        encode_msa(b"too short", geometry)


def test_write_msa_atomic_round_trip(tmp_path):
    geometry = MsaGeometry(sectors_per_track=9, sides=1, first_track=0, last_track=0)
    raw = b"\x00" * geometry.image_size
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

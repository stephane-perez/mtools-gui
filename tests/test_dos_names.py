"""8.3 short-name conversion (dos_names.py) and how DosBackend applies it:
names created on the DOS side (copy from Linux, new folder, rename) become
uppercase 8.3 so Atari TOS / DOS see them - no VFAT long-name entries.
End-to-end tests use the real mtools binaries on a fresh disk image and
check the raw directory entries; they're skipped without mtools.
"""

import os
import shutil
import subprocess
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from mtools_gui import mtools_client
from mtools_gui.backends.dos_backend import DosBackend
from mtools_gui.dos_names import assign_short_names, is_short_name, to_short_name
from mtools_gui.entry import Entry

needs_mtools = pytest.mark.skipif(
    shutil.which("mformat") is None or shutil.which("mcopy") is None,
    reason="mtools not installed",
)


@pytest.mark.parametrize(
    "name, expected",
    [
        ("README.TXT", "README.TXT"),
        ("readme.txt", "README.TXT"),
        ("Un fichier très long.txt", "UNFICHIE.TXT"),
        ("archive.tar.gz", "ARCHIVET.GZ"),
        ("photo.jpeg", "PHOTO.JPE"),
        ("Mon Dossier Long", "MONDOSSI"),
        ("été", "ETE"),
        ("a+b=c.txt", "A_B_C.TXT"),
        (".bashrc", "BASHRC"),
        ("noext.", "NOEXT"),
        ("€€€", "_"),
        ("prix 5€.txt", "PRIX5.TXT"),
        ("é", "E"),
        ("...", "_"),
        ("GAME.PRG", "GAME.PRG"),
    ],
)
def test_to_short_name(name, expected):
    assert to_short_name(name) == expected


def test_conversion_is_idempotent():
    for name in ["Un fichier très long.txt", "x.y.z", "abc def"]:
        once = to_short_name(name)
        assert to_short_name(once) == once
        assert is_short_name(once)


def test_batch_collisions_get_a_tilde_suffix():
    assert assign_short_names(["Rapport 2024.txt", "rapport2024.txt", "RAPPORT2024.TXT"]) == [
        "RAPPORT2.TXT",
        "RAPPOR~1.TXT",
        "RAPPOR~2.TXT",
    ]


def test_batch_without_collisions_is_plain_conversion():
    assert assign_short_names(["a.txt", "b.txt"]) == ["A.TXT", "B.TXT"]


# -- DosBackend wiring (mtools mocked) ----------------------------------------


def test_mkdir_and_rename_use_short_names_when_enabled(monkeypatch):
    calls = []
    monkeypatch.setattr(mtools_client, "mkdir", lambda dev, p: calls.append(("mmd", p)))
    monkeypatch.setattr(mtools_client, "rename", lambda dev, a, b: calls.append(("mren", a, b)))

    backend = DosBackend("/dev/sde1", short_names=True)
    backend.mkdir("/", "Nouveau dossier")
    backend.rename("/DIR", "OLD.TXT", "nouveau nom.txt")

    assert calls == [("mmd", "::/NOUVEAUD"), ("mren", "::/DIR/OLD.TXT", "::/DIR/NOUVEAUN.TXT")]


def test_names_are_kept_when_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(mtools_client, "mkdir", lambda dev, p: calls.append(p))
    DosBackend("/dev/sde1", short_names=False).mkdir("/", "Nouveau dossier")
    assert calls == ["::/Nouveau dossier"]


def test_copy_in_skips_staging_when_names_are_already_8_3(monkeypatch, tmp_path):
    (tmp_path / "GAME.PRG").write_bytes(b"x")
    (tmp_path / "AUTO").mkdir()
    (tmp_path / "AUTO" / "BOOT.PRG").write_bytes(b"y")
    captured = {}
    monkeypatch.setattr(
        mtools_client,
        "copy_many_unix_to_dos",
        lambda dev, srcs, dst: captured.update(srcs=srcs, dst=dst),
    )
    sources = [str(tmp_path / "GAME.PRG"), str(tmp_path / "AUTO")]
    DosBackend("/dev/sde1", short_names=True).copy_in_many(sources, "/")
    assert captured["srcs"] == sources


def test_copy_in_stages_renamed_tree_under_home_and_cleans_up(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    src = tmp_path / "src"
    (src / "Mon Dossier").mkdir(parents=True)
    (src / "Mon Dossier" / "lisez moi.txt").write_text("hi")
    (src / "petit.txt").write_text("p")

    seen = {}

    def fake_copy(dev, srcs, dst):
        seen["srcs"] = srcs
        seen["tree"] = sorted(
            os.path.relpath(os.path.join(root, f), os.path.dirname(srcs[0]))
            for s in srcs
            for root, dirs, files in os.walk(s)
            for f in dirs + files
        ) + sorted(os.path.basename(s) for s in srcs)

    monkeypatch.setattr(mtools_client, "copy_many_unix_to_dos", fake_copy)
    DosBackend("/dev/sde1", short_names=True).copy_in_many(
        [str(src / "Mon Dossier"), str(src / "petit.txt")], "/"
    )

    assert [os.path.basename(s) for s in seen["srcs"]] == ["MONDOSSI", "PETIT.TXT"]
    assert all(s.startswith(str(home)) for s in seen["srcs"])
    assert "MONDOSSI/LISEZMOI.TXT" in seen["tree"]
    # Staging area removed afterwards, original untouched.
    assert os.listdir(home / ".cache" / "mtools_gui") == []
    assert (src / "Mon Dossier" / "lisez moi.txt").read_text() == "hi"


def test_target_names_matches_what_copy_in_creates():
    backend = DosBackend("/dev/sde1", short_names=True)
    assert backend.target_names(["a b.txt", "AB.TXT"]) == ["AB.TXT", "AB~1.TXT"]
    assert DosBackend("/dev/sde1").target_names(["a b.txt"]) == ["a b.txt"]


# -- end to end: no VFAT entry written at all ---------------------------------


def _raw_root_entries(image):
    """(short name, is_lfn) for each root directory slot of a FAT12 image."""
    d = image.read_bytes()
    bps = int.from_bytes(d[11:13], "little")
    reserved = int.from_bytes(d[14:16], "little")
    fats = d[16]
    root_entries = int.from_bytes(d[17:19], "little")
    sectors_per_fat = int.from_bytes(d[22:24], "little")
    off = (reserved + fats * sectors_per_fat) * bps
    out = []
    for i in range(root_entries):
        e = d[off + 32 * i : off + 32 * i + 32]
        if e[0] == 0:
            break
        if e[0] == 0xE5:
            continue
        out.append((e[:11].decode("ascii", "replace"), e[11] == 0x0F, e[12]))
    return out


@needs_mtools
def test_end_to_end_copy_writes_only_uppercase_short_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    image = tmp_path / "atari.st"
    subprocess.run(
        ["mformat", "-C", "-f", "720", "-a", "-i", str(image), "::"],
        check=True,
        capture_output=True,
    )
    src = tmp_path / "src"
    (src / "Mon Dossier Long").mkdir(parents=True)
    (src / "Mon Dossier Long" / "readme.md").write_text("r")
    (src / "Un fichier très long.txt").write_text("u")
    (src / "hello.txt").write_text("h")

    backend = DosBackend(str(image), short_names=True)
    backend.copy_in_many(
        [str(src / n) for n in ["Mon Dossier Long", "Un fichier très long.txt", "hello.txt"]], "/"
    )
    backend.mkdir("/", "nouveau dossier")

    entries = _raw_root_entries(image)
    assert not any(is_lfn for _n, is_lfn, _c in entries)
    assert not any(case_flags for _n, _l, case_flags in entries)  # no NT lowercase flag
    assert [n for n, _l, _c in entries] == ["MONDOSSI   ", "UNFICHIETXT", "HELLO   TXT", "NOUVEAUD   "]
    assert {e.name for e in backend.list_dir("/MONDOSSI")} >= {"README.MD"}

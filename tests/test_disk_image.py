"""Floppy disk images (e.g. Atari ST .ST) as an alternative to a physical
DOS drive in the right pane:
  - mtools_client runs mtools directly on an image file (no pkexec, no
    privileged helper), and keeps using the helper for /dev/ devices.
  - the GUI never shows a drive and an image at the same time: mounting
    an image resets the drive picker, picking a drive unmounts the image,
    and "Refresh drives" leaves a mounted image alone.
The end-to-end tests at the bottom run the real mtools binaries against a
freshly formatted image and are skipped where mtools isn't installed.
"""

import os
import shutil
import subprocess
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from mtools_gui import main_window as mw
from mtools_gui import mtools_client
from mtools_gui.backends.dos_backend import DosBackend
from mtools_gui.device_discovery import DriveCandidate

HAS_MTOOLS = shutil.which("mformat") is not None and shutil.which("mdir") is not None
needs_mtools = pytest.mark.skipif(not HAS_MTOOLS, reason="mtools not installed")


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    return warnings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    # Keep the user's real settings untouched, and start every test clean.
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr(mw, "QSettings", lambda: QSettings(path, QSettings.IniFormat))
    return path


DRIVE = DriveCandidate(path="/dev/sde1", label="ATARI", size="2G", fstype="", mountpoint=None)


@pytest.fixture
def window(settings_file, monkeypatch):
    monkeypatch.setattr(mw.device_discovery, "discover", lambda: [DRIVE])
    # Listing is never really performed in GUI tests: record who asked.
    listed = []

    def fake_list_dir(device, dos_path):
        listed.append(device)
        return []

    monkeypatch.setattr(mtools_client, "list_dir", fake_list_dir)
    w = mw.MainWindow()
    w._listed = listed
    return w


# -- mtools_client dispatch ----------------------------------------------------


def test_image_target_runs_mtools_directly_without_pkexec(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mtools_client.subprocess, "run", fake_run)
    mtools_client.copy_many_unix_to_dos("/home/u/disk.st", ["/home/u/a.txt"], "::/")

    assert captured["argv"] == ["mcopy", "-i", "/home/u/disk.st", "-s", "/home/u/a.txt", "::/"]
    assert captured["env"]["MTOOLS_SKIP_CHECK"] == "1"


def test_block_device_still_goes_through_the_helper(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mtools_client.subprocess, "run", fake_run)
    mtools_client.delete("/dev/sde1", "::/A.TXT")

    assert captured["argv"][0] == "pkexec"


def test_image_command_failure_raises_a_command_error(monkeypatch):
    monkeypatch.setattr(
        mtools_client.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="could not read boot sector"),
    )
    with pytest.raises(mtools_client.MtoolsCommandError, match="boot sector"):
        mtools_client.list_dir("/home/u/notanimage.st", "::")


def test_image_timeout_raises_timeout_error(monkeypatch):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(mtools_client.subprocess, "run", fake_run)
    with pytest.raises(mtools_client.MtoolsTimeoutError):
        mtools_client.list_dir("/home/u/disk.st", "::")


def test_dos_backend_knows_whether_it_is_an_image():
    assert DosBackend("/home/u/disk.st").is_image
    assert not DosBackend("/dev/sde1").is_image


# -- GUI: mutual exclusivity between drive and image ---------------------------


def test_unmount_button_is_disabled_until_an_image_is_mounted(window, tmp_path):
    assert not window.unmount_image_button.isEnabled()
    image = tmp_path / "disk.st"
    image.write_bytes(b"")
    assert window.mount_image(str(image))
    assert window.unmount_image_button.isEnabled()
    assert isinstance(window.right_pane.backend, DosBackend)
    assert window.right_pane.backend.device == str(image)
    assert "disk.st" in window.image_label.text()


def test_mounting_an_image_deselects_the_drive(window, tmp_path):
    window.drive_combo.setCurrentIndex(1)
    assert window.right_pane.backend.device == "/dev/sde1"

    window.mount_image(str(tmp_path / "disk.st"))

    assert window.drive_combo.currentData() is None
    assert window.right_pane.backend.device == str(tmp_path / "disk.st")


def test_selecting_a_drive_unmounts_the_image(window, tmp_path):
    window.mount_image(str(tmp_path / "disk.st"))

    window.drive_combo.setCurrentIndex(1)

    assert window.right_pane.backend.device == "/dev/sde1"
    assert window._mounted_image is None
    assert not window.unmount_image_button.isEnabled()
    assert window.image_label.text() == ""


def test_unmount_empties_the_right_pane(window, tmp_path):
    window.mount_image(str(tmp_path / "disk.st"))

    window.unmount_image()

    assert window.right_pane.backend is None
    assert window.drive_combo.currentData() is None
    assert not window.unmount_image_button.isEnabled()


def test_refreshing_drives_keeps_the_mounted_image(window, tmp_path):
    window.mount_image(str(tmp_path / "disk.st"))

    window._refresh_drives()

    assert window.right_pane.backend.device == str(tmp_path / "disk.st")
    assert window.drive_combo.currentData() is None


def test_unreadable_image_is_not_mounted_and_shows_an_error(window, monkeypatch, no_real_dialogs):
    window.drive_combo.setCurrentIndex(1)

    def failing_list_dir(device, dos_path):
        raise mtools_client.MtoolsCommandError("could not read boot sector")

    monkeypatch.setattr(mtools_client, "list_dir", failing_list_dir)

    assert not window.mount_image("/home/u/notanimage.st")
    assert no_real_dialogs  # a warning was shown
    # The drive that was there is left alone.
    assert window.right_pane.backend.device == "/dev/sde1"
    assert window.drive_combo.currentData() is DRIVE
    assert window._mounted_image is None


def test_mounted_image_is_restored_on_next_launch(window, tmp_path, monkeypatch):
    image = tmp_path / "disk.st"
    image.write_bytes(b"")
    window.mount_image(str(image))
    window.close()

    monkeypatch.setattr(mw.device_discovery, "discover", lambda: [])
    restored = mw.MainWindow()

    assert restored._mounted_image == str(image)
    assert restored.right_pane.backend.device == str(image)


# -- end to end with real mtools ------------------------------------------------


@pytest.fixture
def atari_image(tmp_path):
    image = tmp_path / "atari.st"
    # 720 KB double-sided double-density, Atari flavour of the boot sector.
    subprocess.run(
        ["mformat", "-C", "-f", "720", "-a", "-i", str(image), "::"],
        check=True,
        capture_output=True,
    )
    return image


@needs_mtools
def test_end_to_end_copy_in_list_read_and_copy_out(atari_image, tmp_path):
    src = tmp_path / "src"
    (src / "SUB").mkdir(parents=True)
    (src / "README.TXT").write_text("hello atari\n")
    (src / "SUB" / "GAME.PRG").write_bytes(b"\x60\x1a")

    backend = DosBackend(str(atari_image))
    backend.copy_in_many([str(src / "README.TXT"), str(src / "SUB")], "/")
    backend.mkdir("/", "AUTO")

    names = {e.name.upper(): e.is_dir for e in backend.list_dir("/")}
    assert names == {"README.TXT": False, "SUB": True, "AUTO": True}
    assert backend.read_text("/", "README.TXT") == "hello atari\n"

    out = tmp_path / "out"
    out.mkdir()
    backend.copy_out_many("/", ["README.TXT", "SUB"], str(out))
    assert (out / "README.TXT").read_text() == "hello atari\n"
    assert (out / "SUB" / "GAME.PRG").read_bytes() == b"\x60\x1a"

    backend.delete_many("/", backend.list_dir("/"))
    assert backend.list_dir("/") == []


@needs_mtools
def test_end_to_end_non_image_file_is_rejected(tmp_path):
    not_an_image = tmp_path / "notes.txt"
    not_an_image.write_text("not a floppy")
    with pytest.raises(mtools_client.MtoolsCommandError):
        DosBackend(str(not_an_image)).list_dir("/")

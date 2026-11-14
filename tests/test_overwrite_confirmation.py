"""Copying/moving onto an existing same-named destination entry used to
silently nest instead of replacing it (mcopy/mmove, and shutil.copytree
without dirs_exist_ok, copy the source *inside* an existing destination
directory rather than replacing it - e.g. copying folder PHOTOS onto an
already-present PHOTOS produced PHOTOS/PHOTOS/* nested alongside PHOTOS's old
contents). Now: the destination is checked for conflicts, the user is
asked to confirm, and on confirmation every conflicting destination entry is
renamed to a backup name before the copy/move runs (not deleted outright -
see _with_replace_many's docstring: a rename-then-restore-on-failure means a
copy that fails partway through doesn't cost the user their original).
Headless (offscreen); the end-to-end test uses only local temp
directories - no SD card or mtools involved.
"""

import os
from datetime import datetime
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from mtools_gui import main_window as mw
from mtools_gui.backends.local_backend import LocalBackend
from mtools_gui.entry import Entry
from mtools_gui.transfer_service import TransferService


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _entry(name, is_dir=False):
    return Entry(name=name, is_dir=is_dir, size=0, modified=datetime(2024, 1, 1))


# -- TransferService._with_replace_many --------------------------------------


def _fake_backend(calls, listing=()):
    return SimpleNamespace(
        rename=lambda directory, old, new: calls.append(("rename", directory, old, new)),
        delete=lambda directory, name, is_dir: calls.append(("delete", directory, name, is_dir)),
        list_dir=lambda directory: [_entry(n) for n in listing],
    )


def test_with_replace_renames_existing_entry_out_of_the_way_first():
    calls = []
    existing = _entry("PHOTOS", is_dir=True)
    dst_backend = _fake_backend(calls)

    wrapped = TransferService._with_replace_many(
        lambda: calls.append(("copy",)), dst_backend, "/dest_dir", [existing]
    )
    wrapped()

    assert calls == [
        ("rename", "/dest_dir", "PHOTOS", "~MTG0001.BAK"),
        ("copy",),
        ("delete", "/dest_dir", "~MTG0001.BAK", True),
    ]


def test_with_replace_renames_every_conflicting_entry_for_a_batch():
    calls = []
    existing_a = _entry("PHOTOS", is_dir=True)
    existing_b = _entry("VIDEOS", is_dir=True)
    dst_backend = _fake_backend(calls)

    wrapped = TransferService._with_replace_many(
        lambda: calls.append(("copy",)), dst_backend, "/dest_dir", [existing_a, existing_b]
    )
    wrapped()

    assert calls == [
        ("rename", "/dest_dir", "PHOTOS", "~MTG0001.BAK"),
        ("rename", "/dest_dir", "VIDEOS", "~MTG0002.BAK"),
        ("copy",),
        ("delete", "/dest_dir", "~MTG0001.BAK", True),
        ("delete", "/dest_dir", "~MTG0002.BAK", True),
    ]


def test_with_replace_restores_the_backup_if_the_copy_fails():
    calls = []
    existing = _entry("PHOTOS", is_dir=True)
    dst_backend = _fake_backend(calls)

    def failing_copy():
        raise OSError("card was pulled mid-copy")

    wrapped = TransferService._with_replace_many(failing_copy, dst_backend, "/dest_dir", [existing])

    with pytest.raises(Exception, match="card was pulled"):
        wrapped()

    # the failed attempt is cleaned up, then the original is restored -
    # the user still has their original PHOTOS, not nothing (the fake
    # listing is empty, so the post-restore check reports it missing:
    # that's covered separately below)
    assert calls[:3] == [
        ("rename", "/dest_dir", "PHOTOS", "~MTG0001.BAK"),
        ("delete", "/dest_dir", "PHOTOS", True),
        ("rename", "/dest_dir", "~MTG0001.BAK", "PHOTOS"),
    ]


def test_with_replace_is_noop_when_nothing_to_replace():
    calls = []
    wrapped = TransferService._with_replace_many(lambda: calls.append("copy"), None, "/x", [])
    wrapped()
    assert calls == ["copy"]


def test_replace_end_to_end_survives_a_failed_copy(tmp_path):
    # A LocalBackend<->LocalBackend rehearsal of the rollback: the
    # destination's original content must still be there after a copy
    # that fails partway through, not vanished.
    src_dir = tmp_path / "src"
    (src_dir / "PHOTOS").mkdir(parents=True)
    (src_dir / "PHOTOS" / "f1.txt").write_text("from source")

    dst_dir = tmp_path / "dst"
    (dst_dir / "PHOTOS").mkdir(parents=True)
    (dst_dir / "PHOTOS" / "existing.txt").write_text("precious original data")

    backend = LocalBackend()
    existing = _entry("PHOTOS", is_dir=True)

    def failing_copy():
        raise OSError("simulated failure partway through the copy")

    wrapped = TransferService._with_replace_many(failing_copy, backend, str(dst_dir), [existing])

    with pytest.raises(OSError):
        wrapped()

    assert (dst_dir / "PHOTOS" / "existing.txt").read_text() == "precious original data"


# -- main_window._transfer_entries: conflict detection + confirmation -------


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))


@pytest.fixture
def window(qapp):
    return mw.MainWindow()


def test_conflict_detected_asks_confirmation_and_passes_replacements(window, monkeypatch):
    src = window.left_pane
    dst = window.right_pane

    existing = _entry("PHOTOS", is_dir=True)
    monkeypatch.setattr(dst, "backend", SimpleNamespace(list_dir=lambda path: [existing]))

    captured = {}
    monkeypatch.setattr(
        window.transfer_service,
        "copy",
        lambda *a, **k: captured.update(kwargs=k),
    )
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    window._transfer_entries(src, dst, [_entry("PHOTOS", is_dir=True)], move=False)

    assert captured["kwargs"]["replacements"] == {"PHOTOS": existing}


def test_conflict_declined_cancels_the_whole_transfer(window, monkeypatch):
    src = window.left_pane
    dst = window.right_pane
    monkeypatch.setattr(
        dst, "backend", SimpleNamespace(list_dir=lambda path: [_entry("PHOTOS", is_dir=True)])
    )
    called = []
    monkeypatch.setattr(window.transfer_service, "copy", lambda *a, **k: called.append(True))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.No))

    window._transfer_entries(src, dst, [_entry("PHOTOS", is_dir=True)], move=False)

    assert called == []


def test_no_conflict_does_not_prompt_and_passes_empty_replacements(window, monkeypatch):
    src = window.left_pane
    dst = window.right_pane
    monkeypatch.setattr(dst, "backend", SimpleNamespace(list_dir=lambda path: []))

    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(True) or QMessageBox.Yes))
    captured = {}
    monkeypatch.setattr(
        window.transfer_service, "copy", lambda *a, **k: captured.update(kwargs=k)
    )

    window._transfer_entries(src, dst, [_entry("newfile.txt")], move=False)

    assert asked == []
    assert captured["kwargs"]["replacements"] == {}


# -- End-to-end: reproduces the exact PHOTOS nesting bug with local dirs -----


def test_copying_a_folder_onto_an_existing_one_replaces_instead_of_nesting(
    window, monkeypatch, tmp_path
):
    src_dir = tmp_path / "src"
    (src_dir / "PHOTOS").mkdir(parents=True)
    (src_dir / "PHOTOS" / "f1.txt").write_text("from source")
    (src_dir / "PHOTOS" / "f2.txt").write_text("from source")

    dst_dir = tmp_path / "dst"
    (dst_dir / "PHOTOS").mkdir(parents=True)
    (dst_dir / "PHOTOS" / "existing.txt").write_text("pre-existing local file")

    window.left_pane.set_backend(LocalBackend(), str(src_dir))
    window.right_pane.set_backend(LocalBackend(), str(dst_dir))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))

    entries = window.left_pane.entries_by_names(["PHOTOS"])
    window._transfer_entries(window.left_pane, window.right_pane, entries, move=False)

    # let the background QRunnable finish
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(500, loop.quit)
    loop.exec()

    result = {p.name for p in (dst_dir / "PHOTOS").iterdir()}
    # must be replaced (f1.txt, f2.txt only) - not nested (PHOTOS/PHOTOS/...)
    # and not merged with the stale existing.txt
    assert result == {"f1.txt", "f2.txt"}
    assert not (dst_dir / "PHOTOS" / "PHOTOS").exists()


# -- the backup phase is part of the transaction ------------------------------


class _ScriptedBackend:
    """In-memory directory whose rename/delete can be told to fail."""

    def __init__(self, names, fail_rename=(), fail_delete=()):
        self.names = set(names)
        self.fail_rename = set(fail_rename)  # (old, new) pairs
        self.fail_delete = set(fail_delete)
        self.calls = []

    def list_dir(self, directory):
        return [_entry(n) for n in sorted(self.names)]

    def rename(self, directory, old, new):
        self.calls.append(("rename", old, new))
        if (old, new) in self.fail_rename or old not in self.names or new in self.names:
            raise OSError(f"cannot rename {old} -> {new}")
        self.names.remove(old)
        self.names.add(new)

    def delete(self, directory, name, is_dir):
        self.calls.append(("delete", name))
        if name in self.fail_delete or name not in self.names:
            raise OSError(f"cannot delete {name}")
        self.names.remove(name)


def test_backup_failure_midway_restores_only_what_was_renamed():
    backend = _ScriptedBackend(
        ["A.TXT", "B.TXT", "C.TXT"], fail_rename={("B.TXT", "~MTG0002.BAK")}
    )
    copied = []
    wrapped = TransferService._with_replace_many(
        lambda: copied.append(True), backend, "/", [_entry("A.TXT"), _entry("B.TXT"), _entry("C.TXT")]
    )

    with pytest.raises(OSError, match="B.TXT"):
        wrapped()

    assert copied == []  # never copied over a half-backed-up directory
    assert backend.names == {"A.TXT", "B.TXT", "C.TXT"}  # back exactly as before
    # C was never touched, and nothing was deleted
    assert ("rename", "C.TXT", "~MTG0003.BAK") not in backend.calls
    assert not any(c[0] == "delete" for c in backend.calls)


def test_copy_failure_restores_every_backup_and_removes_partial_copies():
    backend = _ScriptedBackend(["A.TXT", "B.TXT"])

    def partial_copy():
        backend.names.add("A.TXT")  # copied A, then the card was pulled
        raise OSError("card pulled")

    wrapped = TransferService._with_replace_many(
        partial_copy, backend, "/", [_entry("A.TXT"), _entry("B.TXT")]
    )
    with pytest.raises(OSError, match="card pulled"):
        wrapped()

    assert backend.names == {"A.TXT", "B.TXT"}


def test_a_backup_that_cannot_be_restored_is_reported_by_name():
    from mtools_gui.mtools_client import MtoolsClientError

    backend = _ScriptedBackend(["A.TXT"], fail_rename={("~MTG0001.BAK", "A.TXT")})

    def failing_copy():
        raise OSError("card pulled")

    wrapped = TransferService._with_replace_many(failing_copy, backend, "/", [_entry("A.TXT")])
    with pytest.raises(MtoolsClientError) as info:
        wrapped()

    assert "A.TXT -> ~MTG0001.BAK" in str(info.value)
    assert "card pulled" in str(info.value)
    assert "~MTG0001.BAK" in backend.names  # original kept, not deleted


def test_backups_are_deleted_only_after_a_successful_copy_and_leftovers_reported():
    from mtools_gui.mtools_client import MtoolsClientError

    backend = _ScriptedBackend(["A.TXT"], fail_delete={"~MTG0001.BAK"})

    def copy():
        assert "~MTG0001.BAK" in backend.names  # backup still there during the copy
        backend.names.add("A.TXT")

    wrapped = TransferService._with_replace_many(copy, backend, "/", [_entry("A.TXT")])
    with pytest.raises(MtoolsClientError, match="~MTG0001.BAK"):
        wrapped()
    assert "A.TXT" in backend.names


def test_backup_names_avoid_existing_entries_and_survive_8_3_conversion():
    from mtools_gui.dos_names import to_short_name
    from mtools_gui.transfer_service import _backup_names

    backend = _ScriptedBackend(["~mtg0001.bak", "A.TXT"])
    names = _backup_names(backend, "/", 2)
    assert names == ["~MTG0002.BAK", "~MTG0003.BAK"]
    assert all(to_short_name(n) == n for n in names)


def test_replace_on_an_8_3_dos_image_end_to_end(tmp_path):
    # Regression: in 8.3 mode the old ".mtools_gui_bak_<name>" backup was
    # converted on rename (to "MTOOLS_G.TXT"), so restoring or deleting it
    # by its expected name failed, and a 2nd conflict collided with the 1st.
    import shutil
    import subprocess

    if shutil.which("mformat") is None:
        pytest.skip("mtools not installed")
    from mtools_gui.backends.dos_backend import DosBackend

    image = tmp_path / "a.st"
    subprocess.run(["mformat", "-C", "-f", "720", "-i", str(image), "::"], check=True, capture_output=True)
    src = tmp_path / "src"
    src.mkdir()
    for n in ("A.TXT", "B.TXT"):
        (src / n).write_text("old " + n)
    dos = DosBackend(str(image), short_names=True)
    dos.copy_in_many([str(src / "A.TXT"), str(src / "B.TXT")], "/")
    for n in ("A.TXT", "B.TXT"):
        (src / n).write_text("new " + n)

    existing = [e for e in dos.list_dir("/")]
    wrapped = TransferService._with_replace_many(
        lambda: dos.copy_in_many([str(src / "A.TXT"), str(src / "B.TXT")], "/"), dos, "/", existing
    )
    wrapped()

    assert sorted(e.name for e in dos.list_dir("/")) == ["A.TXT", "B.TXT"]
    assert dos.read_text("/", "A.TXT") == "new A.TXT"

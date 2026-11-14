"""The in-app "Install now" button for the privileged helper - runs
`pkexec <sys.executable> -c "import mtools_gui.install_helper as m; m._install()"`
and reacts to its exit code, instead of requiring a manual terminal
command. sys.executable (not shutil.which("mtools-gui-install-helper"))
is used deliberately: that console script only exists on $PATH for a
pipx/pip install, not when running from an AppImage, where it lives
solely inside the bundled Python env - see main_window.py's
_install_helper_now for the full rationale. Headless (offscreen);
subprocess.run is mocked throughout - never actually invokes pkexec or
touches the real system.
"""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from mtools_gui import main_window as mw


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def no_real_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))
    return warnings


@pytest.fixture
def window(qapp):
    return mw.MainWindow()


def test_successful_install_invokes_pkexec_with_current_interpreter_and_refreshes_drives(
    window, monkeypatch, no_real_dialogs
):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mw.subprocess, "run", fake_run)
    refreshed = []
    monkeypatch.setattr(window, "_refresh_drives", lambda: refreshed.append(True))

    window._install_helper_now()

    assert not no_real_dialogs
    assert refreshed == [True]
    assert captured["argv"][0] == "pkexec"
    assert captured["argv"][1] == mw.sys.executable
    assert captured["argv"][2] == "-c"
    assert "mtools_gui.install_helper" in captured["argv"][3]
    assert "_install()" in captured["argv"][3]


def test_auth_dismissed_shows_status_message_not_warning(window, monkeypatch, no_real_dialogs):
    monkeypatch.setattr(
        mw.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=126, stdout="", stderr="")
    )

    window._install_helper_now()

    assert not no_real_dialogs
    message = window.statusBar().currentMessage().lower()
    assert "annul" in message or "cancel" in message


def test_generic_failure_shows_warning_with_stderr(window, monkeypatch, no_real_dialogs):
    monkeypatch.setattr(
        mw.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="disk full"),
    )

    window._install_helper_now()

    assert len(no_real_dialogs) == 1
    assert "disk full" in no_real_dialogs[0][2]


def test_pkexec_missing_shows_warning(window, monkeypatch, no_real_dialogs):
    def raise_not_found(*a, **k):
        raise FileNotFoundError("pkexec")

    monkeypatch.setattr(mw.subprocess, "run", raise_not_found)

    window._install_helper_now()

    assert len(no_real_dialogs) == 1


def test_install_button_in_dialog_triggers_install(window, monkeypatch, no_real_dialogs):
    called = []
    monkeypatch.setattr(window, "_install_helper_now", lambda: called.append(True))
    monkeypatch.setattr(mw.os.path, "exists", lambda path: False)

    # Simulate the user clicking "Install now" in the QMessageBox by
    # driving MainWindow._check_helper_installed with a stubbed QMessageBox
    # that reports the accept-role button as clicked.
    class FakeBox:
        def __init__(self, *a, **k):
            self._buttons = {}

        def setIcon(self, *a):
            pass

        def setWindowTitle(self, *a):
            pass

        def setText(self, *a):
            pass

        def addButton(self, text, role):
            btn = object()
            self._buttons[role] = btn
            return btn

        def exec(self):
            pass

        def clickedButton(self):
            return self._buttons[QMessageBox.AcceptRole]

    monkeypatch.setattr(mw, "QMessageBox", FakeBox)
    # Constants read off the (now-replaced) name in main_window's
    # namespace - carry them over from the real class.
    FakeBox.AcceptRole = QMessageBox.AcceptRole
    FakeBox.RejectRole = QMessageBox.RejectRole
    FakeBox.Information = QMessageBox.Information

    window._check_helper_installed()

    assert called == [True]


def _fake_box_class(shown):
    class FakeBox:
        AcceptRole = QMessageBox.AcceptRole
        RejectRole = QMessageBox.RejectRole
        Information = QMessageBox.Information

        def __init__(self, *a, **k):
            self._buttons = {}

        def setIcon(self, *a):
            pass

        def setWindowTitle(self, title):
            shown.append(title)

        def setText(self, *a):
            pass

        def addButton(self, text, role):
            shown.append(text)
            btn = object()
            self._buttons[role] = btn
            return btn

        def exec(self):
            pass

        def clickedButton(self):
            return self._buttons[QMessageBox.AcceptRole]

    return FakeBox


def test_outdated_helper_offers_an_update(window, monkeypatch):
    from mtools_gui import install_helper
    from mtools_gui.i18n import _

    called, shown = [], []
    monkeypatch.setattr(window, "_install_helper_now", lambda: called.append(True))
    monkeypatch.setattr(install_helper, "installed_helper_is_outdated", lambda: True)
    monkeypatch.setattr(mw, "QMessageBox", _fake_box_class(shown))

    window._check_helper_installed()

    assert _("update_now_button") in shown
    assert called == [True]


def test_up_to_date_helper_shows_nothing(window, monkeypatch):
    shown = []
    monkeypatch.setattr(mw, "QMessageBox", _fake_box_class(shown))
    window._check_helper_installed()  # conftest: installed and up to date
    assert shown == []


def test_installed_helper_is_outdated_compares_with_the_bundled_copy(monkeypatch, tmp_path):
    import importlib.resources

    from mtools_gui import install_helper

    bundled = (importlib.resources.files("mtools_gui") / "system_files" / "mtools-gui-helper").read_bytes()
    installed = tmp_path / "helper"
    # conftest's autouse fixture stubs this very function out: undo it to
    # test the real one.
    monkeypatch.undo()
    monkeypatch.setattr(install_helper, "HELPER_INSTALL_PATH", str(installed))

    assert install_helper.installed_helper_is_outdated() is False  # not installed: nothing to compare
    installed.write_bytes(bundled)
    assert install_helper.installed_helper_is_outdated() is False
    installed.write_bytes(bundled + b"\n# older version\n")
    assert install_helper.installed_helper_is_outdated() is True

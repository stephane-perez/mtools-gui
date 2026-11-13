"""Shared fixtures.

MainWindow.__init__ calls _check_helper_installed(), which - if the
privileged helper isn't installed on whatever machine runs the tests -
constructs a real QMessageBox and blocks on .exec() waiting for a button
click that never comes under Qt's "offscreen" platform (used throughout
this suite). This went unnoticed locally only because the development
machine happens to have the helper installed for real hardware testing
- on a machine that never ran that install step (a fresh GitHub Actions
runner, confirmed against a real run that sat for ~28 minutes before
being manually cancelled), every test constructing a real MainWindow()
(test_overwrite_confirmation.py, test_progress_indicator.py,
test_install_button.py) hangs instead of failing fast, and since none of
them mock this out themselves, the whole suite hangs with them.

This autouse fixture makes tests behave the same regardless of whether
the helper happens to be installed on whatever machine runs them:
os.path.exists is patched to report the helper as present for that one
specific path, leaving every other os.path.exists call untouched. A test
that specifically wants to exercise the not-installed path (see
test_install_button.py's dialog test) overrides this locally with its
own monkeypatch.setattr - that correctly takes precedence for the
duration of that one test and is undone afterwards, same as any other
monkeypatch stacking.
"""

import os

import pytest

from mtools_gui import install_helper
from mtools_gui.constants import HELPER_INSTALL_PATH


@pytest.fixture(autouse=True)
def _pretend_helper_is_installed(monkeypatch):
    real_exists = os.path.exists

    def fake_exists(path):
        if path == HELPER_INSTALL_PATH:
            return True
        return real_exists(path)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    # Same reasoning for an *outdated* installed helper (e.g. on a dev
    # machine with an older copy installed): the update dialog would block
    # just the same. test_install_button.py exercises it explicitly.
    monkeypatch.setattr(install_helper, "installed_helper_is_outdated", lambda: False)

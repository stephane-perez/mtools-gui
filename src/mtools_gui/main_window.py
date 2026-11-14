from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from . import __version__, device_discovery, install_helper, mtools_client
from .backends.dos_backend import DosBackend
from .backends.local_backend import LocalBackend
from .backends.msa_backend import MsaBackend
from .msa import MsaError
from .constants import HELPER_INSTALL_PATH, INSTALL_HELPER_COMMAND
from .device_discovery import DriveCandidate
from .entry import Entry
from .i18n import _
from .pane_widget import PaneWidget
from .transfer_service import TransferService

logger = logging.getLogger(__name__)

# Opening a file displays its complete text in a QPlainTextEdit. Keep the
# amount of content read into memory bounded.
MAX_VIEW_FILE_SIZE = 64 * 1024

# A multi-select delete/move/overwrite on a folder with hundreds of
# entries used to dump the whole comma-joined list into the confirmation
# dialog - readable for a handful of files, but an unusable wall of text
# (and a dialog stretched off-screen) once selection sizes got large.
# Capped at a character count rather than a line count: QMessageBox wraps
# based on the window's width, which this code doesn't control, so a
# "3 lines" target can't be enforced directly - a character budget is the
# closest deterministic equivalent.
_NAME_LIST_CHAR_LIMIT = 200


def _format_name_list_for_dialog(names: list[str]) -> str:
    joined = ", ".join(names)
    if len(joined) <= _NAME_LIST_CHAR_LIMIT:
        return joined
    kept: list[str] = []
    length = 0
    for name in names:
        addition = len(name) + (2 if kept else 0)  # ", " separator
        if kept and length + addition > _NAME_LIST_CHAR_LIMIT:
            break
        kept.append(name)
        length += addition
    return _("names_truncated", shown=", ".join(kept), count=len(names) - len(kept))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(_("window_title"))
        self.resize(1000, 600)

        self.settings = QSettings()

        self.local_backend = LocalBackend()
        self.transfer_service = TransferService(self)
        self.transfer_service.operation_started.connect(self._on_operation_started)
        self.transfer_service.operation_succeeded.connect(self._on_operation_succeeded)
        self.transfer_service.operation_failed.connect(self._on_operation_failed)

        # Ongoing-operation indicator: a *permanent* status bar widget, so
        # it survives the transient showMessage() calls used elsewhere for
        # "done"/error notifications (those replace each other, but never
        # touch a permanent widget).
        self._pending_operations: list[str] = []
        self._progress_label = QLabel("", self)

        self.left_pane = PaneWidget("left", self)
        self.right_pane = PaneWidget("right", self)
        self.left_pane.error.connect(self._show_status_error)
        self.right_pane.error.connect(self._show_status_error)
        self.left_pane.activated_file.connect(lambda e: self._view_file(self.left_pane, e))
        self.right_pane.activated_file.connect(lambda e: self._view_file(self.right_pane, e))
        self.left_pane.focused.connect(lambda: self._set_active_pane(self.left_pane))
        self.right_pane.focused.connect(lambda: self._set_active_pane(self.right_pane))
        self.left_pane.view.entries_dropped.connect(
            lambda side, names, move: self._on_entries_dropped(self.left_pane, side, names, move)
        )
        self.right_pane.view.entries_dropped.connect(
            lambda side, names, move: self._on_entries_dropped(self.right_pane, side, names, move)
        )
        self.active_pane = self.left_pane

        left_path = self.settings.value("left_pane/path", str(Path.home()))
        if not os.path.isdir(left_path):
            logger.info("Saved left pane path not found (%s), falling back to $HOME", left_path)
            left_path = str(Path.home())
        self.left_pane.set_backend(self.local_backend, left_path)
        logger.info("Left pane initialized at %s", left_path)

        # Consumed once, on the first _refresh_drives() call below, to
        # restore the previously selected DOS drive/path if it's still
        # present on this system - see _refresh_drives/_on_drive_selected.
        self._pending_right_device = self.settings.value("right_pane/device", None)
        self._pending_right_path = self.settings.value("right_pane/path", "/")
        self._pending_right_image = self.settings.value("right_pane/image", None)

        self._drive_candidates: list[DriveCandidate] = []
        self.drive_combo = QComboBox(self)
        self.drive_combo.currentIndexChanged.connect(self._on_drive_selected)
        refresh_drives_button = QPushButton(_("drive_refresh_button"), self)
        refresh_drives_button.clicked.connect(self._refresh_drives)

        # A floppy disk image (e.g. an Atari ST .ST) is the alternative to
        # a physical drive for the right pane - never both at once:
        # mounting an image resets the drive combo to its placeholder, and
        # picking a drive unmounts the image.
        self._mounted_image: str | None = None
        self._mounted_msa_backend: MsaBackend | None = None
        self.open_image_button = QPushButton(_("image_open_button"), self)
        self.open_image_button.setToolTip(_("image_open_tooltip"))
        self.open_image_button.clicked.connect(self._choose_image)
        self.unmount_image_button = QPushButton(_("image_unmount_button"), self)
        self.unmount_image_button.clicked.connect(self.unmount_image)
        self.unmount_image_button.setEnabled(False)
        self.image_label = QLabel("", self)

        # On by default: the drives this tool exists for (Atari TOS/GEMDOS,
        # plain DOS) can't see VFAT long names - see dos_names.py.
        self.short_names_checkbox = QCheckBox(_("short_names_checkbox"), self)
        self.short_names_checkbox.setToolTip(_("short_names_tooltip"))
        self.short_names_checkbox.setChecked(
            self.settings.value("dos/short_names", True, type=bool)
        )
        self.short_names_checkbox.toggled.connect(self._on_short_names_toggled)

        # The drive picker sits on its own row, above both panes rather
        # than inside the right pane's own layout - otherwise the right
        # pane would start one row lower than the left, leaving the two
        # tables visibly misaligned.
        drive_row = QHBoxLayout()
        drive_row.addWidget(QLabel(_("drive_label"), self))
        drive_row.addWidget(self.drive_combo, stretch=1)
        drive_row.addWidget(refresh_drives_button)
        drive_row.addWidget(self.open_image_button)
        drive_row.addWidget(self.unmount_image_button)
        drive_row.addWidget(self.image_label)
        drive_row.addStretch(0)
        drive_row.addWidget(self.short_names_checkbox)

        splitter = QSplitter(self)
        splitter.addWidget(self.left_pane)
        splitter.addWidget(self.right_pane)
        splitter.setSizes([500, 500])

        central = QWidget(self)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 4)
        central_layout.addLayout(drive_row)
        central_layout.addWidget(splitter)
        self.setCentralWidget(central)

        self._build_toolbar()
        self._build_shortcuts()
        self.statusBar().addPermanentWidget(self._progress_label)
        self.statusBar().showMessage(_("status_ready"))

        self._refresh_drives()
        self._restore_image()
        self._check_helper_installed()

    # -- setup -----------------------------------------------------------

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Actions", self)
        self.addToolBar(toolbar)
        toolbar.addAction(_("toolbar_refresh"), self.refresh_active_pane)
        toolbar.addAction(_("toolbar_copy"), self.copy_selection)
        move_action = toolbar.addAction(_("toolbar_move"), self.move_selection)
        move_action.setToolTip(_("toolbar_move_tooltip"))
        toolbar.addAction(_("toolbar_rename"), self.rename_selection)
        toolbar.addAction(_("toolbar_delete"), self.delete_selection)
        toolbar.addAction(_("toolbar_mkdir"), self.make_directory)
        toolbar.addAction(_("toolbar_about"), self._show_about)

    def _build_shortcuts(self) -> None:
        # Deliberately not Total Commander/Midnight Commander bindings.
        # No shortcut for "Deplacer" - only the toolbar button and
        # Alt+glisser-deposer trigger a move (Ctrl+Alt+C was unwieldy).
        QShortcut(QKeySequence("F5"), self, activated=self.refresh_active_pane)
        QShortcut(QKeySequence("Ctrl+C"), self, activated=self.copy_selection)
        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.make_directory)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self.delete_selection)
        QShortcut(QKeySequence(Qt.Key_F2), self, activated=self.rename_selection)
        QShortcut(QKeySequence(Qt.Key_Tab), self, activated=self._toggle_active_pane)

    def _check_helper_installed(self) -> None:
        if not os.path.exists(HELPER_INSTALL_PATH):
            logger.warning("Privileged helper not installed (%s)", HELPER_INSTALL_PATH)
            title = _("dialog_install_required_title")
            text = _("dialog_install_required_text", command=INSTALL_HELPER_COMMAND)
            button = _("install_now_button")
        elif install_helper.installed_helper_is_outdated():
            logger.warning("Privileged helper outdated (%s)", HELPER_INSTALL_PATH)
            title = _("dialog_update_required_title")
            text = _("dialog_update_required_text", command=INSTALL_HELPER_COMMAND)
            button = _("update_now_button")
        else:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(title)
        box.setText(text)
        install_button = box.addButton(button, QMessageBox.AcceptRole)
        box.addButton(_("install_later_button"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is install_button:
            self._install_helper_now()

    def _install_helper_now(self) -> None:
        # Re-invoke the current interpreter (sys.executable) rather than
        # shutil.which("mtools-gui-install-helper") on $PATH: that console
        # script only exists there for a pipx/pip install. Running from
        # an AppImage, it lives solely inside the bundled Python env, with
        # no on-PATH entry at all. sys.executable + "-c" works uniformly
        # across every packaging form, since Python resolves its own
        # stdlib/site-packages from the interpreter binary's own location
        # (not from $PYTHONPATH/$PYTHONHOME, which pkexec strips anyway,
        # same as sudo's env_reset) - confirmed by running the app itself
        # from a built AppImage during development.
        logger.info("Installing privileged helper via pkexec (%s -c ...)", sys.executable)
        script = "import mtools_gui.install_helper as m; m._install()"
        try:
            proc = subprocess.run(
                ["pkexec", sys.executable, "-c", script], capture_output=True, text=True
            )
        except FileNotFoundError as exc:
            logger.error("pkexec not found: %s", exc)
            QMessageBox.warning(self, _("dialog_error_title"), _("err_pkexec_not_found"))
            return

        if proc.returncode == 0:
            logger.info("Privileged helper installed successfully")
            QMessageBox.information(self, _("install_success_title"), _("install_success_text"))
            self._refresh_drives()
        elif proc.returncode == 126:
            logger.info("Helper install cancelled (auth dismissed)")
            self.statusBar().showMessage(_("install_auth_cancelled"), 5000)
        else:
            error = proc.stderr.strip() or f"exit code {proc.returncode}"
            logger.error("Helper install failed: %s", error)
            QMessageBox.warning(self, _("dialog_error_title"), _("install_failed_text", error=error))

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            _("about_title"),
            _("about_text", version=__version__),
        )

    # -- active pane tracking ---------------------------------------------

    def _set_active_pane(self, pane: PaneWidget) -> None:
        self.active_pane = pane

    def _toggle_active_pane(self) -> None:
        other = self.right_pane if self.active_pane is self.left_pane else self.left_pane
        other.view.setFocus()

    def _inactive_pane(self) -> PaneWidget:
        return self.right_pane if self.active_pane is self.left_pane else self.left_pane

    # -- drive picker -------------------------------------------------------

    def _refresh_drives(self) -> None:
        # On a manual refresh, keep whatever is currently selected; on the
        # very first call (startup), nothing is selected yet, so fall back
        # to the device saved from the previous session.
        current_data = self.drive_combo.currentData()
        preferred_path = current_data.path if current_data else self._pending_right_device
        self._pending_right_device = None

        self._drive_candidates = device_discovery.discover()
        logger.info("%d DOS drive(s) detected", len(self._drive_candidates))
        self.drive_combo.blockSignals(True)
        self.drive_combo.clear()
        self.drive_combo.addItem(_("drive_placeholder"), None)
        restore_index = 0
        for i, candidate in enumerate(self._drive_candidates, start=1):
            self.drive_combo.addItem(candidate.display_name(), candidate)
            if preferred_path and candidate.path == preferred_path:
                restore_index = i
        self.drive_combo.setCurrentIndex(restore_index)
        self.drive_combo.blockSignals(False)

        # Signals were blocked throughout the rebuild above, so
        # currentIndexChanged never fired on its own. Only force an
        # attach/detach when needed - if the same device is still
        # selected, leave the pane's current navigation path alone
        # (a manual "Rafraichir" shouldn't reset the user back to "/").
        already_attached = (
            restore_index != 0
            and isinstance(self.right_pane.backend, DosBackend)
            and self.right_pane.backend.device == preferred_path
        )
        # A mounted image leaves the combo on its placeholder: refreshing
        # the drive list must not unmount it.
        image_mounted = restore_index == 0 and self._mounted_image is not None
        if not already_attached and not image_mounted:
            self._on_drive_selected(restore_index)

    def _on_drive_selected(self, index: int) -> None:
        candidate: DriveCandidate | None = self.drive_combo.itemData(index)
        if candidate is None:
            self.right_pane.set_backend(None)
            return
        if self._mounted_image is not None:
            if not self._flush_msa_image():
                self.drive_combo.blockSignals(True)
                self.drive_combo.setCurrentIndex(0)
                self.drive_combo.blockSignals(False)
                return
            logger.info("DOS drive selected, unmounting image %s", self._mounted_image)
            self._cleanup_msa_backend()
            self._set_mounted_image(None)
        initial_path = self._pending_right_path or "/"
        self._pending_right_path = None
        logger.info("DOS drive selected: %s (initial path %s)", candidate.path, initial_path)
        self.right_pane.set_backend(self._make_dos_backend(candidate.path), initial_path)

    def _make_dos_backend(self, device: str) -> DosBackend:
        return DosBackend(device, short_names=self.short_names_checkbox.isChecked())

    def _on_short_names_toggled(self, checked: bool) -> None:
        logger.info("8.3 short names: %s", "on" if checked else "off")
        self.settings.setValue("dos/short_names", checked)
        if isinstance(self.right_pane.backend, DosBackend):
            self.right_pane.backend.short_names = checked

    # -- disk image -------------------------------------------------------

    def _choose_image(self) -> None:
        start_dir = self.settings.value("image/last_dir", str(Path.home()))
        path, _filter = QFileDialog.getOpenFileName(
            self, _("image_dialog_title"), start_dir, _("image_dialog_filter")
        )
        if path:
            self.settings.setValue("image/last_dir", os.path.dirname(path))
            self.mount_image(path)

    def mount_image(self, path: str, initial_path: str = "/") -> bool:
        """Mount a raw FAT image or an Atari MSA image in the right pane."""
        path = os.path.abspath(path)
        if path == self._mounted_image:
            return True
        backend: DosBackend
        try:
            if Path(path).suffix.lower() == ".msa":
                backend = MsaBackend(path, short_names=self.short_names_checkbox.isChecked())
            else:
                backend = self._make_dos_backend(path)
            # Probe before replacing the currently mounted image.
            backend.list_dir("/")
        except (mtools_client.MtoolsClientError, MsaError, OSError) as exc:
            if "backend" in locals() and isinstance(backend, MsaBackend):
                backend.cleanup()
            logger.error("Could not mount image %s: %s", path, exc)
            QMessageBox.warning(
                self, _("dialog_error_title"), _("image_mount_failed", path=path, error=exc)
            )
            return False

        if not self._flush_msa_image():
            if isinstance(backend, MsaBackend):
                backend.cleanup()
            return False
        self._cleanup_msa_backend()

        logger.info("Disk image mounted: %s (initial path %s)", path, initial_path)
        self.drive_combo.blockSignals(True)
        self.drive_combo.setCurrentIndex(0)
        self.drive_combo.blockSignals(False)
        self._set_mounted_image(path)
        self._mounted_msa_backend = backend if isinstance(backend, MsaBackend) else None
        self.right_pane.set_backend(backend, initial_path)
        return True

    def _flush_msa_image(self) -> bool:
        """Persist changes from the temporary raw image back to its MSA file."""
        if self._mounted_msa_backend is None:
            return True
        try:
            self._mounted_msa_backend.flush()
        except (OSError, MsaError) as exc:
            logger.exception("Could not save MSA image %s", self._mounted_image)
            QMessageBox.warning(
                self, _("dialog_error_title"),
                _("msa_save_failed", path=self._mounted_image or "", error=exc),
            )
            return False
        return True

    def _cleanup_msa_backend(self) -> None:
        if self._mounted_msa_backend is not None:
            self._mounted_msa_backend.cleanup()
            self._mounted_msa_backend = None

    def unmount_image(self) -> None:
        if self._mounted_image is None:
            return
        if not self._flush_msa_image():
            return
        logger.info("Disk image unmounted: %s", self._mounted_image)
        self._cleanup_msa_backend()
        self._set_mounted_image(None)
        self.right_pane.set_backend(None)

    def _set_mounted_image(self, path: str | None) -> None:
        self._mounted_image = path
        self.unmount_image_button.setEnabled(path is not None)
        if path is None:
            self.image_label.setText("")
            self.image_label.setToolTip("")
        else:
            self.image_label.setText(_("image_mounted_label", name=os.path.basename(path)))
            self.image_label.setToolTip(path)

    def _restore_image(self) -> None:
        # Startup only: re-mount the image from the previous session, unless
        # a drive was restored instead (the two are mutually exclusive).
        image = self._pending_right_image
        self._pending_right_image = None
        if not image or self.drive_combo.currentData() is not None:
            return
        if not os.path.isfile(image):
            logger.info("Saved disk image not found (%s), not restoring it", image)
            return
        self.mount_image(image, self._pending_right_path or "/")
        self._pending_right_path = None

    # -- operations -----------------------------------------------------

    def copy_selection(self) -> None:
        src = self.active_pane
        dst = self._inactive_pane()
        self._transfer_entries(src, dst, src.selected_entries(), move=False)

    def move_selection(self) -> None:
        src = self.active_pane
        dst = self._inactive_pane()
        self._transfer_entries(src, dst, src.selected_entries(), move=True)

    def _on_entries_dropped(self, dst: PaneWidget, src_side: str, names: list[str], move: bool) -> None:
        src = self.left_pane if src_side == "left" else self.right_pane
        if src is dst:
            return  # dropped on the pane it came from - nothing to do
        entries = src.entries_by_names(names)
        logger.info(
            "Drag-and-drop (%s): %s from %s to %s",
            "move" if move else "copy", names, src.side, dst.side,
        )
        self._transfer_entries(src, dst, entries, move=move)

    def _transfer_entries(self, src: PaneWidget, dst: PaneWidget, entries: list[Entry], move: bool) -> None:
        if not entries or not self._require_backend(dst):
            return
        if move:
            names = ", ".join(e.name for e in entries)
            if QMessageBox.question(
                self,
                _("confirm_move_title"),
                _(
                    "confirm_move_text",
                    side=_("side_left" if dst.side == "left" else "side_right"),
                    names=_format_name_list_for_dialog([e.name for e in entries]),
                ),
            ) != QMessageBox.Yes:
                logger.info("Move cancelled by the user: %s", names)
                return

        # mcopy/mmove (and shutil.copytree without dirs_exist_ok) treat an
        # *existing* destination directory as "copy source inside it"
        # rather than "replace it" - so an unnoticed name collision would
        # silently nest a nested copy instead of overwriting. Check
        # against a fresh listing (not the possibly-stale pane model) and
        # confirm before clobbering anything.
        try:
            dst_listing = dst.backend.list_dir(dst.current_path)
        except Exception as exc:
            logger.error("Could not check destination contents before transfer: %s", exc)
            dst_listing = []

        # Names the entries will actually get at the destination: a copy
        # from Linux onto a DOS drive in 8.3 mode renames them (e.g.
        # "Mon fichier.txt" lands as "MONFICHI.TXT", which may well exist
        # already). DOS names are case-insensitive, so compare them that way.
        names = [e.name for e in entries]
        if isinstance(dst.backend, DosBackend):
            if isinstance(src.backend, DosBackend):
                targets = names  # copied/moved within DOS: names are kept
            else:
                targets = dst.backend.target_names(names)
            dst_by_name = {e.name.upper(): e for e in dst_listing}
            targets = [t.upper() for t in targets]
        else:
            dst_by_name = {e.name: e for e in dst_listing}
            targets = names
        conflicts = {
            e.name: dst_by_name[t] for e, t in zip(entries, targets) if t in dst_by_name
        }
        if conflicts:
            # The names as they exist at the destination (after any 8.3
            # conversion), i.e. what is actually about to be replaced.
            replaced = [r.name for r in conflicts.values()]
            if QMessageBox.question(
                self,
                _("confirm_overwrite_title"),
                _("confirm_overwrite_text", names=_format_name_list_for_dialog(replaced)),
            ) != QMessageBox.Yes:
                logger.info("Overwrite cancelled by the user: %s", ", ".join(replaced))
                return

        verb = "Move" if move else "Copy"
        service_call = self.transfer_service.move if move else self.transfer_service.copy
        # One batched call for the whole selection rather than one per
        # entry: each call is a separate pkexec+helper+mtools round-trip
        # for a DOS-side backend, so submitting per-file made an N-file
        # transfer cost roughly N seconds even though mcopy/mmove/mdel
        # all natively accept a whole file list in a single invocation.
        logger.info(
            "%s requested: %d item(s) (%s:%s -> %s:%s)%s",
            verb, len(entries), src.side, src.current_path, dst.side, dst.current_path,
            f" [{len(conflicts)} replacing existing]" if conflicts else "",
        )
        service_call(
            src.backend, src.side, src.current_path, entries,
            dst.backend, dst.side, dst.current_path,
            replacements=conflicts,
        )

    def delete_selection(self) -> None:
        pane = self.active_pane
        entries = pane.selected_entries()
        if not entries:
            return
        names = ", ".join(e.name for e in entries)
        if QMessageBox.question(
            self,
            _("confirm_delete_title"),
            _("confirm_delete_text", names=_format_name_list_for_dialog([e.name for e in entries])),
        ) != QMessageBox.Yes:
            logger.info("Delete cancelled by the user: %s", names)
            return
        logger.info(
            "Delete requested: %d item(s) from %s:%s", len(entries), pane.side, pane.current_path
        )
        self.transfer_service.delete(pane.backend, pane.side, pane.current_path, entries)

    def make_directory(self) -> None:
        pane = self.active_pane
        if not self._require_backend(pane):
            return
        name, ok = QInputDialog.getText(self, _("dialog_new_folder_title"), _("dialog_new_folder_label"))
        if ok and name:
            logger.info("New folder requested: %s:%s/%s", pane.side, pane.current_path, name)
            self.transfer_service.mkdir(pane.backend, pane.side, pane.current_path, name)

    def rename_selection(self) -> None:
        pane = self.active_pane
        entries = pane.selected_entries()
        if len(entries) != 1:
            self.statusBar().showMessage(_("status_select_one_to_rename"), 4000)
            return
        entry = entries[0]
        new_name, ok = QInputDialog.getText(
            self, _("dialog_rename_title"), _("dialog_rename_label"), text=entry.name
        )
        if ok and new_name and new_name != entry.name:
            logger.info(
                "Rename requested: %s:%s/%s -> %s",
                pane.side, pane.current_path, entry.name, new_name,
            )
            self.transfer_service.rename(pane.backend, pane.side, pane.current_path, entry.name, new_name)

    def refresh_active_pane(self) -> None:
        self.left_pane.refresh()
        self.right_pane.refresh()

    def _require_backend(self, pane: PaneWidget) -> bool:
        if pane.backend is None:
            self.statusBar().showMessage(_("status_choose_drive"), 4000)
            return False
        return True

    def _view_file(self, pane: PaneWidget, entry: Entry) -> None:
        # Check the size from the directory listing before asking a backend
        # to read the file (read_text currently loads the whole content).
        if entry.size > MAX_VIEW_FILE_SIZE:
            QMessageBox.information(
                self,
                _("view_dialog_too_large_title"),
                _("view_dialog_too_large_text", name=entry.name, size=MAX_VIEW_FILE_SIZE // 1024),
            )
            return
        try:
            content = pane.backend.read_text(pane.current_path, entry.name)
        except Exception as exc:
            self._on_operation_failed(_("view_dialog_read_error", name=entry.name, error=exc))
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(entry.name)
        dialog.resize(600, 400)
        layout = QVBoxLayout(dialog)
        text_edit = QPlainTextEdit(dialog)
        text_edit.setReadOnly(True)
        text_edit.setPlainText(content)
        layout.addWidget(text_edit)
        dialog.exec()

    # -- feedback ---------------------------------------------------------

    def _on_operation_started(self, description: str) -> None:
        self._pending_operations.append(description)
        self._update_progress_label()

    def _on_operation_succeeded(self, description: str, sides: str) -> None:
        logger.info("%s: done", description)
        self._remove_pending(description)
        self.statusBar().showMessage(_("status_op_done", description=description), 5000)
        for side in sides.split(","):
            (self.left_pane if side == "left" else self.right_pane).refresh()

    def _on_operation_failed(self, message: str, description: str | None = None) -> None:
        logger.error("%s", message)
        if description is not None:
            self._remove_pending(description)
            # A failed operation may still have changed things (a partial
            # copy, backups left behind - see _with_replace_many): show
            # the real state rather than a stale listing.
            self.left_pane.refresh()
            self.right_pane.refresh()
        self.statusBar().showMessage(message, 8000)
        QMessageBox.warning(self, _("dialog_error_title"), message)

    def _remove_pending(self, description: str) -> None:
        if description in self._pending_operations:
            self._pending_operations.remove(description)
        self._update_progress_label()

    def _update_progress_label(self) -> None:
        n = len(self._pending_operations)
        if n == 0:
            self._progress_label.setText("")
        elif n == 1:
            self._progress_label.setText(_("status_in_progress_one", description=self._pending_operations[0]))
        else:
            self._progress_label.setText(_("status_in_progress_many", count=n))

    def _show_status_error(self, message: str) -> None:
        logger.warning("%s", message)
        self.statusBar().showMessage(message, 8000)

    # -- session persistence ------------------------------------------------

    def closeEvent(self, event) -> None:
        if not self._flush_msa_image():
            event.ignore()
            return
        self._cleanup_msa_backend()
        self.settings.setValue("left_pane/path", self.left_pane.current_path)
        logger.info("Saving left pane path: %s", self.left_pane.current_path)

        candidate: DriveCandidate | None = self.drive_combo.currentData()
        if candidate is not None:
            self.settings.setValue("right_pane/device", candidate.path)
            self.settings.remove("right_pane/image")
            self.settings.setValue("right_pane/path", self.right_pane.current_path)
            logger.info(
                "Saving DOS drive: %s (path %s)", candidate.path, self.right_pane.current_path
            )
        elif self._mounted_image is not None:
            self.settings.setValue("right_pane/image", self._mounted_image)
            self.settings.remove("right_pane/device")
            self.settings.setValue("right_pane/path", self.right_pane.current_path)
            logger.info(
                "Saving disk image: %s (path %s)", self._mounted_image, self.right_pane.current_path
            )
        else:
            self.settings.remove("right_pane/device")
            self.settings.remove("right_pane/image")
            self.settings.remove("right_pane/path")
        super().closeEvent(event)

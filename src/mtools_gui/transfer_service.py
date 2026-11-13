"""Runs copy/move (and other potentially slow) backend operations off the
UI thread, and dispatches to the right mechanism depending on which pair
of backends (local filesystem vs. DOS-via-mtools) is involved.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .backends import Backend
from .backends.dos_backend import DosBackend
from .backends.local_backend import LocalBackend
from .entry import Entry
from .i18n import _
from .mtools_client import MtoolsClientError


class _WorkerSignals(QObject):
    succeeded = Signal()
    failed = Signal(str)


class _CallableTask(QRunnable):
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.signals = _WorkerSignals()

    def run(self):
        try:
            self.func()
        except (MtoolsClientError, OSError) as exc:
            self.signals.failed.emit(str(exc))
        except Exception as exc:  # unexpected - still surface it, don't crash the UI
            self.signals.failed.emit(_("op_unexpected_error", error=exc))
        else:
            self.signals.succeeded.emit()


class TransferService(QObject):
    """Emits operation_succeeded/operation_failed; the GUI decides what to
    refresh from the `sides` hint it passed in when submitting."""

    operation_started = Signal(str)  # description
    operation_succeeded = Signal(str, str)  # description, sides ("left", "right" or "left,right")
    operation_failed = Signal(str, str)  # full_message, description

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        # PySide6/Shiboken gotcha: QThreadPool.start() hands the QRunnable's
        # C++ object to the pool, but nothing keeps the *Python* wrapper
        # alive while the worker thread runs it. If our own refcount on
        # `task` drops to zero (which it does almost immediately once
        # _submit() returns, since no other Python reference to it existed)
        # while the pool is still executing run() on a background thread,
        # the wrapper can be garbage-collected out from under Qt - this was
        # observed to segfault / corrupt the heap ("free(): invalid
        # pointer") specifically on slower operations (e.g. copying a
        # folder with many files), which give more time for the race to
        # hit. Keeping an explicit Python reference for the task's full
        # lifetime closes that race.
        self._active_tasks: list[_CallableTask] = []
        # One lock per DOS device path, created lazily. Nothing serialized
        # two mtools invocations against the same device before this -
        # e.g. a fast double-submit (multi-select copy, or a delete
        # landing while a copy is still running) could run mcopy/mdel
        # concurrently against the same FAT volume, which mtools/FAT was
        # never designed to tolerate. LocalBackend isn't tracked here:
        # ordinary filesystem operations on different files don't share
        # this failure mode, and serializing them too would only cost
        # responsiveness for no real benefit.
        self._device_locks: dict[str, threading.Lock] = {}
        self._device_locks_guard = threading.Lock()

    def _lock_for_device(self, device: str) -> threading.Lock:
        with self._device_locks_guard:
            lock = self._device_locks.get(device)
            if lock is None:
                lock = threading.Lock()
                self._device_locks[device] = lock
            return lock

    def _with_device_lock(self, func, *backends: Backend):
        # Sorted so two operations naming the same two devices in
        # opposite order (src/dst swapped) always acquire them in the
        # same order, avoiding a deadlock between them.
        devices = sorted({b.device for b in backends if isinstance(b, DosBackend)})
        if not devices:
            return func
        locks = [self._lock_for_device(d) for d in devices]

        def wrapped():
            for lock in locks:
                lock.acquire()
            try:
                func()
            finally:
                for lock in reversed(locks):
                    lock.release()

        return wrapped

    def _submit(self, description: str, sides: str, func) -> None:
        task = _CallableTask(func)
        task.setAutoDelete(False)
        self._active_tasks.append(task)

        def _release() -> None:
            if task in self._active_tasks:
                self._active_tasks.remove(task)

        def _on_succeeded() -> None:
            _release()
            self.operation_succeeded.emit(description, sides)

        def _on_failed(msg: str) -> None:
            _release()
            self.operation_failed.emit(
                _("op_failed_combined", description=description, msg=msg), description
            )

        task.signals.succeeded.connect(_on_succeeded)
        task.signals.failed.connect(_on_failed)
        self.operation_started.emit(description)
        self._pool.start(task)

    @staticmethod
    def _describe(op_single: str, op_many: str, entries: list[Entry]) -> str:
        if len(entries) == 1:
            return _(op_single, name=entries[0].name)
        return _(op_many, count=len(entries))

    def copy(
        self,
        src_backend: Backend,
        src_side: str,
        src_dir: str,
        entries: list[Entry],
        dst_backend: Backend,
        dst_side: str,
        dst_dir: str,
        replacements: dict[str, Entry] | None = None,
    ) -> None:
        if not entries:
            return
        replacements = replacements or {}
        description = self._describe("op_copy", "op_copy_many", entries)
        sides = dst_side if src_side == dst_side else f"{src_side},{dst_side}"
        names = [e.name for e in entries]

        if isinstance(src_backend, LocalBackend) and isinstance(dst_backend, LocalBackend):
            import shutil

            def func():
                for entry in entries:
                    src_path = src_backend.join(src_dir, entry.name)
                    dst_path = dst_backend.join(dst_dir, entry.name)
                    if entry.is_dir:
                        shutil.copytree(src_path, dst_path)
                    else:
                        shutil.copy2(src_path, dst_path)
        elif isinstance(src_backend, DosBackend) and isinstance(dst_backend, DosBackend):
            func = lambda: src_backend.copy_within_many(names, src_dir, dst_dir)
        elif isinstance(src_backend, LocalBackend) and isinstance(dst_backend, DosBackend):
            unix_paths = [src_backend.join(src_dir, name) for name in names]
            func = lambda: dst_backend.copy_in_many(unix_paths, dst_dir)
        elif isinstance(src_backend, DosBackend) and isinstance(dst_backend, LocalBackend):
            func = lambda: src_backend.copy_out_many(src_dir, names, dst_dir)
        else:
            raise TypeError(_("err_unknown_pane_combination"))

        func = self._with_replace_many(func, dst_backend, dst_dir, list(replacements.values()))
        func = self._with_device_lock(func, src_backend, dst_backend)
        self._submit(description, sides, func)

    def move(
        self,
        src_backend: Backend,
        src_side: str,
        src_dir: str,
        entries: list[Entry],
        dst_backend: Backend,
        dst_side: str,
        dst_dir: str,
        replacements: dict[str, Entry] | None = None,
    ) -> None:
        if not entries:
            return
        replacements = replacements or {}
        description = self._describe("op_move", "op_move_many", entries)
        sides = dst_side if src_side == dst_side else f"{src_side},{dst_side}"
        names = [e.name for e in entries]

        if isinstance(src_backend, LocalBackend) and isinstance(dst_backend, LocalBackend):
            import shutil

            def func():
                for entry in entries:
                    src_path = src_backend.join(src_dir, entry.name)
                    dst_path = dst_backend.join(dst_dir, entry.name)
                    shutil.move(src_path, dst_path)
        elif isinstance(src_backend, DosBackend) and isinstance(dst_backend, DosBackend):
            func = lambda: src_backend.move_within_many(names, src_dir, dst_dir)
        else:
            # Moving across the Linux/DOS boundary: mmove can't cross it
            # (see man mmove), so this is copy-then-delete-source. If
            # _blocking_copy_many itself raises, delete is never reached -
            # the source is untouched, which is already correct. The gap
            # was the other way: if the copy *succeeds* but deleting the
            # source then fails (e.g. the card was pulled mid-operation),
            # that surfaced as a generic "Move failed", indistinguishable
            # from a move that never copied anything - misleading, since
            # the data is safe (now in both places) rather than lost.
            def func():
                self._blocking_copy_many(src_backend, src_dir, entries, dst_backend, dst_dir)
                try:
                    if isinstance(src_backend, DosBackend):
                        src_backend.delete_many(src_dir, entries)
                    else:
                        for entry in entries:
                            src_backend.delete(src_dir, entry.name, entry.is_dir)
                except Exception as exc:
                    if len(entries) == 1:
                        msg = _(
                            "move_copied_but_source_delete_failed",
                            name=names[0], error=exc,
                        )
                    else:
                        msg = _(
                            "move_copied_but_source_delete_failed_many",
                            count=len(entries), error=exc,
                        )
                    raise MtoolsClientError(msg) from exc

        func = self._with_replace_many(func, dst_backend, dst_dir, list(replacements.values()))
        func = self._with_device_lock(func, src_backend, dst_backend)
        self._submit(description, sides, func)

    @staticmethod
    def _with_replace_many(func, dst_backend: Backend, dst_dir: str, replacements: list[Entry]):
        """Wrap func so every existing same-named destination entry is
        out of the way before it runs. mcopy/mmove (and shutil.copytree
        without dirs_exist_ok) treat an *existing* destination directory
        as "copy source inside it" rather than "replace it" - e.g.
        copying a folder PHOTOS onto an already-present PHOTOS silently
        produced PHOTOS/PHOTOS/* nested alongside PHOTOS's old contents
        instead of replacing them.

        Each existing entry is renamed to a backup name rather than
        deleted outright, so nothing is lost if anything goes wrong. The
        whole sequence is one transaction:
          1. back up each existing entry, recording exactly which renames
             really happened (a failure on the 3rd of 5 leaves only 2 to
             undo - and the 3rd's original untouched);
          2. run func;
          3. only once func succeeded, delete the backups.
        If step 1 or 2 fails, whatever func left under a target name is
        removed and only the backups actually made are renamed back, then
        checked against a fresh listing. A backup that can't be restored
        is reported by name instead of being silently left behind, and so
        is a backup that can't be deleted after a successful copy.

        Backup names are 8.3-safe ("~MTG0001.BAK"): a long name would be
        converted on a DOS drive in 8.3 mode, and restoring would then
        look for a file that no longer exists under the expected name.

        mtools has no multi-file rename, so each conflicting entry costs
        one rename (and maybe one delete) call of its own. That's fine -
        conflicts are the uncommon case; this exists to keep them safe,
        not fast."""
        if not replacements:
            return func

        def wrapped():
            backups = list(zip(replacements, _backup_names(dst_backend, dst_dir, len(replacements))))
            done: list[tuple[Entry, str]] = []
            func_started = False
            try:
                for entry, backup_name in backups:
                    dst_backend.rename(dst_dir, entry.name, backup_name)
                    done.append((entry, backup_name))
                func_started = True
                func()
            except Exception as exc:
                failed = _restore_backups(dst_backend, dst_dir, done, func_started)
                if failed:
                    raise MtoolsClientError(
                        _("replace_restore_failed", error=exc, items=_describe_backups(failed))
                    ) from exc
                raise
            leftover = []
            for entry, backup_name in done:
                try:
                    dst_backend.delete(dst_dir, backup_name, entry.is_dir)
                except Exception:
                    leftover.append((entry, backup_name))
            if leftover:
                raise MtoolsClientError(
                    _("replace_cleanup_failed", items=_describe_backups(leftover))
                )

        return wrapped

    def delete(self, backend: Backend, side: str, directory: str, entries: list[Entry]) -> None:
        if not entries:
            return
        description = self._describe("op_delete", "op_delete_many", entries)

        if isinstance(backend, DosBackend):
            func = lambda: backend.delete_many(directory, entries)
        else:
            def func():
                for entry in entries:
                    backend.delete(directory, entry.name, entry.is_dir)

        self._submit(description, side, self._with_device_lock(func, backend))

    def mkdir(self, backend: Backend, side: str, directory: str, name: str) -> None:
        self._submit(
            _("op_mkdir", name=name),
            side,
            self._with_device_lock(lambda: backend.mkdir(directory, name), backend),
        )

    def rename(self, backend: Backend, side: str, directory: str, old_name: str, new_name: str) -> None:
        self._submit(
            _("op_rename", name=old_name),
            side,
            self._with_device_lock(
                lambda: backend.rename(directory, old_name, new_name), backend
            ),
        )

    @staticmethod
    def _blocking_copy_many(
        src_backend: Backend,
        src_dir: str,
        entries: list[Entry],
        dst_backend: Backend,
        dst_dir: str,
    ) -> None:
        """Used internally for the copy step of a cross-boundary move -
        runs synchronously since it's already inside a worker thread."""
        names = [e.name for e in entries]
        if isinstance(src_backend, LocalBackend) and isinstance(dst_backend, DosBackend):
            unix_paths = [src_backend.join(src_dir, name) for name in names]
            dst_backend.copy_in_many(unix_paths, dst_dir)
        elif isinstance(src_backend, DosBackend) and isinstance(dst_backend, LocalBackend):
            src_backend.copy_out_many(src_dir, names, dst_dir)
        else:
            raise TypeError(_("err_unknown_pane_combination_move"))


# -- backup helpers for TransferService._with_replace_many -------------------

_BACKUP_TEMPLATE = "~MTG{:04d}.BAK"


def _backup_names(backend: Backend, directory: str, count: int) -> list[str]:
    """`count` backup names not already used in `directory` (compared
    case-insensitively, as on a DOS drive)."""
    try:
        taken = {e.name.upper() for e in backend.list_dir(directory)}
    except Exception:
        taken = set()  # a collision would only make the rename fail - handled
    names = []
    i = 1
    while len(names) < count:
        candidate = _BACKUP_TEMPLATE.format(i)
        if candidate.upper() not in taken:
            names.append(candidate)
        i += 1
    return names


def _restore_backups(
    backend: Backend, directory: str, done: list[tuple[Entry, str]], func_started: bool
) -> list[tuple[Entry, str]]:
    """Undo the backups actually made, most recent first. Returns those
    that couldn't be put back."""
    failed = []
    for entry, backup_name in reversed(done):
        if func_started:
            try:
                backend.delete(directory, entry.name, entry.is_dir)
            except Exception:
                pass  # nothing partial was left under the target name - fine
        try:
            backend.rename(directory, backup_name, entry.name)
        except Exception:
            failed.append((entry, backup_name))
    # Trust, but verify: every restored name must really be back.
    restored = [(e, b) for e, b in done if (e, b) not in failed]
    if restored:
        try:
            present = {e.name.upper() for e in backend.list_dir(directory)}
        except Exception:
            present = None  # can't check - the renames themselves succeeded
        if present is not None:
            failed += [(e, b) for e, b in restored if e.name.upper() not in present]
    return failed


def _describe_backups(pairs: list[tuple[Entry, str]]) -> str:
    return ", ".join(f"{entry.name} -> {backup}" for entry, backup in pairs)

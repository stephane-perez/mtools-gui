from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from . import Backend
from .. import mtools_client
from ..dos_names import assign_short_names, to_short_name
from ..entry import Entry

ROOT = "/"


class DosBackend(Backend):
    """DOS/FAT partition access through mtools, via the privileged helper.

    Paths are kept as posix-style strings relative to the DOS drive root
    (e.g. "/", "/SUBDIR"); they're translated to mtools' "::"-prefixed
    syntax only at the point of calling mtools_client.

    `device` is either a removable block device (/dev/sdX1, reached
    through the privileged helper) or the path of a floppy disk image
    file such as an Atari ST .ST (opened directly by mtools, with the
    user's own permissions) - mtools_client dispatches on which one it is.

    With `short_names` on, every name this backend *creates* (copy from
    Linux, new folder, rename) is converted to uppercase 8.3 first - see
    dos_names.py. Names already on the DOS side are never touched.
    """

    def __init__(self, device: str, short_names: bool = False):
        self.device = device
        self.short_names = short_names

    @property
    def is_image(self) -> bool:
        return mtools_client.is_image_target(self.device)

    def _dos_arg(self, path: str) -> str:
        return "::" if path in ("", ROOT) else f"::{path}"

    def list_dir(self, path: str) -> list[Entry]:
        return mtools_client.list_dir(self.device, self._dos_arg(path))

    def target_name(self, name: str) -> str:
        """The name `name` will actually get once created on this drive."""
        return to_short_name(name) if self.short_names else name

    def target_names(self, names: list[str]) -> list[str]:
        """Same as target_name, for a batch copied into one directory
        (identical converted names get told apart) - matches what
        copy_in_many will create."""
        return assign_short_names(names) if self.short_names else list(names)

    def mkdir(self, path: str, name: str) -> None:
        mtools_client.mkdir(self.device, self._dos_arg(self.join(path, self.target_name(name))))

    def delete(self, path: str, name: str, is_dir: bool) -> None:
        target = self._dos_arg(self.join(path, name))
        if is_dir:
            mtools_client.delete_recursive(self.device, target)
        else:
            mtools_client.delete(self.device, target)

    def rename(self, path: str, old_name: str, new_name: str) -> None:
        mtools_client.rename(
            self.device,
            self._dos_arg(self.join(path, old_name)),
            self._dos_arg(self.join(path, self.target_name(new_name))),
        )

    def read_text(self, path: str, name: str) -> str:
        return mtools_client.read_text(self.device, self._dos_arg(self.join(path, name)))

    def join(self, path: str, name: str) -> str:
        return str(PurePosixPath(path or ROOT) / name)

    def parent(self, path: str) -> str:
        parent = str(PurePosixPath(path or ROOT).parent)
        return parent

    def copy_in_many(self, unix_sources: list[str], dest_dir: str) -> None:
        """Copy multiple local files onto the DOS partition in one mtools call."""
        if not self.short_names or not _tree_needs_renaming(unix_sources):
            mtools_client.copy_many_unix_to_dos(self.device, unix_sources, self._dos_arg(dest_dir))
            return
        # mcopy -s names everything after its source, recursively, and
        # can't be told otherwise for the files inside a copied folder. So
        # the selection is first mirrored into a staging tree whose names
        # are already 8.3, then that tree goes through the usual single
        # mcopy call. Files are hard-linked when possible (no data copied).
        # The staging area lives under $HOME: the privileged helper only
        # accepts Unix paths inside the user's home or removable media.
        cache = Path.home() / ".cache" / "mtools_gui"
        cache.mkdir(parents=True, exist_ok=True)
        staging = tempfile.mkdtemp(prefix="staging-", dir=cache)
        try:
            names = assign_short_names([os.path.basename(p.rstrip("/")) for p in unix_sources])
            staged = []
            for source, short in zip(unix_sources, names):
                target = os.path.join(staging, short)
                _stage(source, target)
                staged.append(target)
            mtools_client.copy_many_unix_to_dos(self.device, staged, self._dos_arg(dest_dir))
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def copy_out_many(self, source_dir: str, source_names: list[str], unix_dest_dir: str) -> None:
        """Copy multiple files from the DOS partition into one local directory."""
        mtools_client.copy_many_dos_to_unix(
            self.device,
            [self._dos_arg(self.join(source_dir, name)) for name in source_names],
            unix_dest_dir,
        )

    def copy_within_many(self, names: list[str], src_dir: str, dst_dir: str) -> None:
        mtools_client.copy_within_dos_many(
            self.device,
            [self._dos_arg(self.join(src_dir, name)) for name in names],
            self._dos_arg(dst_dir),
        )

    def move_within_many(self, names: list[str], src_dir: str, dst_dir: str) -> None:
        mtools_client.move_within_dos_many(
            self.device,
            [self._dos_arg(self.join(src_dir, name)) for name in names],
            self._dos_arg(dst_dir),
        )

    def delete_many(self, path: str, entries: list[Entry]) -> None:
        # mdel and mdeltree are two different binaries (files vs.
        # directories), so a mixed selection needs at most two calls
        # instead of one - still far fewer than one per entry.
        files = [self._dos_arg(self.join(path, e.name)) for e in entries if not e.is_dir]
        dirs = [self._dos_arg(self.join(path, e.name)) for e in entries if e.is_dir]
        if files:
            mtools_client.delete_many(self.device, files)
        if dirs:
            mtools_client.delete_recursive_many(self.device, dirs)


def _tree_needs_renaming(unix_sources: list[str]) -> bool:
    """True if any name in the selection (recursively) isn't already
    uppercase 8.3 - otherwise the staging step can be skipped."""
    names = [os.path.basename(p.rstrip("/")) for p in unix_sources]
    if assign_short_names(names) != names:
        return True
    for source in unix_sources:
        if os.path.isdir(source):
            for _root, dirs, files in os.walk(source):
                children = dirs + files
                if assign_short_names(children) != children:
                    return True
    return False


def _stage(source: str, target: str) -> None:
    if os.path.isdir(source):
        os.mkdir(target)
        children = sorted(os.listdir(source))
        for child, short in zip(children, assign_short_names(children)):
            _stage(os.path.join(source, child), os.path.join(target, short))
        shutil.copystat(source, target)
        return
    try:
        os.link(source, target)
    except OSError:
        # Different filesystem (e.g. a source on /media), or links not
        # supported: fall back to a real copy, keeping the timestamps.
        shutil.copy2(source, target)

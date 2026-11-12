from __future__ import annotations

from pathlib import PurePosixPath

from . import Backend
from .. import mtools_client
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
    """

    def __init__(self, device: str):
        self.device = device

    @property
    def is_image(self) -> bool:
        return mtools_client.is_image_target(self.device)

    def _dos_arg(self, path: str) -> str:
        return "::" if path in ("", ROOT) else f"::{path}"

    def list_dir(self, path: str) -> list[Entry]:
        return mtools_client.list_dir(self.device, self._dos_arg(path))

    def mkdir(self, path: str, name: str) -> None:
        mtools_client.mkdir(self.device, self._dos_arg(self.join(path, name)))

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
            self._dos_arg(self.join(path, new_name)),
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
        mtools_client.copy_many_unix_to_dos(self.device, unix_sources, self._dos_arg(dest_dir))

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

"""DOS/FAT backend backed by a temporary raw image decoded from MSA."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .dos_backend import DosBackend
from ..msa import MsaError, decode_msa, write_msa_atomic


class MsaBackend(DosBackend):
    """Expose an MSA image through mtools using a temporary raw ST image.

    Changes are written back to the original MSA only when flush() succeeds.
    The original file is replaced atomically by the MSA writer.
    """

    def __init__(self, msa_path: str, short_names: bool = False):
        self.msa_path = os.path.abspath(msa_path)
        try:
            raw, self.geometry = decode_msa(Path(self.msa_path).read_bytes())
        except OSError:
            raise
        except MsaError:
            raise

        self._temporary_directory = tempfile.TemporaryDirectory(prefix="mtools-gui-msa-")
        self._temporary_image = os.path.join(self._temporary_directory.name, "disk.st")
        try:
            with open(self._temporary_image, "wb") as stream:
                stream.write(raw)
        except Exception:
            self._temporary_directory.cleanup()
            raise
        super().__init__(self._temporary_image, short_names=short_names)

    def flush(self) -> None:
        raw = Path(self.device).read_bytes()
        write_msa_atomic(self.msa_path, raw, self.geometry)

    def cleanup(self) -> None:
        self._temporary_directory.cleanup()

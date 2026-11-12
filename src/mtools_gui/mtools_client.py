"""Unprivileged-side client: builds `pkexec <helper> <op> <device> [args]`
calls and translates the helper's exit-code/stdout contract (see
system_files/mtools-gui-helper) into typed results/exceptions.
"""

from __future__ import annotations

import logging
import os
import subprocess

from .constants import HELPER_INSTALL_PATH, INSTALL_HELPER_COMMAND
from .i18n import _
from .mdir_parser import parse_mdir_output
from .entry import Entry

logger = logging.getLogger(__name__)

# mdir/mdel/mmd/mren/... only ever touch metadata (names, directory
# entries), never data proportional to file size, so they should always
# be near-instant - a generous ceiling still catches a genuinely wedged
# device without ever bothering a normal user. mcopy is the one
# operation whose duration legitimately scales with how much data is
# being moved, so it gets a much longer allowance - this is only meant
# to catch a truly hung device (unplugged mid-transfer, wedged kernel
# driver), not to bound ordinary large transfers.
HELPER_TIMEOUT_SECONDS = 60
HELPER_COPY_TIMEOUT_SECONDS = 600


class MtoolsClientError(Exception):
    """Base class for all errors raised by this module."""


class HelperNotInstalledError(MtoolsClientError):
    def __init__(self):
        super().__init__(_("err_helper_not_installed", command=INSTALL_HELPER_COMMAND))


class AuthDismissedError(MtoolsClientError):
    def __init__(self):
        super().__init__(_("err_auth_dismissed"))


class ValidationError(MtoolsClientError):
    pass


class MtoolsCommandError(MtoolsClientError):
    pass


class MtoolsTimeoutError(MtoolsClientError):
    def __init__(self, op: str, timeout: int):
        super().__init__(_("err_helper_timeout", op=op, timeout=timeout))


# -- Disk images (.ST, .IMG, ...) ----------------------------------------------
# A disk image is an ordinary file owned by the user, so mtools can open it
# directly (`mdir -i image.st ::`) with the user's own permissions - no
# pkexec, no privileged helper, no removable-device validation involved.
# Only real block devices (always under /dev/) go through the helper.
# Mirrors the helper's own mtools invocation: same binaries, same
# MTOOLS_SKIP_CHECK=1 (older Atari ST floppies made with early TOS ROMs
# have a BPB mtools' geometry check would otherwise reject), same "-s" for
# mcopy so directories are copied recursively.
DIRECT_OPS = {"mdir", "mcopy", "mdel", "mdeltree", "mmd", "mmove", "mren", "mattrib", "mrd", "mtype"}

# Runs as the user, so the user's own PATH is fine (and needed if mtools
# lives somewhere unusual, e.g. on an immutable distribution) - unlike the
# root helper, which deliberately pins /usr/bin.
DIRECT_ENV_OVERRIDES = {"LC_ALL": "C", "MTOOLS_SKIP_CHECK": "1"}


def is_image_target(target: str) -> bool:
    """True for a disk image file, False for a block device."""
    return not target.startswith("/dev/")


def _run_direct(op: str, image_path: str, args: list[str]) -> str:
    if op not in DIRECT_OPS:
        raise MtoolsClientError(_("err_usage_default"))
    extra_flags = ["-s"] if op == "mcopy" else []
    argv = [op, "-i", image_path, *extra_flags, *args]
    timeout = HELPER_COPY_TIMEOUT_SECONDS if op == "mcopy" else HELPER_TIMEOUT_SECONDS
    logger.debug("Direct mtools call: %s (timeout=%ss)", " ".join(argv), timeout)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **DIRECT_ENV_OVERRIDES},
            # mcopy may ask interactively on a name clash; never let it
            # wait for a keyboard that doesn't exist.
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        logger.error("%s not found", op)
        raise MtoolsClientError(_("err_mtools_not_found", op=op)) from exc
    except subprocess.TimeoutExpired as exc:
        logger.error("%s (image=%s) timed out after %ss", op, image_path, timeout)
        raise MtoolsTimeoutError(op, timeout) from exc

    if proc.returncode == 0:
        if proc.stderr.strip():
            logger.debug("%s (image=%s): mtools warning: %s", op, image_path, proc.stderr.strip())
        return proc.stdout
    logger.debug(
        "%s (image=%s) failed: code=%s stderr=%s",
        op, image_path, proc.returncode, proc.stderr.strip(),
    )
    raise MtoolsCommandError(proc.stderr.strip() or _("err_command_default", op=op))


def _run(op: str, target: str, args: list[str]) -> str:
    """Dispatch: a disk image is handled directly, a block device through
    the privileged helper."""
    if is_image_target(target):
        return _run_direct(op, target, args)
    return _run_helper(op, target, args)


def _run_helper(op: str, device: str, args: list[str]) -> str:
    argv = ["pkexec", HELPER_INSTALL_PATH, op, device, *args]
    timeout = HELPER_COPY_TIMEOUT_SECONDS if op == "mcopy" else HELPER_TIMEOUT_SECONDS
    logger.debug("Helper call: %s (timeout=%ss)", " ".join(argv), timeout)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        logger.error("pkexec not found")
        raise MtoolsClientError(_("err_pkexec_not_found")) from exc
    except subprocess.TimeoutExpired as exc:
        logger.error("%s (device=%s) timed out after %ss", op, device, timeout)
        raise MtoolsTimeoutError(op, timeout) from exc

    if proc.returncode == 4:
        logger.error("%s (device=%s) timed out inside the helper", op, device)
        raise MtoolsTimeoutError(op, timeout)

    if proc.returncode == 0:
        if proc.stderr.strip():
            # mtools sometimes prints a non-fatal warning to stderr even
            # on success (e.g. a BPB geometry mismatch on some GEMDOS
            # media) - log it, don't treat it as a failure.
            logger.debug("%s (device=%s): mtools warning: %s", op, device, proc.stderr.strip())
        return proc.stdout

    logger.debug(
        "%s (device=%s) failed: code=%s stderr=%s", op, device, proc.returncode, proc.stderr.strip()
    )
    if proc.returncode == 126:
        raise AuthDismissedError()
    if proc.returncode == 127:
        raise HelperNotInstalledError()
    if proc.returncode == 2:
        raise ValidationError(proc.stderr.strip() or _("err_validation_default"))
    if proc.returncode == 3:
        raise MtoolsClientError(proc.stderr.strip() or _("err_usage_default"))
    raise MtoolsCommandError(proc.stderr.strip() or _("err_command_default", op=op))


def list_dir(device: str, dos_path: str) -> list[Entry]:
    output = _run("mdir", device, [dos_path])
    return parse_mdir_output(output)


def delete(device: str, dos_path: str) -> None:
    _run("mdel", device, [dos_path])


def delete_recursive(device: str, dos_path: str) -> None:
    _run("mdeltree", device, [dos_path])


def mkdir(device: str, dos_path: str) -> None:
    _run("mmd", device, [dos_path])


def rmdir(device: str, dos_path: str) -> None:
    _run("mrd", device, [dos_path])


def rename(device: str, old_dos_path: str, new_dos_path: str) -> None:
    _run("mren", device, [old_dos_path, new_dos_path])


# -- Batch variants -----------------------------------------------------------
# A multi-select transfer used to submit one _run_helper call per file, each
# paying the full cost of a fresh pkexec+polkit round-trip, a new helper
# Python interpreter, its own lsblk device re-validation and mtools re-parsing
# the FAT tables from scratch - around a second of overhead per file, however
# small. mcopy/mmove/mdel/mdeltree all natively accept multiple source
# arguments in one invocation (targeting a single destination directory for
# mcopy/mmove), so a whole selection can go through that chain exactly once.
# No helper-side change was needed for this: validate_args already validates
# every argument in the list individually, regardless of how many there are.


def copy_many_unix_to_dos(device: str, unix_paths: list[str], dos_dir: str) -> None:
    _run("mcopy", device, [*unix_paths, dos_dir])


def copy_many_dos_to_unix(device: str, dos_paths: list[str], unix_dir: str) -> None:
    _run("mcopy", device, [*dos_paths, unix_dir])


def copy_within_dos_many(device: str, src_dos_paths: list[str], dst_dos_dir: str) -> None:
    _run("mcopy", device, [*src_dos_paths, dst_dos_dir])


def move_within_dos_many(device: str, src_dos_paths: list[str], dst_dos_dir: str) -> None:
    _run("mmove", device, [*src_dos_paths, dst_dos_dir])


def delete_many(device: str, dos_paths: list[str]) -> None:
    _run("mdel", device, dos_paths)


def delete_recursive_many(device: str, dos_paths: list[str]) -> None:
    _run("mdeltree", device, dos_paths)


def read_text(device: str, dos_path: str) -> str:
    return _run("mtype", device, [dos_path])

# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project follows [Semantic Versioning](https://semver.org/).

Versions before this file existed (0.1.0 - 0.4.1) were reconstructed
from the git history.

## [Unreleased]

### Added

- Floppy disk images: a **Disk image...** button, next to **Refresh
  drives**, mounts a raw FAT image file (Atari ST `.ST`, `.IMG`, `.IMA`,
  `.DSK`, `.VFD`, ...) in the right pane, and **Unmount image** releases
  it. The right pane shows either a drive or an image, never both. Images
  are opened by mtools directly with the user's own permissions: the
  privileged helper isn't involved. The mounted image and its current
  folder are restored on the next launch.
- 8.3 file names: files and folders copied from Linux (including the
  contents of copied folders), created or renamed on the DOS side get an
  uppercase 8.3 name (`Un fichier très long.txt` -> `UNFICHIE.TXT`), so
  Atari TOS/GEMDOS and DOS can see them - no VFAT long names. Controlled
  by an **8.3 names** checkbox, on by default and remembered.
- README: install instructions for immutable systems (Fedora Atomic).

### Changed

- The overwrite confirmation compares names as they will actually be
  written (after 8.3 conversion, case-insensitively on the DOS side), and
  lists the existing destination names that are about to be replaced.

### Fixed

- Tests no longer hang on a machine where the privileged helper isn't
  installed, and no longer depend on a real removable device being
  plugged in.

## [0.4.1] - 2026-09-07

### Fixed

- Opening a local folder you aren't allowed to read no longer leaves the
  pane pointing at that folder while still showing the previous one.

### Changed

- mtools always runs with `MTOOLS_SKIP_CHECK=1`, so older Atari disks
  whose boot sector geometry mtools would otherwise reject can be read.

## [0.4.0] - 2026-09-07

### Added

- GitHub Actions: tests on Python 3.10-3.12, a `pipx install` smoke test,
  and an AppImage built and published for every `v*` tag.

### Changed

- A multi-file copy, move or delete now runs as a single mtools call
  instead of one per file (each call costs a password-less pkexec
  round-trip, about a second), which makes large selections much faster.

## [0.3.0] - 2026-09-06

### Security

- Timeouts on every helper and mtools call, so an unplugged or wedged
  device can't hang the application or the root helper.
- Operations on the same device are serialized: mtools never runs twice
  at the same time against one FAT volume.
- The helper re-checks a path is still allowed right before handing
  copied files back to the user (narrower TOCTOU window).

### Fixed

- Directory listings with VFAT long file names are parsed correctly.
- French interface text now has its accents.

## [0.2.0] - 2026-09-06

### Added

- AppImage packaging, in addition to pipx.
- An **Install now** button installs the privileged helper from the
  application itself (via pkexec), instead of a terminal command.
- The status bar shows the operations in progress.
- Confirmation before overwriting existing files or folders at the
  destination.

### Changed

- GPLv3 license.
- English documentation.

## [0.1.0] - 2026-09-04

### Added

- First version: a two-pane file manager between the local Linux
  filesystem and a DOS/FAT partition on a removable drive, through
  mtools and a narrowly validated privileged helper (pkexec + polkit).
- Copy, move (button or Alt+drag-and-drop), rename, delete, new folder,
  text file viewer, drag-and-drop between panes, sortable columns.
- Automatic discovery of removable drives, including partitions the
  kernel doesn't recognize as FAT (e.g. Atari GEMDOS).
- French and English interface.

# mtools-gui

A two-pane file manager (Midnight/Total Commander style) for exchanging
files between the local Linux filesystem and a DOS/FAT partition on a
removable drive (an SD card, a USB drive, ...), via
[mtools](https://www.gnu.org/software/mtools/) rather than a kernel
mount.

mtools' official graphical frontend, [MToolsFM](https://mtoolsfm.sourceforge.net/),
hasn't had a release since 2008 and is hard to build on current
distributions (it depends on an obsolete GTK version) - hence this
project.

## Authorship

Every line of code in this project was written by Claude (Anthropic's
AI model), through an extended pair-programming conversation with
Stéphane Perez, who directed the design, decided every feature and fix, 
and tested each change against real hardware (an Atari SD card).

## Installation

Two ways to install, pick whichever suits you:

- **AppImage**: a single ~80 MB file (it bundles its own Python and Qt,
  so it works regardless of what's already on your system) - no
  installation, no dependencies, but a bigger download.
- **pipx**: a much smaller download, but requires Python and pipx to
  already be installed.

### AppImage (no Python/pip needed)

Download `mtools_gui-x86_64.AppImage` from the
[releases page](https://github.com/stephane-perez/mtools-gui/releases),
then:

```bash
chmod +x mtools_gui-x86_64.AppImage
./mtools_gui-x86_64.AppImage
```

Needs glibc >= 2.28 (Ubuntu 18.10+/20.04/22.04, Debian 10+, Fedora 29+,
and most distributions from the last several years). To build it
yourself: `packaging/appimage/build.sh` (needs network access, to
download a base Python AppImage and appimagetool).

### From source (pipx)

Needs Python >= 3.10 and [pipx](https://pipx.pypa.io/) (`apt install
pipx` / `dnf install pipx` / `pip install --user pipx`).

On immutable systems such as Fedora Atomic images, run:
`python -m pip install pipx`

`pipx install .` installs from a local copy of the source, so it needs
one first - pick whichever of these two is easiest:

**Option A - let pipx fetch it, no manual clone needed:**

```bash
pipx install "git+https://github.com/stephane-perez/mtools_gui.git"
```

On immutable systems such as Fedora Atomic images, run:

```bash
python -m pipx install "git+https://github.com/stephane-perez/mtools_gui.git"
```

**Option B - clone it yourself** (handy if you also want to read the
code, switch branches, or build the AppImage locally):

```bash
git clone https://github.com/stephane-perez/mtools_gui.git
cd mtools_gui
pipx install .
```

Either way, this doesn't need to happen in any particular directory -
`git clone` creates its own `mtools_gui/` subfolder wherever you run it
(your home directory is a fine default), and `pipx install` copies what
it needs into its own isolated location; the cloned folder can be
deleted afterwards (Option B) or was never created in the first place
(Option A).

The left pane (local files) works right away. The right pane (a DOS
drive) needs one extra one-time step first: `mtools` needs root access
to read/write a raw block device (an SD card, a USB key), so instead of
running the whole GUI as root, mtools_gui installs one small dedicated
helper program that does nothing except run mtools commands against the
removable drive you pick - see [Security](#security) below for how
that's kept narrow.

Either click "Install now" when the app prompts for it on first launch
(it'll explain the same thing and ask for your password), or run this
once manually in a terminal:

```bash
sudo "$(command -v mtools-gui-install-helper)"
```

This installs, once per machine:
- `/usr/local/libexec/mtools-gui-helper`: the small privileged program
  mentioned above - the only thing on the system that actually runs
  mtools commands against the chosen device.
- `/etc/polkit-1/rules.d/49-mtools-gui.rules`: a polkit rule that lets
  the active graphical session's user run this one program (and only
  this one) without a password each time.

## Running the application

```bash
mtools-gui
```

## 8.3 file names

Atari TOS/GEMDOS and plain DOS only understand short `NAME.EXT` names
(8 characters, a 3-character extension, uppercase); a VFAT long name is
invisible to them. With **8.3 names** ticked (the default, next to the
drive picker), every file or folder copied from Linux, created or
renamed on the DOS side gets an uppercase 8.3 name - e.g.
`Un fichier très long.txt` becomes `UNFICHIE.TXT`, `Mon Dossier` becomes
`MONDOSSI`, including inside copied folders. Accents are dropped
(`été` -> `ETE`), spaces removed, other forbidden characters replaced by
`_`; two names that would collide in the same copy are told apart with
`~1`, `~2`, ... Names already on the drive are never changed. Untick it
to keep long names (for a PC-only FAT drive, for instance).

## Floppy disk images

Instead of a physical drive, the right pane can also show the contents
of a floppy disk image file - e.g. an Atari ST `.ST` image (also `.IMG`,
`.IMA`, `.DSK`, `.VFD`, or any raw FAT image). Click **Disk image...**
(next to **Refresh drives**), pick the file, and copy, move, rename,
delete or create files and folders inside it exactly as on a real drive.
**Unmount image** releases it.

The right pane shows either a drive or an image, never both: mounting
an image deselects the drive, and choosing a drive unmounts the image.

An image is an ordinary file you own, so mtools opens it directly with
your own permissions - the privileged helper isn't involved (and doesn't
even need to be installed for this). Compressed formats such as `.MSA`
aren't raw FAT images and can't be opened by mtools: convert them to
`.ST` first.

## Language

The interface is in French or English depending on the system locale
(`fr_*` -> French, everything else -> English). To force a language:

```bash
MTOOLS_GUI_LANG=en mtools-gui
```

## System requirements

- [mtools](https://www.gnu.org/software/mtools/): a collection of
  command-line tools (`mcopy`, `mdir`, `mdel`, `mmd`, ...) that read and
  write MS-DOS/FAT disks directly, without mounting them.
  - Debian/Ubuntu and derivatives: `apt install mtools`
  - Fedora/RHEL/CentOS and other RPM-based distributions: `dnf install mtools`
    (`yum install mtools` on older releases)
  - Arch Linux: `pacman -S mtools`
  - Or grab a `.deb`/`.rpm` package directly from the
    [official mtools downloads](https://www.gnu.org/software/mtools/).
- `polkit`/`pkexec` (present by default on most modern desktop
  distributions).
- `lsblk` (util-linux, present by default).

## Security

See the comments in `src/mtools_gui/system_files/mtools-gui-helper`: the
helper independently validates (via `lsblk` AND `/sys/block/*/removable`)
that the target device is actually removable before any operation, and
confines any Unix path passed to `mcopy` to the user's home directory or
the usual removable-media mount roots.

## Uninstalling the privileged helper

```bash
sudo "$(command -v mtools-gui-install-helper)" --uninstall
```

## License

GPLv3 or later - see [LICENSE](LICENSE).

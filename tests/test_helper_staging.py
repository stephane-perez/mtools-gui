"""The privileged helper never reads or writes a Unix-side path as root:
mcopy works on a private staging directory, and the Unix side of the
transfer is done by a child process running as the invoking user, the two
exchanging a tar stream (see the helper's module docstring). This closes
the TOCTOU window between validating a path and root opening it.

The end-to-end tests run the real mtools binaries on a disk image (the
device validation is stubbed) and are skipped without mtools. They don't
need root: the privilege drop is stubbed out there and tested on its own,
when the tests do run as root.
"""

import io
import os
import shutil
import subprocess
import sys
import tarfile
from importlib.machinery import SourceFileLoader
import importlib.util
from pathlib import Path

import pytest

HELPER_PATH = Path(__file__).parent.parent / "src" / "mtools_gui" / "system_files" / "mtools-gui-helper"
MTOOLS_ENV = {"MTOOLS_SKIP_CHECK": "1", "PATH": "/usr/bin:/bin"}

needs_mtools = pytest.mark.skipif(shutil.which("mformat") is None, reason="mtools not installed")


@pytest.fixture
def helper():
    loader = SourceFileLoader("mtools_gui_helper_staging", str(HELPER_PATH))
    spec = importlib.util.spec_from_loader("mtools_gui_helper_staging", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# -- classify_mcopy -------------------------------------------------------------


def test_classify_mcopy(helper):
    assert helper.classify_mcopy(["::/A", "::/B", "::/DEST"]) == "dos"
    assert helper.classify_mcopy(["/home/u/a", "/home/u/b", "::/DEST"]) == "to_dos"
    assert helper.classify_mcopy(["::/A", "::/B", "/home/u/dst"]) == "from_dos"


@pytest.mark.parametrize(
    "args",
    [
        ["/home/u/a", "::/A", "::/DEST"],  # mixed sources
        ["/home/u/a", "/home/u/dst"],  # Unix to Unix: never mtools' job
        ["/home/u/x/a", "/home/u/y/a", "::/DEST"],  # same name twice
    ],
)
def test_classify_mcopy_rejects(helper, args):
    with pytest.raises(helper.ValidationError):
        helper.classify_mcopy(args)


def test_classify_mcopy_needs_a_source_and_a_target(helper):
    with pytest.raises(helper.UsageError):
        helper.classify_mcopy(["::/A"])


# -- _checked_members: what root accepts from the user-side stream --------------


def _tar_with(*members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for info, data in members:
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    buf.seek(0)
    return tarfile.open(fileobj=buf, mode="r")


def _file(name, data=b"x", **attrs):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    for k, v in attrs.items():
        setattr(info, k, v)
    return info, data


def _special(name, type_, linkname=""):
    info = tarfile.TarInfo(name)
    info.type = type_
    info.linkname = linkname
    return info, None


def test_checked_members_accepts_files_and_dirs_and_neutralizes_owner_and_mode(helper):
    d = tarfile.TarInfo("DIR")
    d.type = tarfile.DIRTYPE
    tar = _tar_with(
        _file("A.TXT", uid=1000, mode=0o4777),
        (d, None),
        _file("DIR/B.TXT"),
    )
    members = list(helper._checked_members(tar, {"A.TXT", "DIR"}))
    assert [m.name for m in members] == ["A.TXT", "DIR", "DIR/B.TXT"]
    assert members[0].uid == 0 and members[0].mode == 0o644
    assert members[1].mode == 0o755


@pytest.mark.parametrize(
    "member",
    [
        _special("A.TXT", tarfile.SYMTYPE, "/etc/shadow"),
        _special("A.TXT", tarfile.LNKTYPE, "/etc/shadow"),
        _special("A.TXT", tarfile.CHRTYPE),
        _file("/etc/cron.d/evil"),
        _file("A.TXT/../../etc/passwd"),
        _file("OTHER.TXT"),  # not one of the requested sources
    ],
)
def test_checked_members_rejects_anything_else(helper, member):
    tar = _tar_with(member)
    with pytest.raises(helper.TransferError):
        list(helper._checked_members(tar, {"A.TXT"}))


# -- privilege drop (only meaningful as root) ------------------------------------


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to drop privileges")
def test_user_preexec_really_drops_to_the_invoking_user(helper):
    import pwd

    nobody = pwd.getpwnam("nobody")
    out = subprocess.run(
        ["id", "-u"],
        preexec_fn=helper._user_preexec((nobody.pw_uid, nobody.pw_gid, "nobody")),
        capture_output=True,
        text=True,
        cwd="/",
    ).stdout.strip()
    assert out == str(nobody.pw_uid)


# -- end to end through main(), real mtools on an image ---------------------------


@pytest.fixture
def setup(helper, tmp_path, monkeypatch):
    image = tmp_path / "card.st"
    subprocess.run(["mformat", "-C", "-f", "720", "-a", "-i", str(image), "::"], check=True, capture_output=True)
    home = tmp_path / "home"
    (home / "src" / "DIR").mkdir(parents=True)
    (home / "src" / "A.TXT").write_text("hello\n")
    (home / "src" / "DIR" / "B.TXT").write_text("b\n")
    (home / "out").mkdir()
    staging_parent = tmp_path / "staging"
    staging_parent.mkdir()

    monkeypatch.setattr(helper, "validate_device", lambda d: d)
    monkeypatch.setattr(helper, "_invoking_home_dir", lambda: str(home))
    monkeypatch.setattr(helper, "_invoking_identity", lambda: (os.getuid(), os.getgid(), "me"))
    monkeypatch.setattr(helper, "_user_preexec", lambda identity: None)  # already unprivileged here
    monkeypatch.setattr(helper, "STAGING_PARENT", str(staging_parent))
    monkeypatch.setattr(helper, "HELPER_ENV", {**helper.HELPER_ENV, "PATH": "/usr/bin:/bin"})
    return image, home, staging_parent


def _ls(image):
    out = subprocess.run(["mdir", "-b", "-/", "-i", str(image), "::"], capture_output=True, text=True, env=MTOOLS_ENV)
    return sorted(out.stdout.split())


@needs_mtools
def test_local_to_dos_and_back(helper, setup):
    image, home, staging_parent = setup

    rc = helper.main(["h", "mcopy", str(image), str(home / "src" / "A.TXT"), str(home / "src" / "DIR"), "::/"])
    assert rc == 0
    assert _ls(image) == ["::/A.TXT", "::/DIR/", "::/DIR/B.TXT"]

    rc = helper.main(["h", "mcopy", str(image), "::/A.TXT", "::/DIR", str(home / "out")])
    assert rc == 0
    assert (home / "out" / "A.TXT").read_text() == "hello\n"
    assert (home / "out" / "DIR" / "B.TXT").read_text() == "b\n"

    assert os.listdir(staging_parent) == []  # staging always cleaned up


@needs_mtools
def test_hostile_stream_from_the_user_side_is_refused(helper, setup, capsys):
    image, home, staging_parent = setup
    # A user-side process that, instead of packing the requested file,
    # sends a symlink to a root-only file.
    helper._PACK_SCRIPT = """
import sys, tarfile
with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as tar:
    info = tarfile.TarInfo("A.TXT"); info.type = tarfile.SYMTYPE; info.linkname = "/etc/shadow"
    tar.addfile(info)
"""
    rc = helper.main(["h", "mcopy", str(image), str(home / "src" / "A.TXT"), "::/"])

    assert rc == 1
    assert "inattendu" in capsys.readouterr().err
    assert _ls(image) == []
    assert os.listdir(staging_parent) == []


@needs_mtools
def test_user_side_failure_is_reported(helper, setup, capsys):
    image, home, _staging = setup
    rc = helper.main(["h", "mcopy", str(image), str(home / "src" / "MISSING.TXT"), "::/"])
    assert rc == 1
    assert "FileNotFoundError" in capsys.readouterr().err

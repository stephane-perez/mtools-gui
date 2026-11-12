"""Conversion of arbitrary Linux file names to DOS 8.3 short names
(uppercase, at most 8 characters + a 3-character extension).

Why: when given a name that doesn't fit 8.3 (too long, lowercase, spaces,
accents...), mtools writes a VFAT long-filename entry next to an
auto-generated short one. Atari TOS/GEMDOS (and plain DOS) ignore VFAT
entries and only ever see that auto-generated "UNFICH~1.TXT", while
lowercase-but-8.3 names get a Windows NT case flag GEMDOS doesn't know
either. mtools' own `MTOOLS_NO_VFAT` setting would be the natural fix but
still produced VFAT entries when tried with mtools 4.0.43, so names are
converted here instead, before mtools ever sees them: a name that is
already uppercase 8.3 makes mtools write a plain short entry only.

The conversion favours readable truncation ("Un fichier très long.txt"
-> "UNFICHIE.TXT") over Windows-style "~1" suffixes, which are only used
to tell apart two names of the same batch that would otherwise collide.
"""

from __future__ import annotations

import string
import unicodedata

# Characters allowed in a DOS short name besides letters and digits.
_ALLOWED = frozenset(string.ascii_uppercase + string.digits + "!#$%&'()-@^_`{}~")
_BASE_LEN = 8
_EXT_LEN = 3


def _clean(part: str) -> str:
    # NFKD + ASCII drops accents ("é" -> "e") instead of replacing the
    # whole letter with "_".
    ascii_part = unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode("ascii")
    out = []
    for char in ascii_part.upper():
        if char in _ALLOWED:
            out.append(char)
        elif char in " .":
            continue  # no spaces in a short name; extra dots just vanish
        else:
            out.append("_")
    return "".join(out)


def _split(name: str) -> tuple[str, str]:
    stripped = name.strip(" .")
    base, dot, ext = stripped.rpartition(".")
    if not dot:
        return stripped, ""
    return base, ext


def to_short_name(name: str) -> str:
    """Convert one name to uppercase 8.3. Idempotent: a valid uppercase 8.3
    name is returned unchanged."""
    base, ext = _split(name)
    base = _clean(base)[:_BASE_LEN] or "_"
    ext = _clean(ext)[:_EXT_LEN]
    return f"{base}.{ext}" if ext else base


def is_short_name(name: str) -> bool:
    return to_short_name(name) == name


def assign_short_names(names: list[str]) -> list[str]:
    """Convert a whole batch of names living in the same directory,
    disambiguating any that would end up identical with a "~N" suffix
    (e.g. "Rapport 2024.txt" and "rapport_2024.txt"). Same order as
    `names`, and deterministic: the same input always gives the same
    output."""
    used: set[str] = set()
    result = []
    for name in names:
        short = to_short_name(name)
        if short in used:
            base, _dot, ext = short.partition(".")
            i = 1
            while True:
                suffix = f"~{i}"
                candidate = base[: _BASE_LEN - len(suffix)] + suffix + (f".{ext}" if ext else "")
                if candidate not in used:
                    short = candidate
                    break
                i += 1
        used.add(short)
        result.append(short)
    return result

"""Find the declared shape under the declared roots.

Two decisions worth knowing about, both measured rather than assumed:

Roots are declared and never inferred. `find / -name Makefile` did not finish
inside two minutes on the runner this was written on. Declaring `/usr/src` and
`/usr/share/doc` takes under a second for the same shape.

Overlapping roots are deduplicated by resolved path. Writing `/usr` and
`/usr/src` in one shape is an easy mistake and would otherwise double the
apparent size of the corpus, which is the one number the floor is checked
against.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Located:
    files: tuple[Path, ...]
    per_root: dict[str, int]
    missing_roots: tuple[str, ...]

    @property
    def total(self) -> int:
        return len(self.files)


def _matches(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def find(
    roots: tuple[Path, ...],
    names: tuple[str, ...],
    skip_names: tuple[str, ...] = (),
) -> Located:
    """Walk `roots` for regular files whose basename matches `names`.

    Symlinks are skipped, both as files and as directories: a distribution
    tree is full of them and following them turns one input into five copies
    of the same input, or a loop.
    """
    found: dict[Path, None] = {}
    per_root: dict[str, int] = {}
    missing: list[str] = []

    for root in roots:
        key = str(root)
        per_root[key] = 0
        if not root.is_dir():
            missing.append(key)
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not Path(dirpath, d).is_symlink()]
            for filename in filenames:
                if not _matches(filename, names):
                    continue
                if skip_names and _matches(filename, skip_names):
                    continue
                p = Path(dirpath, filename)
                if p.is_symlink() or not p.is_file():
                    continue
                try:
                    resolved = p.resolve()
                except OSError:
                    continue
                if resolved in found:
                    continue
                found[resolved] = None
                per_root[key] += 1

    return Located(
        files=tuple(sorted(found)),
        per_root=per_root,
        missing_roots=tuple(missing),
    )


def sample(files: tuple[Path, ...], limit: int | None) -> tuple[Path, ...]:
    """Take a stable pseudo-random subset of `files`.

    Ordered by a hash of the path rather than by the path itself. Sorting by
    path and taking the first N gives you one directory, which on a kernel
    tree means the corpus is 40 makefiles from `arch/alpha` and nothing else.
    The hash makes the sample spread out and makes it the same on every run of
    the same box, so a failure can be reproduced. It is not the same across
    boxes, because the file list is not either.
    """
    if limit is None or len(files) <= limit:
        return files
    keyed = sorted(files, key=lambda p: hashlib.sha256(str(p).encode()).hexdigest())
    return tuple(sorted(keyed[:limit]))

"""Find the declared shape under the declared roots.

Three decisions worth knowing about, all measured rather than assumed:

Roots are declared and never inferred. `find / -name Makefile` did not finish
inside two minutes on the runner this was written on. Declaring `/usr/src` and
`/usr/share/doc` takes under a second for the same shape.

Overlapping roots are deduplicated by resolved path. Writing `/usr` and
`/usr/src` in one shape is an easy mistake and would otherwise double the
apparent size of the corpus, which is the one number the floor is checked
against.

A `names` pattern is matched against as many trailing path components as it
has, not against the basename. The discriminator for the shape you want is
often a directory and not a filename -- there are 81 GitHub Actions workflow
files on this runner and 9,718 `*.yml`, and the only thing that tells them
apart is the `.github/workflows/` above them. A pattern with one component is
a basename match, which is what every pattern was before this existed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Located:
    files: tuple[Path, ...]
    per_root: dict[str, int]
    missing_roots: tuple[str, ...]
    # Resolved input path -> the directory the pattern began matching at. For a
    # basename pattern that is the file's own directory, so this is an identity
    # for every corpus written before `{tree}` existed.
    trees: dict[Path, Path] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.files)

    @property
    def tree_count(self) -> int:
        """How many distinct `{tree}` values the inputs have.

        Reported next to the file count for a tree-scoped shape, because there
        the file count overstates how much variety is in the corpus: 81
        workflow files across 30 projects is 30 strangers' habits and not 81.
        Never asserted on -- the floor counts inputs.
        """
        return len(set(self.trees.values()))

    @property
    def tree_scoped(self) -> bool:
        """Whether any input's tree is something other than its own directory.

        Which is to say: whether a `names` pattern with more than one component
        matched anything. For a basename shape `tree_count` is the number of
        distinct directories -- true, and not what anyone reading a line about
        trees would take it for, so the text report leaves it out.
        """
        return any(tree != path.parent for path, tree in self.trees.items())

    def tree_of(self, path: Path) -> Path:
        """The `{tree}` for an input, defaulting to its own directory.

        A `Located` built by hand (tests, and the odd caller that only has
        paths) has no `trees`, and a `KeyError` from the middle of a corpus
        walk is exactly the sort of failure this package has had before.
        """
        return self.trees.get(path, path.parent)


def _spec(pattern: str) -> tuple[str, ...]:
    """Split a `names` pattern into the components it matches.

    Validation of the parts lives in `config`, which can name the file and the
    shape; by the time a pattern is here it has been through that.
    """
    return tuple(p for p in pattern.split("/") if p)


def _match_depth(parts: tuple[str, ...], specs: tuple[tuple[str, ...], ...]) -> int | None:
    """How many trailing components of `parts` a pattern claimed, or None.

    The first pattern in declared order wins, so which pattern a file is
    credited to -- and therefore its `{tree}` -- does not depend on the order
    the filesystem happened to be walked in.

    Matching is against the end of the absolute path rather than against the
    path relative to the root. That makes the result independent of how the
    roots were sliced: `/usr/local/lib` and `/usr/local/lib/node_modules` find
    the same files with the same trees, which is the same property the dedup
    is after.
    """
    for spec in specs:
        if len(spec) > len(parts):
            continue
        tail = parts[len(parts) - len(spec) :]
        if all(fnmatch.fnmatch(part, pat) for part, pat in zip(tail, spec)):
            return len(spec)
    return None


def find(
    roots: tuple[Path, ...],
    names: tuple[str, ...],
    skip_names: tuple[str, ...] = (),
) -> Located:
    """Walk `roots` for regular files whose path tail matches `names`.

    Symlinks are skipped, both as files and as directories: a distribution
    tree is full of them and following them turns one input into five copies
    of the same input, or a loop.
    """
    specs = tuple(_spec(n) for n in names)
    skip_specs = tuple(_spec(n) for n in skip_names)
    # A basename pattern needs no path split, and the split is the only new
    # cost in the walk. node_modules is 150k files and most shapes still ask
    # one fnmatch of one string.
    deep = any(len(s) > 1 for s in specs + skip_specs)

    found: dict[Path, None] = {}
    trees: dict[Path, Path] = {}
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
            # Dropping the leading "/" keeps the root anchor out of the
            # comparison: `fnmatch("/", "*")` is true, so a pattern with more
            # components than the path has depth would otherwise match it.
            dir_parts = tuple(Path(dirpath).parts[1:]) if deep else ()
            for filename in filenames:
                parts = dir_parts + (filename,)
                depth = _match_depth(parts, specs)
                if depth is None:
                    continue
                if skip_specs and _match_depth(parts, skip_specs) is not None:
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
                # Taken on the resolved path, so the tree handed to a checker
                # is an ancestor of the path the report prints. depth is 1 for
                # a basename pattern and parents[0] is the file's own
                # directory, which is what `{dir}` has always been. The match
                # ran against the unresolved path, so clamp: resolving can
                # shorten a path and `/` has no parent to climb to.
                parents = resolved.parents
                trees[resolved] = parents[min(depth - 1, len(parents) - 1)]
                per_root[key] += 1

    return Located(
        files=tuple(sorted(found)),
        per_root=per_root,
        missing_roots=tuple(missing),
        trees=trees,
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

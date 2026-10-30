from pathlib import Path

from stockbox.locate import find, sample


def write(root: Path, rel: str, text: str = "x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_matches_exact_names_and_globs(tmp_path):
    write(tmp_path, "a/Makefile")
    write(tmp_path, "b/rules.mk")
    write(tmp_path, "c/README")
    located = find((tmp_path,), ("Makefile", "*.mk"))
    assert {p.name for p in located.files} == {"Makefile", "rules.mk"}
    assert located.total == 2


def test_missing_root_is_reported_not_fatal(tmp_path):
    write(tmp_path, "a/Makefile")
    located = find((tmp_path, Path("/nonexistent-stockbox-root")), ("Makefile",))
    assert located.total == 1
    assert located.missing_roots == ("/nonexistent-stockbox-root",)
    assert located.per_root["/nonexistent-stockbox-root"] == 0


def test_overlapping_roots_do_not_double_count(tmp_path):
    # Declaring /usr and /usr/src in one shape is an easy mistake, and it would
    # otherwise double the corpus size the floor is measured against.
    write(tmp_path, "inner/Makefile")
    located = find((tmp_path, tmp_path / "inner"), ("Makefile",))
    assert located.total == 1


def test_symlinked_files_are_skipped(tmp_path):
    real = write(tmp_path, "real/Makefile")
    (tmp_path / "link").mkdir()
    (tmp_path / "link" / "Makefile").symlink_to(real)
    located = find((tmp_path,), ("Makefile",))
    assert located.files == (real,)


def test_symlinked_directories_are_not_followed(tmp_path):
    write(tmp_path, "real/Makefile")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    located = find((tmp_path,), ("Makefile",))
    assert located.total == 1


def test_directory_named_like_the_shape_is_not_an_input(tmp_path):
    (tmp_path / "Makefile").mkdir()
    write(tmp_path, "real/Makefile")
    located = find((tmp_path,), ("Makefile",))
    assert [p.name for p in located.files] == ["Makefile"]
    assert located.files[0].parent.name == "real"


def test_skip_names_removes_matches(tmp_path):
    write(tmp_path, "a/Makefile")
    write(tmp_path, "b/Makefile.in")
    located = find((tmp_path,), ("Makefile*",), skip_names=("*.in",))
    assert {p.name for p in located.files} == {"Makefile"}


def test_per_root_counts_attribute_to_the_root_that_found_it(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    write(one, "Makefile")
    write(two, "a/Makefile")
    write(two, "b/Makefile")
    located = find((one, two), ("Makefile",))
    assert located.per_root[str(one)] == 1
    assert located.per_root[str(two)] == 2


def test_results_are_sorted(tmp_path):
    for name in ("c", "a", "b"):
        write(tmp_path, f"{name}/Makefile")
    located = find((tmp_path,), ("Makefile",))
    assert list(located.files) == sorted(located.files)


class TestPathPatterns:
    """A pattern claims as many trailing components as it has (logbook#56).

    The bug these are about is not subtle once you see it: with the whole
    pattern fnmatched against the basename, `.github/workflows/*.yml` matched
    nothing at all, and nothing in the output distinguished that from a machine
    with no workflow files on it.
    """

    def test_a_directory_pattern_matches_by_path(self, tmp_path):
        want = write(tmp_path, "proj/.github/workflows/ci.yml")
        write(tmp_path, "proj/other.yml")
        write(tmp_path, "proj/workflows/ci.yml")  # no .github above it
        located = find((tmp_path,), (".github/workflows/*.yml",))
        assert located.files == (want,)

    def test_the_pattern_matches_at_any_depth(self, tmp_path):
        shallow = write(tmp_path, ".github/workflows/a.yml")
        deep = write(tmp_path, "a/b/c/d/.github/workflows/b.yml")
        located = find((tmp_path,), (".github/workflows/*.yml",))
        assert set(located.files) == {shallow, deep}

    def test_a_star_does_not_cross_a_slash(self, tmp_path):
        # fnmatch on its own would: it compiles `*` to `.*`, which eats
        # separators and makes the component count meaningless.
        write(tmp_path, "a/b/c.yml")
        assert find((tmp_path,), ("a/*.yml",)).total == 0
        assert find((tmp_path,), ("*/*.yml",)).total == 1

    def test_a_wildcard_directory_component_works(self, tmp_path):
        write(tmp_path, "proj/.github/workflows/ci.yml")
        write(tmp_path, "proj/.gitlab/workflows/ci.yml")
        located = find((tmp_path,), (".git*/workflows/*.yml",))
        assert located.total == 2

    def test_one_component_is_still_a_basename_match(self, tmp_path):
        # Every corpus written before this existed, which must keep working.
        write(tmp_path, "a/b/c/Makefile")
        assert find((tmp_path,), ("Makefile",)).total == 1

    def test_matching_is_against_the_end_of_the_path_not_relative_to_the_root(
        self, tmp_path
    ):
        # So slicing the roots differently cannot change what matches, which is
        # the property the dedup is after too.
        write(tmp_path, "proj/.github/workflows/ci.yml")
        deep_root = tmp_path / "proj" / ".github" / "workflows"
        assert find((tmp_path,), (".github/workflows/*.yml",)).total == 1
        assert find((deep_root,), (".github/workflows/*.yml",)).total == 1

    def test_a_pattern_deeper_than_the_path_matches_nothing(self, tmp_path):
        # `fnmatch("/", "*")` is true, so the leading anchor has to be dropped
        # before the comparison or a long pattern matches a short path.
        p = write(tmp_path, "x.yml")
        depth = len(p.parts)
        assert find((tmp_path,), ("/".join(["*"] * (depth + 2)),)).total == 0

    def test_skip_names_takes_a_path_pattern_too(self, tmp_path):
        keep = write(tmp_path, "proj/.github/workflows/ci.yml")
        write(tmp_path, "vendor/.github/workflows/ci.yml")
        located = find(
            (tmp_path,),
            (".github/workflows/*.yml",),
            skip_names=("vendor/.github/workflows/*.yml",),
        )
        assert located.files == (keep,)

    def test_per_root_counting_survives_a_path_pattern(self, tmp_path):
        one, two = tmp_path / "one", tmp_path / "two"
        write(one, "p/.github/workflows/a.yml")
        write(two, "q/.github/workflows/a.yml")
        write(two, "q/.github/workflows/b.yml")
        located = find((one, two), (".github/workflows/*.yml",))
        assert located.per_root[str(one)] == 1
        assert located.per_root[str(two)] == 2


class TestTree:
    def test_the_tree_is_where_the_pattern_started_matching(self, tmp_path):
        p = write(tmp_path, "proj/.github/workflows/ci.yml")
        located = find((tmp_path,), (".github/workflows/*.yml",))
        assert located.tree_of(p) == tmp_path / "proj"
        assert located.tree_scoped
        assert located.tree_count == 1

    def test_a_basename_pattern_has_the_files_own_directory_as_its_tree(self, tmp_path):
        p = write(tmp_path, "a/b/Makefile")
        located = find((tmp_path,), ("Makefile",))
        assert located.tree_of(p) == p.parent
        # Nothing here is tree-scoped, so the report says nothing about trees.
        assert not located.tree_scoped

    def test_many_files_in_one_tree_share_it(self, tmp_path):
        for name in ("a.yml", "b.yml", "c.yml"):
            write(tmp_path, f"proj/.github/workflows/{name}")
        located = find((tmp_path,), (".github/workflows/*.yml",))
        assert located.total == 3
        assert located.tree_count == 1

    def test_the_first_pattern_in_declared_order_wins(self, tmp_path):
        # Which decides the tree, so it must not depend on walk order.
        p = write(tmp_path, "proj/.github/workflows/ci.yml")
        deep_first = find((tmp_path,), (".github/workflows/*.yml", "*.yml"))
        shallow_first = find((tmp_path,), ("*.yml", ".github/workflows/*.yml"))
        assert deep_first.tree_of(p) == tmp_path / "proj"
        assert shallow_first.tree_of(p) == p.parent

    def test_tree_of_an_unknown_path_is_its_own_directory(self, tmp_path):
        # A hand-built Located has no trees, and a KeyError out of the middle
        # of a corpus walk is a failure this package has had before.
        located = find((tmp_path,), ("Makefile",))
        stray = Path("/somewhere/else/Makefile")
        assert located.tree_of(stray) == Path("/somewhere/else")


class TestSample:
    def _files(self, n):
        return tuple(Path(f"/corpus/dir{i:03d}/Makefile") for i in range(n))

    def test_no_limit_returns_everything(self):
        files = self._files(10)
        assert sample(files, None) == files

    def test_limit_above_the_count_returns_everything(self):
        files = self._files(3)
        assert sample(files, 10) == files

    def test_limit_is_respected_and_is_a_subset(self):
        files = self._files(100)
        taken = sample(files, 7)
        assert len(taken) == 7
        assert set(taken) <= set(files)

    def test_is_stable_across_calls(self):
        files = self._files(100)
        assert sample(files, 9) == sample(files, 9)

    def test_does_not_just_take_the_first_directory(self):
        # Sorting by path and slicing gives you 40 makefiles from arch/alpha
        # and nothing else. The hash ordering is the whole point.
        files = self._files(200)
        taken = sample(files, 20)
        assert taken != files[:20]

    def test_output_is_sorted_even_though_selection_is_hashed(self):
        taken = sample(self._files(50), 10)
        assert list(taken) == sorted(taken)

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

"""Tests against inputs nobody here wrote.

Everything in the other test files is a corpus of my own construction, which
is the failure mode logbook#48 is about: a model tested only against the
inputs its author imagined. These run the real example config over the real
files on the machine, so the parts that only break on a distribution tree --
unreadable files, deep symlink farms, filenames the glob did not expect --
break here rather than in someone's CI.

Skipped, not failed, where the directory is absent: that keeps the suite
honest on a laptop. The `box` job in CI asserts the skips did not happen.
"""

from pathlib import Path

import pytest

from stockbox.cli import main
from stockbox.config import load
from stockbox.locate import find
from stockbox.report import EXIT_OK, Outcome, as_json
from stockbox.run import run_shape

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "corpus.toml"

pytestmark = pytest.mark.box


def _shape(name):
    corpus = load(EXAMPLE)
    shape = corpus.shape(name)
    present = [r for r in shape.roots if r.is_dir()]
    if not present:
        pytest.skip(f"none of {[str(r) for r in shape.roots]} exist on this machine")
    return shape


SHAPES = ["json", "shell", "json-descriptions", "workflow-trees"]


def test_the_example_config_is_the_one_shipped():
    assert EXAMPLE.is_file()
    assert {s.name for s in load(EXAMPLE).shapes} == set(SHAPES)


@pytest.mark.parametrize("name", SHAPES)
def test_locating_a_real_tree_finds_files_and_they_are_all_readable(name):
    shape = _shape(name)
    located = find(shape.roots, shape.names, shape.skip_names)
    if located.total == 0:
        pytest.skip(f"no {name} inputs on this machine")
    for p in located.files[:200]:
        # locate promises regular, non-symlink files. A distribution tree is
        # where that promise actually gets tested.
        assert p.is_file(), p
        assert not p.is_symlink(), p


def test_the_example_config_passes_on_a_stock_runner(capsys):
    """The claim the README makes, asserted.

    If this fails on a runner image that still has these directories, either
    a floor is now too high for what ships or a checker changed its exit codes
    -- both worth knowing, and neither visible from unit tests.
    """
    for name in SHAPES:
        _shape(name)
    assert main(["run", "-c", str(EXAMPLE)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "every floor met, nothing broken" in out


def test_a_real_corpus_is_mostly_vacuous_and_the_report_says_so():
    """The measurement the exercised floor exists for, on real files.

    `json-descriptions` gets a verdict on every input and holds the construct
    in a handful of them. The numbers move with the image, so what is asserted
    is the gap rather than its size: more verdicts than exercises, and both
    numbers actually measured. A run where the two were equal would mean awk
    stopped printing its count, or the pattern stopped reading it.
    """
    import json as _json

    shape = _shape("json-descriptions")
    located = find(shape.roots, shape.names, shape.skip_names)
    if located.total < shape.min_verdicts:
        pytest.skip(f"only {located.total} json inputs here")

    run = run_shape(shape, located)
    assert run.floor_met, "the verdict floor must pass -- the point is that it can"
    assert not run.measurement_failed, "awk printed no count the pattern could read"
    assert run.exercised >= shape.min_exercised
    assert run.exercised < run.verdicts, (
        f"every one of {run.verdicts} verdicts exercised the subject, which no "
        f"longer demonstrates anything -- pick a sparser construct"
    )
    assert run.subjects_seen > run.exercised, "instances should outnumber the files"

    payload = _json.loads(as_json(Outcome(runs=[run])))["shapes"][0]
    assert payload["exercised"] + payload["vacuous"] + payload["unmeasured"] == (
        payload["verdicts"]
    )


def _files_by_hand(shape) -> list[Path]:
    """Same oracle, counting files rather than trees."""
    return sorted(
        p
        for tree in _trees_by_hand(shape)
        for p in (tree / ".github" / "workflows").glob("*.y*ml")
        if p.is_file() and not p.is_symlink()
    )


def _trees_by_hand(shape) -> list[Path]:
    """The answer, found without using the thing under test.

    The guard on these tests cannot be "skip if stockbox found nothing": that
    is the exact result the bug produced, so the suite would have gone green by
    skipping itself. pathlib does the counting instead, and a machine with no
    vendored workflow trees skips on pathlib's word rather than stockbox's.
    """
    seen = []
    for root in shape.roots:
        if not root.is_dir():
            continue
        for d in root.rglob(".github/workflows"):
            if d.is_dir() and not d.is_symlink() and any(d.glob("*.y*ml")):
                seen.append(d.parent.parent)
    return sorted(set(seen))


class TestWorkflowTrees:
    """The corpus a basename pattern could not express (logbook#56).

    Every number here is read off the machine rather than asserted, because the
    runner image moves: on 2026-10-29 these roots held 81 workflow files in 30
    trees, and the week before the julia stdlib put another 39 under a
    version-numbered path. What is asserted is the shape of the answer.
    """

    def test_the_path_pattern_finds_what_a_basename_pattern_drowns_in(self):
        shape = _shape("workflow-trees")
        by_hand = _trees_by_hand(shape)
        if not by_hand:
            pytest.skip("no vendored workflow trees on this machine")
        located = find(shape.roots, shape.names, shape.skip_names)
        # The assertion the bug failed: `.github/workflows/*.yml` matched no
        # file at all, and `located.total` was 0 with 30 trees sitting there.
        assert located.total >= len(by_hand), (
            f"pathlib found {len(by_hand)} trees holding workflow files and "
            f"stockbox found {located.total} files"
        )
        assert located.tree_count == len(by_hand)
        assert set(located.trees.values()) == set(by_hand)

        # Every hit is where the pattern said it would be.
        for p in located.files:
            assert p.parent.name == "workflows", p
            assert p.parent.parent.name == ".github", p

        # And the basename spelling, which is the only thing that worked
        # before, finds an order of magnitude more of the wrong thing.
        loose = find(shape.roots, ("*.yml", "*.yaml"))
        assert loose.total > located.total * 5, (
            f"{loose.total} loose vs {located.total} located -- if these are "
            f"close, this corpus no longer demonstrates why the path matters"
        )

    def test_the_tree_is_the_project_root_and_not_the_workflow_directory(self):
        shape = _shape("workflow-trees")
        if not _trees_by_hand(shape):
            pytest.skip("no vendored workflow trees on this machine")
        located = find(shape.roots, shape.names, shape.skip_names)
        assert located.tree_scoped
        for path in located.files:
            tree = located.tree_of(path)
            assert tree != path.parent
            # The claim `{tree}` makes, checked against the filesystem rather
            # than against the arithmetic that produced it.
            assert (tree / ".github" / "workflows").is_dir(), tree
            assert path.is_relative_to(tree), (path, tree)

    def test_many_files_in_one_tree_are_one_input(self):
        shape = _shape("workflow-trees")
        by_hand = _trees_by_hand(shape)
        # Both guards on pathlib's answer, not stockbox's. With the bug back,
        # `located.total` is 0 and a guard reading it would skip -- which is
        # how this test would have been a check that cannot fail.
        if len(_files_by_hand(shape)) <= len(by_hand):
            pytest.skip("no tree here holds more than one workflow file")
        located = find(shape.roots, shape.names, shape.skip_names)
        from stockbox.run import collapse

        distinct, dropped = collapse(shape, located)
        # The shape's command names only `{tree}`, so the inputs are trees.
        assert len(distinct) == located.tree_count
        assert dropped == located.total - located.tree_count
        assert len({located.tree_of(p) for p in distinct}) == len(distinct)

    def test_it_passes_its_own_floors_on_a_stock_runner(self):
        shape = _shape("workflow-trees")
        by_hand = _trees_by_hand(shape)
        if len(by_hand) < shape.min_verdicts:
            pytest.skip(f"only {len(by_hand)} workflow trees here")
        located = find(shape.roots, shape.names, shape.skip_names)
        run = run_shape(shape, located)
        assert run.floor_met, "the verdict floor must pass -- the point is that it can"
        assert not run.measurement_failed, "awk printed no count the pattern could read"
        assert run.exercise_floor_met
        assert run.collapsed > 0, "nothing was collapsed, so this proves nothing"
        assert len(run.sampled) == len(run.distinct) == located.tree_count


def test_a_real_corpus_is_big_enough_to_need_sampling():
    # The sampling path is only exercised when candidates exceed the sample,
    # which on hand-built fixtures only happens because I made it happen.
    shape = _shape("json")
    located = find(shape.roots, shape.names, shape.skip_names)
    if located.total <= shape.sample:
        pytest.skip(f"only {located.total} json inputs here, no sampling to test")
    from stockbox.locate import sample

    taken = sample(located.files, shape.sample)
    assert len(taken) == shape.sample
    assert set(taken) <= set(located.files)
    # Across a real tree the sample must not all land in one directory, which
    # is what sorting by path and slicing would give.
    assert len({p.parent for p in taken}) > 1

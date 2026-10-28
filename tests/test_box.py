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


def test_the_example_config_is_the_one_shipped():
    assert EXAMPLE.is_file()
    assert {s.name for s in load(EXAMPLE).shapes} == {
        "json",
        "shell",
        "json-descriptions",
    }


@pytest.mark.parametrize("name", ["json", "shell", "json-descriptions"])
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
    for name in ("json", "shell", "json-descriptions"):
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

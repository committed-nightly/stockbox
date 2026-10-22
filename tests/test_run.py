"""The floor is the only assertion stockbox makes, so this is the file that matters.

Each test builds a corpus of real files on disk whose *contents* are the exit
status a stub checker should return, so a shape's buckets can be driven exactly.
"""

import sys
import textwrap
from pathlib import Path

import pytest

from stockbox.config import parse
from stockbox.locate import find
from stockbox.report import EXIT_BROKEN, EXIT_FLOOR, EXIT_OK, Outcome, as_json, text
from stockbox.run import NOT_RUN, TIMED_OUT, build_command, run_shape

# Reads the file it is given and exits with the number in it. "hang" sleeps.
CHECKER = """\
import sys, time
body = open(sys.argv[1]).read().strip()
if body == "hang":
    time.sleep(30)
print(f"checked {sys.argv[1]}")
sys.exit(int(body))
"""


@pytest.fixture
def checker(tmp_path):
    # Kept outside the corpus directory on purpose: a checker living among its
    # own inputs is how you accidentally check the checker.
    path = tmp_path / "bin" / "checker.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CHECKER)
    return path


def corpus_of(root: Path, codes: list[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for i, code in enumerate(codes):
        (root / f"in{i:03d}.case").write_text(code)
    return root


def build(tmp_path, checker, codes, *, extra="", min_verdicts=1):
    root = corpus_of(tmp_path / "corpus", codes)
    conf = textwrap.dedent(f"""
        [[shape]]
        name = "cases"
        roots = ["{root}"]
        names = ["*.case"]
        command = ["{sys.executable}", "{checker}", "{{path}}"]
        min_verdicts = {min_verdicts}
        verdict = [0, 1]
        declined = [2]
        soundness = [3]
        timeout = 2
        {extra}
    """)
    shape = parse(conf).shape("cases")
    located = find(shape.roots, shape.names, shape.skip_names)
    return shape, located


def outcome_for(tmp_path, checker, codes, **kw):
    shape, located = build(tmp_path, checker, codes, **kw)
    return Outcome(runs=[run_shape(shape, located)])


def test_verdicts_are_counted_and_the_floor_passes(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["0", "1", "0"], min_verdicts=3)
    run = out.runs[0]
    assert run.verdicts == 3
    assert run.floor_met
    assert out.exit_code == EXIT_OK


def test_a_corpus_the_checker_declines_entirely_fails_the_floor(tmp_path, checker):
    # The measured case this tool exists for: 3,140 real kernel makefiles, every
    # one of them a fragment, every run exit 2, nothing tested.
    out = outcome_for(tmp_path, checker, ["2"] * 20, min_verdicts=5)
    run = out.runs[0]
    assert run.located.total == 20
    assert run.count("declined") == 20
    assert run.verdicts == 0
    assert not run.floor_met
    assert out.exit_code == EXIT_FLOOR
    assert "Every input was declined" in text(out)


def test_an_empty_corpus_fails_and_says_it_is_empty(tmp_path, checker):
    out = outcome_for(tmp_path, checker, [], min_verdicts=1)
    assert out.runs[0].located.total == 0
    assert out.exit_code == EXIT_FLOOR
    assert "The corpus is empty" in text(out)


def test_declined_inputs_do_not_count_towards_the_floor(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["0", "2", "2", "2"], min_verdicts=2)
    assert out.runs[0].verdicts == 1
    assert out.exit_code == EXIT_FLOOR


def test_an_unlisted_exit_code_is_broken(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["0", "0", "7"], min_verdicts=1)
    run = out.runs[0]
    assert run.count("broken") == 1
    assert out.exit_code == EXIT_BROKEN
    assert [r.returncode for r in run.broken] == [7]


def test_a_soundness_exit_is_reported_separately_from_a_crash(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["0", "3"], min_verdicts=1)
    run = out.runs[0]
    assert run.count("soundness") == 1
    assert run.count("broken") == 0
    assert out.exit_code == EXIT_BROKEN
    assert "soundness check" in text(out)


def test_broken_beats_a_met_floor(tmp_path, checker):
    # A shape can clear its floor and still have broken the checker. The
    # failure that matters is the broken one.
    out = outcome_for(tmp_path, checker, ["0", "0", "0", "9"], min_verdicts=2)
    assert out.runs[0].floor_met
    assert out.exit_code == EXIT_BROKEN


def test_a_timeout_is_broken_not_declined(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["0", "hang"], min_verdicts=1)
    run = out.runs[0]
    assert run.count("broken") == 1
    broken = run.broken[0]
    assert broken.returncode is None
    assert broken.kind == TIMED_OUT
    assert broken.status == "timeout"
    assert "no result after" in broken.stderr
    assert out.exit_code == EXIT_BROKEN
    assert "broken  timeout" in text(out)


def missing_checker_outcome(tmp_path, *, count=1, min_verdicts=1):
    root = corpus_of(tmp_path / "corpus", ["0"] * count)
    shape = parse(f"""
        [[shape]]
        name = "cases"
        roots = ["{root}"]
        names = ["*.case"]
        command = ["stockbox-no-such-checker", "{{path}}"]
        min_verdicts = {min_verdicts}
    """).shape("cases")
    located = find(shape.roots, shape.names, shape.skip_names)
    return Outcome(runs=[run_shape(shape, located)])


def test_a_checker_that_is_not_installed_is_broken_not_a_crash(tmp_path):
    out = missing_checker_outcome(tmp_path)
    assert out.exit_code == EXIT_BROKEN
    assert "could not run" in out.runs[0].broken[0].stderr


def test_a_checker_that_is_not_installed_is_not_reported_as_a_timeout(tmp_path):
    # The whole report block used to say "exit timeout" directly above a stderr
    # line saying "No such file or directory", because both failures were
    # inferred from `returncode is None`.
    out = missing_checker_outcome(tmp_path)
    result = out.runs[0].broken[0]
    assert result.returncode is None  # still true, and still not enough to tell
    assert result.kind == NOT_RUN
    assert result.status == "not run"
    assert "timeout" not in text(out)


def test_a_missing_checker_says_nothing_about_the_corpus(tmp_path):
    # Three lines of the report used to blame the corpus for an absent command:
    # the status, the floor advice, and the summary.
    rendered = text(missing_checker_outcome(tmp_path, count=4, min_verdicts=2))
    assert "The checker never ran" in rendered
    assert "says nothing about the corpus" in rendered
    assert "could not be run on any of 4 inputs" in rendered
    assert "widen the roots" not in rendered
    assert "broke the checker" not in rendered


def test_a_real_crash_and_a_missing_checker_are_counted_apart(tmp_path, checker):
    # A shape whose checker exists: "broke the checker" is the true story here,
    # and the not-run wording must not swallow it.
    rendered = text(outcome_for(tmp_path, checker, ["0", "9"], min_verdicts=1))
    assert "1 input broke the checker" in rendered
    assert "could not be run" not in rendered
    assert "broken  exit 9" in rendered


def test_json_distinguishes_a_timeout_from_a_checker_that_never_ran(tmp_path, checker):
    import json

    # `exit` is null for both, so the JSON needed the discriminant too.
    timed_out = json.loads(as_json(outcome_for(tmp_path, checker, ["hang"], min_verdicts=1)))
    absent = json.loads(as_json(missing_checker_outcome(tmp_path / "b")))
    for payload in (timed_out, absent):
        assert payload["shapes"][0]["broken_inputs"][0]["exit"] is None
    assert timed_out["shapes"][0]["broken_inputs"][0]["kind"] == TIMED_OUT
    assert absent["shapes"][0]["broken_inputs"][0]["kind"] == NOT_RUN


def test_sample_limits_what_runs_but_not_what_is_counted_as_found(tmp_path, checker):
    shape, located = build(tmp_path, checker, ["0"] * 50, extra="sample = 10", min_verdicts=5)
    run = run_shape(shape, located)
    assert run.located.total == 50
    assert len(run.sampled) == 10
    assert len(run.results) == 10
    assert "sampled 10" in text(Outcome(runs=[run]))


def test_dry_run_runs_nothing(tmp_path, checker):
    shape, located = build(tmp_path, checker, ["9"] * 5, min_verdicts=1)
    run = run_shape(shape, located, dry_run=True)
    assert run.results == []
    rendered = text(Outcome(runs=[run]), dry_run=True)
    assert "Nothing was run" in rendered
    assert "would run 5" in rendered


def test_the_reproduce_command_is_the_command_that_ran(tmp_path, checker):
    out = outcome_for(tmp_path, checker, ["9"], min_verdicts=1)
    broken = out.runs[0].broken[0]
    assert broken.command == build_command(out.runs[0].shape, broken.path)
    assert str(broken.path) in text(out)


def test_json_carries_the_counts_and_the_exit(tmp_path, checker):
    import json

    out = outcome_for(tmp_path, checker, ["0", "2", "3"], min_verdicts=1)
    payload = json.loads(as_json(out))
    shape = payload["shapes"][0]
    assert shape["verdicts"] == 1
    assert shape["declined"] == 1
    assert shape["soundness"] == 1
    assert shape["floor_met"] is True
    assert payload["exit"] == EXIT_BROKEN
    assert shape["broken_inputs"][0]["exit"] == 3


def test_the_checker_runs_in_the_inputs_directory(tmp_path):
    # Checkers that read neighbouring files -- an include, a .dockerignore --
    # only work if cwd is the input's directory.
    root = corpus_of(tmp_path / "corpus", ["0"])
    probe = tmp_path / "bin" / "probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("import os,sys; print(os.getcwd()); sys.exit(0)")
    shape = parse(f"""
        [[shape]]
        name = "cases"
        roots = ["{root}"]
        names = ["*.case"]
        command = ["{sys.executable}", "{probe}", "{{path}}"]
        min_verdicts = 1
    """).shape("cases")
    located = find(shape.roots, shape.names, shape.skip_names)
    run = run_shape(shape, located)
    assert run.results[0].stdout.strip() == str(root)


def test_summary_wording_distinguishes_thin_from_broken(tmp_path, checker):
    thin = outcome_for(tmp_path / "a", checker, ["2", "2"], min_verdicts=1)
    assert "Corpus too thin" in text(thin)
    broken = outcome_for(tmp_path / "b", checker, ["0", "9"], min_verdicts=1)
    assert "broke the checker" in text(broken)
    clean = outcome_for(tmp_path / "c", checker, ["0", "1"], min_verdicts=2)
    assert "every floor met, nothing broken" in text(clean)

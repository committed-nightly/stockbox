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


def test_a_dry_run_rendered_without_the_flag_does_not_crash(tmp_path, checker):
    # Sampled paths and no results at all: every result is a not-run, vacuously.
    # Nothing in the API stops this being rendered as an ordinary report.
    shape, located = build(tmp_path, checker, ["0"], min_verdicts=5)
    run = run_shape(shape, located, dry_run=True)
    assert run.sampled and not run.results
    assert "NOT MET" in text(Outcome(runs=[run]))


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


# --- exercised verdicts ---------------------------------------------------
#
# A verdict says the checker read the file. These say whether the file held
# anything the checker is about, which is a different question and the one
# a corpus is usually wrong about. Every input below exits 0 -- the exit
# codes are identical across the whole block, so nothing here could be
# answered by the verdict count.

# Prints its first line, exits with its second. A checker that reports its
# own denominator, which is the only thing that can answer the question.
COUNTER = """\
import sys
head, _, tail = open(sys.argv[1]).read().partition("\\n")
if head:
    print(head)
sys.exit(int(tail or 0))
"""

SUBJECT = "subject_pattern = '^([0-9]+) widgets$'"


@pytest.fixture
def counter(tmp_path):
    path = tmp_path / "bin" / "counter.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(COUNTER)
    return path


def test_a_full_verdict_count_can_still_be_a_vacuous_corpus(tmp_path, counter):
    # The measurement this floor exists for, in miniature: every input gets a
    # verdict, the verdict floor is met, and two of twelve inputs contained
    # the construct. On the real corpus that was 5 of 119 workflow files.
    bodies = ["3 widgets\n0", "1 widgets\n0"] + ["0 widgets\n0"] * 10
    out = outcome_for(tmp_path, counter, bodies, extra=SUBJECT, min_verdicts=10)
    run = out.runs[0]
    assert run.verdicts == 12 and run.floor_met
    assert run.exercised == 2
    assert run.vacuous == 10
    assert run.subjects_seen == 4


def test_the_exercised_floor_fails_while_the_verdict_floor_passes(tmp_path, counter):
    bodies = ["1 widgets\n0"] + ["0 widgets\n0"] * 9
    out = outcome_for(
        tmp_path, counter, bodies, extra=f"{SUBJECT}\nmin_exercised = 4", min_verdicts=5
    )
    run = out.runs[0]
    assert run.floor_met, "the verdict floor is met -- that is the point"
    assert not run.exercise_floor_met
    assert out.exit_code == EXIT_FLOOR
    report = text(out)
    assert "floor of 5 verdicts: met" in report
    assert "floor of 4 exercised: NOT MET -- 9 of 10 verdicts were vacuous" in report
    assert "held none of what it is about" in report


def test_a_met_exercised_floor_passes(tmp_path, counter):
    bodies = ["2 widgets\n0"] * 4 + ["0 widgets\n0"] * 4
    out = outcome_for(
        tmp_path, counter, bodies, extra=f"{SUBJECT}\nmin_exercised = 3", min_verdicts=5
    )
    assert out.exit_code == EXIT_OK
    assert "floor of 3 exercised: met" in text(out)
    assert "4 of those 8 exercised the subject" in text(out)


def test_no_count_printed_is_not_the_same_as_a_count_of_zero(tmp_path, counter):
    # The distinction the whole feature turns on. A checker saying "0 widgets"
    # is evidence about the corpus; a checker saying nothing is evidence about
    # the pattern. Conflating them blames the corpus for a typo.
    bodies = ["2 widgets\n0", "0 widgets\n0", "\n0"]
    out = outcome_for(tmp_path, counter, bodies, extra=SUBJECT, min_verdicts=1)
    run = out.runs[0]
    assert (run.exercised, run.vacuous, run.unmeasured) == (1, 1, 1)
    assert "1 of those verdicts printed no count" in text(out)


def test_a_pattern_that_matches_nothing_is_broken_not_thin(tmp_path, counter):
    # Exit 3, not 1. Exit 1 says "widen your roots", and the roots are fine.
    bodies = ["2 widgets\n0"] * 5
    out = outcome_for(
        tmp_path,
        counter,
        bodies,
        extra="subject_pattern = '^([0-9]+) sprockets$'\nmin_exercised = 1",
        min_verdicts=1,
    )
    run = out.runs[0]
    assert run.verdicts == 5
    assert run.measurement_failed
    assert out.exit_code == EXIT_BROKEN
    report = text(out)
    assert "exercised: not measured" in report
    assert "This says nothing about the corpus" in report
    assert "Not measured: the subject_pattern for cases" in report
    # The misleading advice must not appear: nothing here is about the corpus.
    assert "held none of what it is about" not in report


def test_declined_and_broken_inputs_are_neither_exercised_nor_vacuous(tmp_path, counter):
    # They produced no verdict, so they have no count to report. Calling them
    # vacuous would charge the corpus twice for one miss.
    bodies = ["2 widgets\n0", "x\n2", "y\n9"]
    out = outcome_for(tmp_path, counter, bodies, extra=SUBJECT, min_verdicts=1)
    run = out.runs[0]
    assert (run.verdicts, run.count("declined"), run.count("broken")) == (1, 1, 1)
    assert len(run.subject_counts) == 1
    assert (run.exercised, run.vacuous, run.unmeasured) == (1, 0, 0)


def test_every_match_in_the_output_is_summed(tmp_path, counter):
    # A checker that prints a count per file rather than one total. The sum is
    # reported and never asserted on, so double-counting cannot change a floor.
    out = outcome_for(tmp_path, counter, ["4 widgets\n0"], extra=SUBJECT, min_verdicts=1)
    assert out.runs[0].subjects_seen == 4
    multi = outcome_for(
        tmp_path / "b",
        counter,
        ["4 widgets\n0"],
        extra="subject_pattern = '([0-9]) widgets'",
        min_verdicts=1,
    )
    assert multi.runs[0].subjects_seen == 4


def test_a_group_that_captures_something_unnumeric_is_no_measurement(tmp_path, counter):
    # Right shape of line, wrong group. Reported as "not measured" rather than
    # silently summing to zero and reading as a vacuous corpus.
    out = outcome_for(
        tmp_path,
        counter,
        ["7 widgets\n0"] * 3,
        extra="subject_pattern = '[0-9]+ (widgets)'\nmin_exercised = 1",
        min_verdicts=1,
    )
    assert out.runs[0].measurement_failed
    assert out.exit_code == EXIT_BROKEN


def test_a_shape_with_no_pattern_reports_no_exercised_line(tmp_path, checker):
    # Not measuring is not the same as measuring zero, and a line saying
    # `exercised 0` on a shape that never asked would be a lie about the corpus.
    out = outcome_for(tmp_path, checker, ["0", "0"], min_verdicts=1)
    report = text(out)
    assert "exercised" not in report
    assert out.runs[0].exercise_floor_met
    assert not out.runs[0].measurement_failed


def test_the_json_payload_carries_both_floors(tmp_path, counter):
    import json as _json

    bodies = ["3 widgets\n0", "0 widgets\n0", "\n0"]
    out = outcome_for(
        tmp_path, counter, bodies, extra=f"{SUBJECT}\nmin_exercised = 1", min_verdicts=1
    )
    shape = _json.loads(as_json(out))["shapes"][0]
    assert shape["verdicts"] == 3
    assert shape["exercised"] == 1
    assert shape["vacuous"] == 1
    assert shape["unmeasured"] == 1
    assert shape["subjects_seen"] == 3
    assert shape["min_exercised"] == 1
    assert shape["exercise_floor_met"] is True
    assert shape["measurement_failed"] is False
    assert shape["subject_pattern"] == "^([0-9]+) widgets$"
    assert _json.loads(as_json(out))["exercised"] == 1


def test_an_awk_program_in_a_command_reaches_the_checker_intact(tmp_path):
    # `str.format` raised KeyError(' n++ ') from inside the corpus walk, so
    # every awk program and every jq filter was an unhandled traceback.
    root = corpus_of(tmp_path / "corpus", ['{"description": "x"}'])
    shape = parse(f"""
        [[shape]]
        name = "cases"
        roots = ["{root}"]
        names = ["*.case"]
        command = ["awk", '/description/ {{ n++ }} END {{ print n+0 " hits" }}', "{{path}}"]
        min_verdicts = 1
        subject_pattern = '^([0-9]+) hits$'
        min_exercised = 1
    """).shape("cases")
    assert shape.command[1] == '/description/ { n++ } END { print n+0 " hits" }'
    located = find(shape.roots, shape.names, shape.skip_names)
    run = run_shape(shape, located)
    assert run.results[0].stdout.strip() == "1 hits"
    assert run.exercised == 1
    assert Outcome(runs=[run]).exit_code == EXIT_OK


# --- path patterns, {tree}, and the collapse -------------------------------


def tree_corpus(root: Path, layout: dict[str, list[str]]) -> Path:
    """`{"proj": ["ci.yml", "release.yml"]}` -> proj/.github/workflows/*.yml."""
    for project, files in layout.items():
        d = root / project / ".github" / "workflows"
        d.mkdir(parents=True, exist_ok=True)
        for name in files:
            (d / name).write_text("on: push\n")
    return root


def tree_shape(root: Path, command: str, *, extra="", min_verdicts=1):
    conf = textwrap.dedent(f"""
        [[shape]]
        name = "trees"
        roots = ["{root}"]
        names = [".github/workflows/*.yml"]
        command = {command}
        min_verdicts = {min_verdicts}
        verdict = [0]
        timeout = 5
        {extra}
    """)
    shape = parse(conf).shape("trees")
    return shape, find(shape.roots, shape.names, shape.skip_names)


def test_tree_substitutes_the_project_root(tmp_path):
    root = tree_corpus(tmp_path, {"proj": ["ci.yml"]})
    shape, located = tree_shape(root, '["echo", "{tree}"]')
    path = located.files[0]
    assert build_command(shape, path, located.tree_of(path)) == (
        "echo",
        str(root / "proj"),
    )


def test_tree_defaults_to_the_files_directory_when_nobody_passes_one(tmp_path):
    # Callers that only have a path -- and every corpus written before `{tree}`
    # existed, where the two are the same thing anyway.
    root = tree_corpus(tmp_path, {"proj": ["ci.yml"]})
    shape, located = tree_shape(root, '["echo", "{tree}"]')
    path = located.files[0]
    assert build_command(shape, path) == ("echo", str(path.parent))


def test_two_files_in_one_tree_are_one_input(tmp_path):
    root = tree_corpus(tmp_path, {"proj": ["a.yml", "b.yml", "c.yml"]})
    shape, located = tree_shape(root, '["true", "{tree}"]')
    run = run_shape(shape, located)
    assert located.total == 3
    assert len(run.distinct) == 1
    assert run.collapsed == 2
    assert run.verdicts == 1, "three runs of the same command line is one input"
    assert "2 of them build a command line another input already builds" in text(
        Outcome(runs=[run])
    )


def test_a_path_scoped_command_collapses_nothing(tmp_path):
    root = tree_corpus(tmp_path, {"proj": ["a.yml", "b.yml"]})
    shape, located = tree_shape(root, '["true", "{path}"]', min_verdicts=2)
    run = run_shape(shape, located)
    assert run.collapsed == 0
    assert run.verdicts == 2
    assert "build a command line" not in text(Outcome(runs=[run]))


def test_the_collapse_happens_before_the_sample(tmp_path):
    # The other order takes the sample from the files and then runs one command
    # per tree, so a sample of 4 would have made two runs and the floor would
    # depend on which files the hash picked.
    root = tree_corpus(tmp_path, {f"p{i}": ["a.yml", "b.yml", "c.yml"] for i in range(5)})
    shape, located = tree_shape(root, '["true", "{tree}"]', extra="sample = 4", min_verdicts=4)
    run = run_shape(shape, located)
    assert located.total == 15
    assert len(run.distinct) == 5
    assert len(run.sampled) == 4
    assert run.verdicts == 4
    assert run.floor_met


def test_the_working_directory_is_the_tree(tmp_path):
    # A checker that wants to be run from inside the project gets that, and a
    # basename shape is unaffected because there the tree is the file's own
    # directory.
    root = tree_corpus(tmp_path, {"proj": ["ci.yml"]})
    shape, located = tree_shape(root, '["sh", "-c", "pwd", "--", "{tree}"]')
    run = run_shape(shape, located)
    assert run.results[0].stdout.strip() == str((root / "proj").resolve())


def test_the_report_counts_trees_only_where_they_are_not_the_directories(tmp_path):
    root = tree_corpus(tmp_path, {"a": ["x.yml"], "b": ["y.yml", "z.yml"]})
    shape, located = tree_shape(root, '["true", "{path}"]', min_verdicts=3)
    rendered = text(Outcome(runs=[run_shape(shape, located)]))
    assert "3 candidates in 2 trees" in rendered

    # And the same corpus matched by basename says nothing about trees, where
    # the count would be its directories and would read as a claim about
    # projects.
    shape2 = parse(
        textwrap.dedent(f"""
            [[shape]]
            name = "flat"
            roots = ["{root}"]
            names = ["*.yml"]
            command = ["true", "{{path}}"]
            min_verdicts = 3
        """)
    ).shape("flat")
    located2 = find(shape2.roots, shape2.names, shape2.skip_names)
    assert located2.total == 3
    # Its tree count is 2, and that is the number not to print. Asserted
    # against the candidates line only: tmp_path carries the test's own name,
    # so a bare `"trees" not in rendered` could never fail.
    assert located2.tree_count == 2
    headline = text(Outcome(runs=[run_shape(shape2, located2)])).splitlines()[0]
    assert headline.endswith("candidates (" + str(root) + "=3)"), headline


def test_json_carries_the_tree_and_collapse_counts(tmp_path):
    import json as _json

    root = tree_corpus(tmp_path, {"a": ["x.yml", "y.yml"], "b": ["z.yml"]})
    shape, located = tree_shape(root, '["true", "{tree}"]', min_verdicts=2)
    payload = _json.loads(as_json(Outcome(runs=[run_shape(shape, located)])))["shapes"][0]
    assert payload["candidates"] == 3
    assert payload["trees"] == 2
    assert payload["distinct"] == 2
    assert payload["collapsed"] == 1
    assert payload["sampled"] == 2
    assert payload["candidates"] - payload["collapsed"] == payload["distinct"]


def test_a_dry_run_prints_the_tree_it_would_pass(tmp_path):
    root = tree_corpus(tmp_path, {"proj": ["a.yml", "b.yml"]})
    shape, located = tree_shape(root, '["coldcache", "{tree}"]')
    rendered = text(Outcome(runs=[run_shape(shape, located, dry_run=True)]), dry_run=True)
    assert f"coldcache {root / 'proj'}" in rendered
    assert "Nothing was run" in rendered

import json
import sys
import textwrap

import pytest

from stockbox.cli import main
from stockbox.report import EXIT_BROKEN, EXIT_FLOOR, EXIT_OK, EXIT_USAGE

CHECKER = """\
import sys
sys.exit(int(open(sys.argv[1]).read().strip()))
"""


@pytest.fixture
def box(tmp_path):
    """A corpus directory, a stub checker, and a corpus.toml pointing at both."""
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    checker = tmp_path / "checker.py"
    checker.write_text(CHECKER)

    def make(codes, *, min_verdicts=1, extra=""):
        for i, code in enumerate(codes):
            (corpus / f"in{i:03d}.case").write_text(code)
        conf = tmp_path / "corpus.toml"
        conf.write_text(
            textwrap.dedent(f"""
            [[shape]]
            name = "cases"
            roots = ["{corpus}"]
            names = ["*.case"]
            command = ["{sys.executable}", "{checker}", "{{path}}"]
            min_verdicts = {min_verdicts}
            verdict = [0, 1]
            declined = [2]
            soundness = [3]
            timeout = 5
            {extra}
            """)
        )
        return conf

    return make


def test_no_subcommand_prints_help_and_exits_usage(capsys):
    assert main([]) == EXIT_USAGE
    assert "stockbox" in capsys.readouterr().out


def test_run_clean_exits_zero(box, capsys):
    conf = box(["0", "1", "0"], min_verdicts=3)
    assert main(["run", "-c", str(conf)]) == EXIT_OK
    assert "every floor met, nothing broken" in capsys.readouterr().out


def test_run_thin_corpus_exits_one(box, capsys):
    conf = box(["2", "2", "2"], min_verdicts=1)
    assert main(["run", "-c", str(conf)]) == EXIT_FLOOR
    out = capsys.readouterr().out
    assert "NOT MET" in out
    assert "Corpus too thin" in out


def test_run_broken_checker_exits_three(box, capsys):
    conf = box(["0", "42"], min_verdicts=1)
    assert main(["run", "-c", str(conf)]) == EXIT_BROKEN
    out = capsys.readouterr().out
    assert "exit 42" in out
    assert "reproduce:" in out


def test_missing_config_is_a_usage_error(tmp_path, capsys):
    assert main(["run", "-c", str(tmp_path / "nope.toml")]) == EXIT_USAGE
    assert "cannot read" in capsys.readouterr().err


def test_bad_config_is_a_usage_error_not_a_failure(tmp_path, capsys):
    conf = tmp_path / "corpus.toml"
    conf.write_text("[[shape]]\nname = 'x'\n")
    assert main(["run", "-c", str(conf)]) == EXIT_USAGE
    assert "stockbox:" in capsys.readouterr().err


def test_unknown_shape_is_a_usage_error(box, capsys):
    conf = box(["0"])
    assert main(["run", "-c", str(conf), "-s", "ghost"]) == EXIT_USAGE
    assert "no shape named 'ghost'" in capsys.readouterr().err


def test_dry_run_exits_zero_even_when_the_checker_would_break(box, capsys):
    conf = box(["42"], min_verdicts=1)
    assert main(["run", "-c", str(conf), "--dry-run"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Nothing was run" in out
    assert "checker.py" in out


def test_sample_override_zero_means_everything(box, capsys):
    conf = box(["0"] * 12, min_verdicts=2, extra="sample = 3")
    assert main(["run", "-c", str(conf), "--sample", "0"]) == EXIT_OK
    assert "ran all 12" in capsys.readouterr().out


def test_sample_override_does_not_rescue_an_unsatisfiable_config(box, capsys):
    # `sample = 3` with `min_verdicts = 12` can never pass, and that is a bug in
    # the file rather than in today's invocation. --sample 0 would make this run
    # work, which is exactly why it must not suppress the error: the file would
    # stay broken for everyone who runs it without the flag.
    conf = box(["0"] * 12, min_verdicts=12, extra="sample = 3")
    assert main(["run", "-c", str(conf), "--sample", "0"]) == EXIT_USAGE
    assert "can never be met" in capsys.readouterr().err


def test_negative_sample_is_a_usage_error(box, capsys):
    conf = box(["0"])
    assert main(["run", "-c", str(conf), "--sample", "-1"]) == EXIT_USAGE
    assert "cannot be negative" in capsys.readouterr().err


def test_roots_counts_without_running(box, capsys):
    conf = box(["42"] * 4, min_verdicts=1)
    assert main(["roots", "-c", str(conf)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "4 candidates" in out
    assert "cases:" in out


def test_roots_flags_a_root_that_contributes_nothing(tmp_path, capsys):
    conf = tmp_path / "corpus.toml"
    conf.write_text(
        textwrap.dedent(f"""
        [[shape]]
        name = "cases"
        roots = ["{tmp_path}", "/nonexistent-stockbox-root"]
        names = ["*.nothing-matches-this"]
        command = ["true", "{{path}}"]
        min_verdicts = 1
        """)
    )
    assert main(["roots", "-c", str(conf)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "contributes nothing" in out
    assert "no such directory" in out


def test_run_json_is_valid_and_has_the_exit_in_it(box, capsys):
    conf = box(["0", "2", "3"], min_verdicts=1)
    code = main(["run", "-c", str(conf), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_BROKEN
    assert payload["exit"] == EXIT_BROKEN
    assert payload["shapes"][0]["name"] == "cases"


def test_roots_json_is_valid(box, capsys):
    conf = box(["0"], min_verdicts=1)
    assert main(["roots", "-c", str(conf), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["shapes"][0]["candidates"] == 1

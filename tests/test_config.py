import pytest

from stockbox.config import ConfigError, parse

GOOD = """
[[shape]]
name = "makefiles"
roots = ["/usr/share/doc"]
names = ["Makefile", "*.mk"]
command = ["unmade", "{name}"]
min_verdicts = 5
verdict = [0, 1]
declined = [2]
soundness = [3]
sample = 40
timeout = 15
"""


def test_parses_a_whole_shape():
    shape = parse(GOOD).shape("makefiles")
    assert [str(r) for r in shape.roots] == ["/usr/share/doc"]
    assert shape.names == ("Makefile", "*.mk")
    assert shape.verdict == {0, 1}
    assert shape.declined == {2}
    assert shape.min_verdicts == 5
    assert shape.sample == 40
    assert shape.timeout == 15.0


def test_defaults_are_the_conservative_ones():
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["*.conf"]
        command = ["true", "{path}"]
        min_verdicts = 1
        """
    ).shape("s")
    # Only exit 0 counts as an answer unless you say otherwise, and nothing is
    # declined: an unlisted code is broken, so a new exit status shows up as a
    # failure rather than being quietly absorbed.
    assert shape.verdict == {0}
    assert shape.declined == frozenset()
    assert shape.bucket(2) == "broken"


@pytest.mark.parametrize(
    "body, fragment",
    [
        ("", "no [[shape]] tables"),
        ('[[shape]]\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1', "no `name`"),
        ('[[shape]]\nname="s"\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1', "roots must be"),
        ('[[shape]]\nname="s"\nroots=[]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1', "declares no roots"),
        ('[[shape]]\nname="s"\nroots=["rel"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1', "not an absolute path"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=[]\ncommand=["a {path}"]\nmin_verdicts=1', "no `names`"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=[]\nmin_verdicts=1', "no `command`"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a","b"]\nmin_verdicts=1', "names no input"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]', "no `min_verdicts`"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=0', "floor of zero"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=10\nsample=5', "can never be met"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1\ntimeout=0', "positive number"),
        ('[[shape]]\nname="s"\nroots=["/etc"]\nnames=["x"]\ncommand=["a {path}"]\nmin_verdicts=1\nverdict=[0]\ndeclined=[0]', "both a"),
        ("[[shape]]\nname=", "not valid TOML"),
    ],
)
def test_rejects(body, fragment):
    with pytest.raises(ConfigError) as exc:
        parse(body)
    assert fragment in str(exc.value)


def test_duplicate_shape_names_are_an_error():
    with pytest.raises(ConfigError, match="defined twice"):
        parse(GOOD + GOOD)


def test_a_command_naming_only_dir_and_name_is_enough():
    # Checkers that must be run from the input's directory never mention
    # {path}; rejecting those would be wrong.
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["Makefile"]
        command = ["unmade", "{name}", "-C", "{dir}"]
        min_verdicts = 1
        """
    ).shape("s")
    assert "{name}" in shape.command


def test_sample_false_means_no_limit():
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["x"]
        command = ["a {path}"]
        min_verdicts = 1
        sample = false
        """
    ).shape("s")
    assert shape.sample is None


def test_unknown_shape_name_lists_the_known_ones():
    with pytest.raises(ConfigError, match="Shapes in this file: makefiles"):
        parse(GOOD).shape("nope")


def test_soundness_beats_verdict_when_a_code_is_in_both():
    # A checker reporting its own model was wrong has not passed, whatever
    # else the config claims about that exit status.
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["x"]
        command = ["a {path}"]
        min_verdicts = 1
        verdict = [0, 3]
        soundness = [3]
        """
    ).shape("s")
    assert shape.bucket(3) == "soundness"
    assert shape.bucket(0) == "verdict"

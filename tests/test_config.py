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


NAMES_SHAPE = """
[[shape]]
name = "s"
roots = ["/etc"]
names = [%s]
command = ["a", "{path}"]
min_verdicts = 1
"""


@pytest.mark.parametrize(
    "pattern, fragment",
    [
        # All three of these looked like they would work while the workflow
        # corpus in logbook#56 was being written.
        ('".github/**/*.yml"', "`**` is not a wildcard here"),
        ('"/etc/*.conf"', "a leading / reads as anchored"),
        ('".github/workflows/"', "a trailing / reads as a directory"),
        ('".github//workflows/*.yml"', "empty path component"),
        ('""', "is empty"),
    ],
)
def test_a_names_pattern_that_cannot_mean_what_it_looks_like_is_rejected(
    pattern, fragment
):
    with pytest.raises(ConfigError) as exc:
        parse(NAMES_SHAPE % pattern)
    assert fragment in str(exc.value)


def test_the_rejection_suggests_the_spelling_that_works():
    with pytest.raises(ConfigError) as exc:
        parse(NAMES_SHAPE % '"/.github/workflows/*.yml"')
    assert "'.github/workflows/*.yml'" in str(exc.value)


def test_skip_names_is_held_to_the_same_rules():
    body = """
    [[shape]]
    name = "s"
    roots = ["/etc"]
    names = ["*.yml"]
    skip_names = ["vendor/**/*.yml"]
    command = ["a", "{path}"]
    min_verdicts = 1
    """
    with pytest.raises(ConfigError, match=r"skip_names entry"):
        parse(body)


def test_a_path_pattern_is_kept_as_written():
    shape = parse(NAMES_SHAPE % '".github/workflows/*.yml"').shape("s")
    assert shape.names == (".github/workflows/*.yml",)


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


# --- subject_pattern and min_exercised ------------------------------------
#
# The second floor. `min_verdicts` asks whether the checker read the file;
# these ask whether the file held anything the checker is about.


def _with(extra: str):
    return parse(
        f"""
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["x"]
        command = ["a", "{{path}}"]
        min_verdicts = 1
        {extra}
        """
    ).shape("s")


def test_a_subject_pattern_is_compiled_at_load_time():
    shape = _with("subject_pattern = '([0-9]+) cache steps?'\nmin_exercised = 3")
    assert shape.subject is not None
    assert shape.subject.pattern == "([0-9]+) cache steps?"
    assert shape.min_exercised == 3


def test_a_shape_without_one_measures_nothing_and_says_so_with_none():
    shape = _with("")
    assert shape.subject is None
    assert shape.min_exercised is None


def test_a_pattern_that_is_not_a_regex_is_a_config_error_not_a_traceback():
    # The point of compiling at load time: 400 inputs into a corpus walk is
    # the wrong place to find out the pattern does not parse.
    with pytest.raises(ConfigError, match="not a valid regex"):
        _with("subject_pattern = '([0-9]+'")


@pytest.mark.parametrize(
    "pattern, groups",
    [("[0-9]+", 0), ("([0-9]+) of ([0-9]+)", 2)],
)
def test_a_pattern_needs_exactly_one_group(pattern, groups):
    with pytest.raises(ConfigError, match=f"has {groups} capturing groups"):
        _with(f"subject_pattern = '{pattern}'")


def test_min_exercised_without_a_pattern_is_rejected():
    # Nothing would count the subject, so the floor could only ever read zero.
    with pytest.raises(ConfigError, match="needs a subject_pattern"):
        _with("min_exercised = 1")


def test_a_pattern_without_a_floor_is_allowed_and_only_reported():
    # Measuring before choosing a floor is the documented order of work, and
    # the number is worth printing on the way there.
    shape = _with("subject_pattern = '([0-9]+)'")
    assert shape.subject is not None
    assert shape.min_exercised is None


def test_an_exercised_floor_of_zero_is_rejected_like_the_verdict_one():
    with pytest.raises(ConfigError, match="floor of zero asserts nothing"):
        _with("subject_pattern = '([0-9]+)'\nmin_exercised = 0")


def test_an_exercised_floor_above_the_sample_can_never_be_met():
    with pytest.raises(ConfigError, match="can never be met"):
        _with("sample = 5\nsubject_pattern = '([0-9]+)'\nmin_exercised = 6")


# --- placeholders ---------------------------------------------------------


def test_a_command_may_contain_braces_that_are_not_placeholders():
    # An awk program survives parsing. Parsing was never where it broke -- the
    # KeyError came from the substitution, so the test that proves that fix is
    # `test_an_awk_program_in_a_command_reaches_the_checker_intact`. This one is
    # here so that tightening the placeholder check cannot start rejecting a
    # legitimate program at load time instead.
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = ["x"]
        command = ["awk", '/x/ { n++ } END { print n+0 " hits" }', "{path}"]
        min_verdicts = 1
        """
    ).shape("s")
    assert shape.command[1] == '/x/ { n++ } END { print n+0 " hits" }'


def test_tree_is_a_placeholder_and_names_an_input():
    shape = parse(
        """
        [[shape]]
        name = "s"
        roots = ["/etc"]
        names = [".github/workflows/*.yml"]
        command = ["coldcache", "{tree}"]
        min_verdicts = 1
        """
    ).shape("s")
    assert shape.command == ("coldcache", "{tree}")


def test_a_misspelt_placeholder_is_caught_at_load_time():
    # Without this it is not an error at all: `{pathh}` reaches the checker
    # as the literal string, and the checker complains about a missing file.
    with pytest.raises(ConfigError, match=r"\{pathh\}, which is no placeholder"):
        parse(
            """
            [[shape]]
            name = "s"
            roots = ["/etc"]
            names = ["x"]
            command = ["true", "{pathh}"]
            min_verdicts = 1
            """
        )


def test_braces_without_any_placeholder_still_names_no_input():
    # The old check was `"{" in part`, which an awk program satisfies while
    # checking the same thing on every input.
    with pytest.raises(ConfigError, match="names no input"):
        parse(
            """
            [[shape]]
            name = "s"
            roots = ["/etc"]
            names = ["x"]
            command = ["awk", "{ print }"]
            min_verdicts = 1
            """
        )

"""The corpus file: what to look for, where, and what counts as a test.

A shape is one (corpus, checker) pair. The fields that matter are `roots`
(where to look -- never the whole filesystem, see `locate`) and the exit-code
buckets, which are what turns a pile of exit statuses into the only number
worth reporting: how many inputs the checker actually formed an opinion about.

`subject_pattern` and `min_exercised` are the second floor, and they exist
because the first one overstates. An exit status tells you the checker read the
file; it does not tell you the file contained anything the checker is *about*.
Four of this org's workflow checkers get a verdict on all 47 real project trees
on a GitHub runner, and the construct each one looks for is in 3 to 11 of them.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT = 30.0
DEFAULT_SAMPLE = 50


class ConfigError(Exception):
    """The corpus file cannot be used. Always a usage error, never a finding."""


@dataclass(frozen=True)
class Shape:
    name: str
    roots: tuple[Path, ...]
    names: tuple[str, ...]
    command: tuple[str, ...]
    verdict: frozenset[int]
    declined: frozenset[int]
    soundness: frozenset[int]
    min_verdicts: int
    sample: int | None
    timeout: float
    skip_names: tuple[str, ...] = ()
    # Compiled rather than kept as a string so a bad regex is a config error at
    # load time, not a traceback 400 files into a corpus run. `.pattern` gives
    # the original back for the report, which has to quote it.
    subject: re.Pattern[str] | None = None
    min_exercised: int | None = None

    def bucket(self, returncode: int) -> str:
        """Which bucket an exit status falls in.

        `soundness` is checked first and is always broken: a checker that
        reports its own model was wrong has not passed, whatever else the
        config says about that code.
        """
        if returncode in self.soundness:
            return "soundness"
        if returncode in self.verdict:
            return "verdict"
        if returncode in self.declined:
            return "declined"
        return "broken"


@dataclass
class Corpus:
    shapes: list[Shape] = field(default_factory=list)

    def shape(self, name: str) -> Shape:
        for s in self.shapes:
            if s.name == name:
                return s
        known = ", ".join(s.name for s in self.shapes) or "none"
        raise ConfigError(f"no shape named {name!r}. Shapes in this file: {known}")


def _codes(raw: object, where: str, default: frozenset[int]) -> frozenset[int]:
    if raw is None:
        return default
    if not isinstance(raw, list) or not all(isinstance(c, int) for c in raw):
        raise ConfigError(f"{where} must be a list of integers, got {raw!r}")
    return frozenset(raw)


def _strings(raw: object, where: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
        raise ConfigError(f"{where} must be a list of strings, got {raw!r}")
    return tuple(raw)


PLACEHOLDERS = ("path", "dir", "name")

# Something a reader would take for a placeholder: one brace-wrapped word, no
# spaces, no punctuation. Deliberately narrow, because everything it does not
# match is passed through to the checker untouched -- `{ n++ }` from an awk
# program, `{a:1}` from a jq filter, `{1..3}` from a shell.
_LOOKS_LIKE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _check_placeholders(command: tuple[str, ...], where: str) -> None:
    """Reject a command that names no input, or misspells the name of one.

    The misspelling half matters because substitution is a plain replace: an
    unrecognised `{pathh}` is not an error at runtime, it is an argument
    reading `{pathh}` handed to the checker, which will say something unhelpful
    about a file of that name. Caught here, where the fix is in view.
    """
    used = set()
    for part in command:
        for found in _LOOKS_LIKE.findall(part):
            if found in PLACEHOLDERS:
                used.add(found)
            else:
                raise ConfigError(
                    f"{where}: command contains {{{found}}}, which is no placeholder "
                    f"of stockbox's. The three are {{path}}, {{dir}} and {{name}}. "
                    f"Braces that are not one of those are passed to the checker as "
                    f"they stand, so this one would have been too."
                )
    if not used:
        raise ConfigError(
            f"{where}: command names no input. Use {{path}}, or {{name}} with "
            f"{{dir}}, somewhere in it, or every run checks the same thing."
        )


def _subject(raw: object, where: str) -> re.Pattern[str] | None:
    """Compile `subject_pattern`, the regex that reads the checker's own count.

    One capturing group, and it has to be the number. Zero groups leaves nothing
    to read; two leaves stockbox choosing between them, and a floor that depends
    on which group a stranger meant is not a floor.
    """
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"{where}: subject_pattern must be a non-empty string, got {raw!r}")
    try:
        # MULTILINE because the useful patterns are anchored to a line of the
        # checker's output -- `^(\d+) cache steps?` and friends.
        pattern = re.compile(raw, re.MULTILINE)
    except re.error as exc:
        raise ConfigError(f"{where}: subject_pattern is not a valid regex: {exc}") from exc
    if pattern.groups != 1:
        raise ConfigError(
            f"{where}: subject_pattern has {pattern.groups} capturing groups and "
            f"needs exactly one -- the number the checker prints. Wrap the count in "
            f"parentheses and nothing else."
        )
    return pattern


def _min_exercised(
    raw: object, subject: re.Pattern[str] | None, sample: int | None, where: str
) -> int | None:
    if raw is None:
        return None
    if subject is None:
        raise ConfigError(
            f"{where}: min_exercised needs a subject_pattern. Without one nothing "
            f"counts the subject, so the floor would be a check on a number stockbox "
            f"never took."
        )
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ConfigError(f"{where}: min_exercised must be an integer, got {raw!r}")
    if raw < 1:
        raise ConfigError(
            f"{where}: min_exercised is {raw}. A floor of zero asserts nothing -- "
            f"drop the key and keep the pattern if you only want the number reported."
        )
    if sample is not None and raw > sample:
        raise ConfigError(
            f"{where}: sample is {sample} but min_exercised is {raw}, so the floor "
            f"can never be met."
        )
    return raw


def parse(text: str, source: str = "<string>") -> Corpus:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{source}: not valid TOML: {exc}") from exc

    raw_shapes = data.get("shape")
    if raw_shapes is None:
        raise ConfigError(
            f"{source}: no [[shape]] tables. A corpus file with no shapes would "
            f"check nothing and report success, which is the failure this tool exists to catch."
        )
    if not isinstance(raw_shapes, list):
        raise ConfigError(f"{source}: `shape` must be written as [[shape]] tables")

    shapes: list[Shape] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_shapes):
        where = f"{source}: shape #{i + 1}"
        if not isinstance(raw, dict):
            raise ConfigError(f"{where} is not a table")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError(f"{where} has no `name`")
        where = f"{source}: shape {name!r}"
        if name in seen:
            raise ConfigError(f"{where} is defined twice")
        seen.add(name)

        roots = _strings(raw.get("roots"), f"{where}: roots")
        if not roots:
            raise ConfigError(
                f"{where} declares no roots. stockbox will not walk `/` for you: "
                f"a full-filesystem search takes minutes on a CI runner and most "
                f"of what it finds is not the shape you meant."
            )
        for r in roots:
            if not Path(r).is_absolute():
                raise ConfigError(f"{where}: root {r!r} is not an absolute path")

        names = _strings(raw.get("names"), f"{where}: names")
        if not names:
            raise ConfigError(f"{where} declares no `names` to match")

        command = _strings(raw.get("command"), f"{where}: command")
        if not command:
            raise ConfigError(f"{where} declares no `command` to run")
        _check_placeholders(command, where)

        min_verdicts = raw.get("min_verdicts")
        if min_verdicts is None:
            raise ConfigError(
                f"{where} has no `min_verdicts`. It is the only assertion this tool "
                f"makes; without it a run that reached no input at all would pass."
            )
        if not isinstance(min_verdicts, int) or isinstance(min_verdicts, bool):
            raise ConfigError(f"{where}: min_verdicts must be an integer, got {min_verdicts!r}")
        if min_verdicts < 1:
            raise ConfigError(
                f"{where}: min_verdicts is {min_verdicts}. A floor of zero asserts "
                f"nothing. Delete the shape instead."
            )

        # `sample = false` is how you say "no limit". It has to be handled
        # before the integer check, because in TOML as in Python a bool is one.
        sample = raw.get("sample", DEFAULT_SAMPLE)
        if sample is False:
            sample = None
        elif sample is not None:
            if not isinstance(sample, int) or isinstance(sample, bool) or sample < 1:
                raise ConfigError(f"{where}: sample must be a positive integer or false")
            if sample < min_verdicts:
                raise ConfigError(
                    f"{where}: sample is {sample} but min_verdicts is {min_verdicts}, "
                    f"so the floor can never be met."
                )

        timeout = raw.get("timeout", DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ConfigError(f"{where}: timeout must be a positive number")

        subject = _subject(raw.get("subject_pattern"), where)
        min_exercised = _min_exercised(raw.get("min_exercised"), subject, sample, where)

        verdict = _codes(raw.get("verdict"), f"{where}: verdict", frozenset({0}))
        declined = _codes(raw.get("declined"), f"{where}: declined", frozenset())
        soundness = _codes(raw.get("soundness"), f"{where}: soundness", frozenset())
        overlap = verdict & declined
        if overlap:
            raise ConfigError(
                f"{where}: exit code(s) {sorted(overlap)} are listed as both a "
                f"verdict and declined."
            )

        shapes.append(
            Shape(
                name=name,
                roots=tuple(Path(r) for r in roots),
                names=names,
                command=command,
                verdict=verdict,
                declined=declined,
                soundness=soundness,
                min_verdicts=min_verdicts,
                sample=sample,
                timeout=float(timeout),
                skip_names=_strings(raw.get("skip_names", []), f"{where}: skip_names"),
                subject=subject,
                min_exercised=min_exercised,
            )
        )
    return Corpus(shapes=shapes)


def load(path: Path) -> Corpus:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse(text, source=str(path))

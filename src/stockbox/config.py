"""The corpus file: what to look for, where, and what counts as a test.

A shape is one (corpus, checker) pair. The fields that matter are `roots`
(where to look -- never the whole filesystem, see `locate`) and the exit-code
buckets, which are what turns a pile of exit statuses into the only number
worth reporting: how many inputs the checker actually formed an opinion about.
"""

from __future__ import annotations

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
        if not any("{" in part for part in command):
            raise ConfigError(
                f"{where}: command names no input. Use {{path}}, or {{name}} with "
                f"{{dir}}, somewhere in it, or every run checks the same thing."
            )

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
            )
        )
    return Corpus(shapes=shapes)


def load(path: Path) -> Corpus:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    return parse(text, source=str(path))

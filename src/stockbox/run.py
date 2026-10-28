"""Run the checker over each located input and bucket the results.

The thing being counted is deliberately not "how many files did we find". It
is "how many files did the checker form an opinion about", because those are
the only ones that tested anything. See the module docstring in `report`.

A verdict is still not the end of it. The checker read the file and exited 0;
whether the file held any of what the checker is *about* is a different
question, and the only thing that can answer it is the checker's own count of
what it saw. `subject_count` reads that number back out of the output.
"""

from __future__ import annotations

import functools
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Shape
from .locate import Located, sample

# How a run ended. Two of these have no exit status at all, and they are not
# the same failure: one is the checker taking too long, the other is the
# checker never having started. Inferring them from `returncode is None`
# reported a missing command as a timeout, which is a plausible-looking lie
# that sends you looking for a performance problem you do not have.
EXITED = "exited"
TIMED_OUT = "timed-out"
NOT_RUN = "not-run"


@dataclass(frozen=True)
class Result:
    path: Path
    command: tuple[str, ...]
    returncode: int | None  # None for TIMED_OUT and NOT_RUN alike -- use `kind`
    bucket: str
    stdout: str
    stderr: str
    kind: str = EXITED

    @property
    def status(self) -> str:
        """How this ended, as the report's middle column."""
        if self.kind == TIMED_OUT:
            return "timeout"
        if self.kind == NOT_RUN:
            return "not run"
        return f"exit {self.returncode}"


def subject_count(shape: Shape, result: Result) -> int | None:
    """How many instances of its own subject the checker reported for this input.

    `None` is not zero, and the difference is the whole point. Zero is the
    checker saying "no cache steps in here"; `None` is nobody saying anything,
    which means the pattern is wrong or the output format moved. Collapsing the
    two turns a typo'd regex into a report about a thin corpus, and sends you
    looking for files instead of for your own mistake.

    Every match is summed rather than the first one taken, so a checker that
    prints a count per file totals correctly. The sum is a reported number and
    never an asserted one -- the floor counts *inputs*, so a pattern that
    double-counts inflates this and changes no verdict.
    """
    if shape.subject is None:
        return None
    values = []
    for found in shape.subject.findall(result.stdout + "\n" + result.stderr):
        try:
            values.append(int(found))
        except ValueError:
            # The group matched something that is not a number, so it is not the
            # count. Falling through to `None` reports that as "no measurement",
            # which is what it is.
            continue
    if not values:
        return None
    return sum(values)


@dataclass
class ShapeRun:
    shape: Shape
    located: Located
    sampled: tuple[Path, ...]
    results: list[Result]

    def count(self, bucket: str) -> int:
        return sum(1 for r in self.results if r.bucket == bucket)

    @property
    def verdicts(self) -> int:
        return self.count("verdict")

    @functools.cached_property
    def subject_counts(self) -> tuple[int | None, ...]:
        """One entry per verdict, in order. Declined and broken inputs are left
        out: the checker did not form an opinion, so it has no count to report
        and counting it as vacuous would blame the corpus twice for one miss."""
        return tuple(
            subject_count(self.shape, r) for r in self.results if r.bucket == "verdict"
        )

    @property
    def exercised(self) -> int:
        """Verdicts where the checker saw at least one of its own subject."""
        return sum(1 for c in self.subject_counts if c)

    @property
    def vacuous(self) -> int:
        """Verdicts where it said, in as many words, that there was none."""
        return sum(1 for c in self.subject_counts if c == 0)

    @property
    def unmeasured(self) -> int:
        """Verdicts whose output the pattern did not match."""
        return sum(1 for c in self.subject_counts if c is None)

    @property
    def subjects_seen(self) -> int:
        return sum(c for c in self.subject_counts if c)

    @property
    def measurement_failed(self) -> bool:
        """The pattern matched nothing anywhere, so there is no measurement.

        Reported and failed separately from a thin corpus, because the numbers
        in that case are not small -- they do not exist.
        """
        if self.shape.subject is None:
            return False
        counts = self.subject_counts
        return bool(counts) and all(c is None for c in counts)

    @property
    def exercise_floor_met(self) -> bool:
        if self.shape.min_exercised is None:
            return True
        return self.exercised >= self.shape.min_exercised

    @property
    def broken(self) -> list[Result]:
        return [r for r in self.results if r.bucket in ("broken", "soundness")]

    @property
    def not_run(self) -> list[Result]:
        """Inputs the checker never started on. A reason, not a fourth bucket --
        these are still broken, and still have to fail the build."""
        return [r for r in self.results if r.kind == NOT_RUN]

    @property
    def floor_met(self) -> bool:
        return self.verdicts >= self.shape.min_verdicts


def build_command(shape: Shape, path: Path) -> tuple[str, ...]:
    """Substitute the input into the command.

    `{path}` is the whole thing, `{dir}` and `{name}` are the halves, for
    checkers that insist on being run from the input's own directory.
    """
    fields = {"path": str(path), "dir": str(path.parent), "name": path.name}
    return tuple(part.format(**fields) for part in shape.command)


def run_one(shape: Shape, path: Path) -> Result:
    command = build_command(shape, path)
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=shape.timeout,
            cwd=str(path.parent),
        )
    except subprocess.TimeoutExpired:
        return Result(
            path=path,
            command=command,
            returncode=None,
            bucket="broken",
            stdout="",
            stderr=f"stockbox: no result after {shape.timeout:g}s",
            kind=TIMED_OUT,
        )
    except OSError as exc:
        return Result(
            path=path,
            command=command,
            returncode=None,
            bucket="broken",
            stdout="",
            stderr=f"stockbox: could not run {command[0]!r}: {exc}",
            kind=NOT_RUN,
        )
    return Result(
        path=path,
        command=command,
        returncode=proc.returncode,
        bucket=shape.bucket(proc.returncode),
        stdout=proc.stdout,
        stderr=proc.stderr,
        kind=EXITED,
    )


def run_shape(
    shape: Shape,
    located: Located,
    *,
    sample_limit: int | None = ...,  # type: ignore[assignment]
    dry_run: bool = False,
    on_result=None,
) -> ShapeRun:
    limit = shape.sample if sample_limit is ... else sample_limit
    sampled = sample(located.files, limit)
    results: list[Result] = []
    if not dry_run:
        for path in sampled:
            result = run_one(shape, path)
            results.append(result)
            if on_result is not None:
                on_result(result)
    return ShapeRun(shape=shape, located=located, sampled=sampled, results=results)

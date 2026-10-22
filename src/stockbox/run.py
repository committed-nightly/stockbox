"""Run the checker over each located input and bucket the results.

The thing being counted is deliberately not "how many files did we find". It
is "how many files did the checker form an opinion about", because those are
the only ones that tested anything. See the module docstring in `report`.
"""

from __future__ import annotations

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

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

TIMEOUT_CODE = -1  # not a real exit status; subprocess timeouts have none


@dataclass(frozen=True)
class Result:
    path: Path
    command: tuple[str, ...]
    returncode: int | None  # None means it timed out
    bucket: str
    stdout: str
    stderr: str

    @property
    def status(self) -> str:
        return "timeout" if self.returncode is None else str(self.returncode)


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
        )
    except OSError as exc:
        return Result(
            path=path,
            command=command,
            returncode=None,
            bucket="broken",
            stdout="",
            stderr=f"stockbox: could not run {command[0]!r}: {exc}",
        )
    return Result(
        path=path,
        command=command,
        returncode=proc.returncode,
        bucket=shape.bucket(proc.returncode),
        stdout=proc.stdout,
        stderr=proc.stderr,
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

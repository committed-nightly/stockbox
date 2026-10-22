"""Turn runs into something a person reads and a CI job can fail on.

The headline number is verdicts, not candidates. A shape that found 3,140 real
makefiles and got a verdict on none of them has tested nothing, and the report
has to say so in the same breath as the 3,140, or the 3,140 is just a number
that makes the job look thorough.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .run import ShapeRun

EXIT_OK = 0
EXIT_FLOOR = 1
EXIT_USAGE = 2
EXIT_BROKEN = 3

MAX_SHOWN = 10


@dataclass
class Outcome:
    runs: list[ShapeRun]

    @property
    def any_broken(self) -> bool:
        return any(r.broken for r in self.runs)

    @property
    def unmet(self) -> list[ShapeRun]:
        return [r for r in self.runs if not r.floor_met]

    @property
    def exit_code(self) -> int:
        if self.any_broken:
            return EXIT_BROKEN
        if self.unmet:
            return EXIT_FLOOR
        return EXIT_OK


def _shell(command: tuple[str, ...]) -> str:
    import shlex

    return " ".join(shlex.quote(part) for part in command)


def _first_line(result) -> str:
    for stream in (result.stderr, result.stdout):
        for line in stream.splitlines():
            if line.strip():
                return line.strip()
    return "(no output)"


def text(outcome: Outcome, *, dry_run: bool = False) -> str:
    out: list[str] = []
    for run in outcome.runs:
        shape = run.shape
        roots = ", ".join(f"{root}={n}" for root, n in shape_root_items(run))
        out.append(f"{shape.name}: {run.located.total} candidates ({roots})")
        if run.located.missing_roots:
            out.append(
                f"    no such directory: {', '.join(run.located.missing_roots)}"
                f" -- nothing from there is in the corpus"
            )
        if dry_run:
            out.append(f"    would run {len(run.sampled)} of them, starting with:")
            from .run import build_command

            for path in run.sampled[:MAX_SHOWN]:
                out.append(f"      {_shell(build_command(shape, path))}")
            if len(run.sampled) > MAX_SHOWN:
                out.append(f"      ... and {len(run.sampled) - MAX_SHOWN} more")
            continue

        sampled_note = (
            f"sampled {len(run.sampled)}"
            if len(run.sampled) < run.located.total
            else f"ran all {len(run.sampled)}"
        )
        counts = (
            f"verdict {run.verdicts}"
            f"   declined {run.count('declined')}"
            f"   broken {run.count('broken')}"
            f"   soundness {run.count('soundness')}"
        )
        out.append(f"    {sampled_note}: {counts}")

        if run.floor_met:
            out.append(f"    floor of {shape.min_verdicts} verdicts: met")
        else:
            out.append(
                f"    floor of {shape.min_verdicts} verdicts: NOT MET"
                f" -- {run.verdicts} of {len(run.sampled)} inputs got a verdict"
            )
            out.append(f"    {_why_floor_missed(run)}")

        for result in run.broken[:MAX_SHOWN]:
            label = "soundness" if result.bucket == "soundness" else "broken"
            out.append(f"\n    {label}  {result.status}  {result.path}")
            out.append(f"      {_first_line(result)}")
            out.append(f"      reproduce: {_shell(result.command)}")
        if len(run.broken) > MAX_SHOWN:
            out.append(f"\n    ... and {len(run.broken) - MAX_SHOWN} more broken inputs")
        out.append("")

    if dry_run:
        out.append("Nothing was run (--dry-run).")
        return "\n".join(out)

    out.append(_summary(outcome))
    return "\n".join(out)


def _why_floor_missed(run: ShapeRun) -> str:
    """The useful half of a floor failure: which way it went wrong."""
    if not run.sampled:
        return (
            "The corpus is empty. Check the roots exist on this machine and that "
            "`names` matches what the files are actually called."
        )
    # `run.results` guards the empty case: a dry-run ShapeRun has sampled paths
    # and no results, and nothing stops it being rendered without `dry_run`.
    if run.results and len(run.not_run) == len(run.results):
        # Nothing here is about the corpus, so none of the advice below applies.
        # Telling someone to widen their roots because they typo'd `command` is
        # how this report wastes an evening.
        return (
            f"The checker never ran: {_shell(run.results[0].command[:1])} could not "
            f"be started for any of the {len(run.results)} inputs. This says nothing "
            f"about the corpus -- fix `command` or install the checker, then re-run."
        )
    declined = run.count("declined")
    if declined == len(run.sampled):
        return (
            "Every input was declined. The files are real but the checker will not "
            "take them -- usually the shape is a fragment of a larger file rather "
            "than a standalone one. Try a different root."
        )
    if declined > run.verdicts:
        return (
            f"{declined} of {len(run.sampled)} inputs were declined. Most of this "
            f"corpus is not the shape the checker wants."
        )
    return "Raise the sample, widen the roots, or lower the floor on purpose."


def _summary(outcome: Outcome) -> str:
    verdicts = sum(r.verdicts for r in outcome.runs)
    broken = sum(len(r.broken) for r in outcome.runs)
    shapes = len(outcome.runs)
    plural = "" if shapes == 1 else "s"

    if outcome.any_broken:
        not_run = sum(len(r.not_run) for r in outcome.runs)
        if not_run == broken:
            # "broke the checker" would be the wrong story entirely: the checker
            # was never started, so it has not been tested and neither has the
            # corpus. Nothing here is evidence about either one.
            return (
                f"The checker could not be run on any of {not_run} "
                f"input{'' if not_run == 1 else 's'}. Nothing was tested."
            )
        soundness = sum(r.count("soundness") for r in outcome.runs)
        tail = f" ({soundness} of them the checker's own soundness check)" if soundness else ""
        ran = f" {not_run} more could not be run at all." if not_run else ""
        return (
            f"{broken - not_run} input{'' if broken - not_run == 1 else 's'} "
            f"broke the checker{tail}.{ran} "
            f"{verdicts} verdicts across {shapes} shape{plural}."
        )
    if outcome.unmet:
        names = ", ".join(r.shape.name for r in outcome.unmet)
        return (
            f"Corpus too thin: {names} did not reach its floor. "
            f"{verdicts} verdicts across {shapes} shape{plural}."
        )
    return (
        f"{verdicts} real inputs checked across {shapes} shape{plural}, "
        f"every floor met, nothing broken."
    )


def shape_root_items(run: ShapeRun) -> list[tuple[str, int]]:
    return list(run.located.per_root.items())


def as_json(outcome: Outcome) -> str:
    payload = {
        "shapes": [
            {
                "name": run.shape.name,
                "candidates": run.located.total,
                "per_root": run.located.per_root,
                "missing_roots": list(run.located.missing_roots),
                "sampled": len(run.sampled),
                "verdicts": run.verdicts,
                "declined": run.count("declined"),
                "broken": run.count("broken"),
                "soundness": run.count("soundness"),
                "min_verdicts": run.shape.min_verdicts,
                "floor_met": run.floor_met,
                "broken_inputs": [
                    {
                        "path": str(r.path),
                        "exit": r.returncode,
                        "kind": r.kind,  # `exit` is null for timed-out and not-run alike
                        "bucket": r.bucket,
                        "command": list(r.command),
                        "message": _first_line(r),
                    }
                    for r in run.broken
                ],
            }
            for run in outcome.runs
        ],
        "verdicts": sum(r.verdicts for r in outcome.runs),
        "exit": outcome.exit_code,
    }
    return json.dumps(payload, indent=2, sort_keys=True)

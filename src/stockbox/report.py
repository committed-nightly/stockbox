"""Turn runs into something a person reads and a CI job can fail on.

The headline number is verdicts, not candidates. A shape that found 3,140 real
makefiles and got a verdict on none of them has tested nothing, and the report
has to say so in the same breath as the 3,140, or the 3,140 is just a number
that makes the job look thorough.

Verdicts have the same problem one level down, which is what `exercised` is
for. 47 verdicts out of 47 inputs reads like a thorough corpus; if the
construct the checker is about was in 5 of them, 42 of those verdicts are the
checker shrugging. Where a shape declares a `subject_pattern` the report puts
both numbers next to each other, for the same reason it does with candidates.
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
    def unmeasured_shapes(self) -> list[ShapeRun]:
        """Shapes whose `subject_pattern` matched nothing at all.

        Broken rather than too-thin, and for the same reason a checker that
        never started is not a corpus failure: nothing was measured, so there
        is no evidence about the corpus either way. Exits 3 because exit 1
        would send someone to widen roots that are probably fine.
        """
        return [r for r in self.runs if r.measurement_failed]

    @property
    def unmet(self) -> list[ShapeRun]:
        return [r for r in self.runs if not r.floor_met or not r.exercise_floor_met]

    @property
    def exit_code(self) -> int:
        if self.any_broken or self.unmeasured_shapes:
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

        out.extend(_exercise_lines(run))

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


WHY_VACUOUS = (
    "These inputs reached the checker and held none of what it is about, so it "
    "had nothing to decide. No verdict count can show that. Move the roots "
    "towards files that actually contain the construct, or lower the floor on "
    "purpose."
)


def _exercise_lines(run: ShapeRun) -> list[str]:
    """What the checker's own count says about the corpus it was given.

    Nothing at all unless the shape declared a `subject_pattern`: a shape
    without one is not failing to measure, it just isn't measuring, and a line
    reading `exercised 0` would be a finding about the config rather than the
    corpus.
    """
    shape = run.shape
    if shape.subject is None:
        return []
    if run.measurement_failed:
        return [
            f"    exercised: not measured -- /{shape.subject.pattern}/ matched no"
            f" output from any of the {run.verdicts} inputs that got a verdict",
            "    This says nothing about the corpus. Run one input by hand and"
            " check the pattern against what the checker prints.",
        ]
    seen = run.subjects_seen
    lines = [
        f"    exercised {run.exercised} of {run.verdicts} verdicts"
        f" ({seen} {'instance' if seen == 1 else 'instances'} of the subject seen)"
    ]
    if run.unmeasured:
        lines.append(
            f"    {run.unmeasured} of those verdicts printed no count, and are"
            f" neither exercised nor vacuous"
        )
    if shape.min_exercised is None:
        return lines
    if run.exercise_floor_met:
        lines.append(f"    floor of {shape.min_exercised} exercised: met")
    else:
        lines.append(
            f"    floor of {shape.min_exercised} exercised: NOT MET"
            f" -- {run.vacuous} of {run.verdicts} verdicts were vacuous"
        )
        lines.append(f"    {WHY_VACUOUS}")
    return lines


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
    # After the broken branch: a checker that crashes on everything explains an
    # unmatched pattern, and is the bigger news. Reached only when the checker
    # ran fine and the pattern still found nothing.
    if outcome.unmeasured_shapes:
        names = ", ".join(r.shape.name for r in outcome.unmeasured_shapes)
        return (
            f"Not measured: the subject_pattern for {names} matched nothing the "
            f"checker printed. The {verdicts} verdicts are real; the exercised "
            f"counts are not a measurement of anything."
        )
    if outcome.unmet:
        names = ", ".join(r.shape.name for r in outcome.unmet)
        return (
            f"Corpus too thin: {names} did not reach its floor. "
            f"{verdicts} verdicts across {shapes} shape{plural}."
        )
    return (
        f"{verdicts} real inputs checked across {shapes} shape{plural}, "
        f"every floor met, nothing broken.{_exercised_tail(outcome)}"
    )


def _exercised_tail(outcome: Outcome) -> str:
    """The verdict total on its own is the number this tool exists to distrust,
    so where a shape measured its subject the clean summary carries both."""
    measured = [r for r in outcome.runs if r.shape.subject is not None]
    if not measured:
        return ""
    exercised = sum(r.exercised for r in measured)
    of = sum(r.verdicts for r in measured)
    return f" {exercised} of those {of} exercised the subject they were measured for."


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
                "subject_pattern": (
                    run.shape.subject.pattern if run.shape.subject else None
                ),
                "min_exercised": run.shape.min_exercised,
                "exercised": run.exercised,
                "vacuous": run.vacuous,
                "unmeasured": run.unmeasured,
                "subjects_seen": run.subjects_seen,
                "exercise_floor_met": run.exercise_floor_met,
                "measurement_failed": run.measurement_failed,
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
        # Only over shapes that declared a pattern. Summing `exercised` across
        # shapes that never measured would read as a corpus that exercised
        # nothing, which is a different and much worse claim.
        "exercised": sum(r.exercised for r in outcome.runs if r.shape.subject is not None),
        "exit": outcome.exit_code,
    }
    return json.dumps(payload, indent=2, sort_keys=True)

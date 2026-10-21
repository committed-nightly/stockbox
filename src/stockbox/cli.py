"""stockbox's command line.

Two commands that matter. `roots` answers "is there anything of this shape on
this machine, and where" -- the question you have before you can write a
corpus file at all. `run` is the CI job.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load
from .locate import find, sample
from .report import EXIT_USAGE, Outcome, as_json, text
from .run import run_shape

DEFAULT_CONFIG = "corpus.toml"

EPILOG = """\
stockbox runs a checker you name over input files it did not write, found on
the machine it is running on. Those inputs are other people's code, and some
checkers execute their input rather than reading it -- a Makefile checker runs
make, which expands $(shell ...) while reading. Use --dry-run to see the exact
commands first.
"""


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stockbox",
        description=(
            "Run your checker over the real config files already on this machine, "
            "and fail when the corpus tested nothing."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version=f"stockbox {__version__}")
    sub = p.add_subparsers(dest="command")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c",
        "--config",
        default=DEFAULT_CONFIG,
        type=Path,
        help=f"the corpus file (default: {DEFAULT_CONFIG})",
    )
    common.add_argument(
        "-s",
        "--shape",
        action="append",
        metavar="NAME",
        help="only this shape. Repeatable. Default: every shape in the file.",
    )
    common.add_argument("--json", action="store_true", help="machine-readable output")

    r = sub.add_parser(
        "run",
        parents=[common],
        help="run the checker over the corpus and check the floors",
        description=(
            "Locate each shape, run its checker over a sample, and report how many "
            "inputs the checker formed an opinion about. Exits 1 if a shape did not "
            "reach its min_verdicts floor and 3 if any input broke the checker."
        ),
    )
    r.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="override every shape's sample size. 0 means no limit.",
    )
    r.add_argument(
        "--dry-run",
        action="store_true",
        help="locate and print the commands, run nothing",
    )

    sub.add_parser(
        "roots",
        parents=[common],
        help="how many files of each shape each declared root actually holds",
        description=(
            "Count what is on this machine without running anything. The number to "
            "look at is per-root: a root contributing 0 files is a root that is not "
            "on this image, or a `names` list that does not match what the files "
            "are called."
        ),
    )
    return p


def _shapes_for(corpus, selected):
    if not selected:
        return corpus.shapes
    return [corpus.shape(name) for name in selected]


def _cmd_roots(args) -> int:
    corpus = load(args.config)
    shapes = _shapes_for(corpus, args.shape)
    payload = []
    lines = []
    for shape in shapes:
        located = find(shape.roots, shape.names, shape.skip_names)
        payload.append(
            {
                "name": shape.name,
                "names": list(shape.names),
                "candidates": located.total,
                "per_root": located.per_root,
                "missing_roots": list(located.missing_roots),
                "sampled": len(sample(located.files, shape.sample)),
                "min_verdicts": shape.min_verdicts,
            }
        )
        lines.append(f"{shape.name}: {located.total} candidates matching {' '.join(shape.names)}")
        for root, n in located.per_root.items():
            note = "  (no such directory)" if root in located.missing_roots else ""
            flag = "" if n else "   <- contributes nothing"
            lines.append(f"    {n:>7}  {root}{note}{flag}")
        taken = len(sample(located.files, shape.sample))
        if taken < located.total:
            lines.append(f"    a run would sample {taken}, floor {shape.min_verdicts}")
        else:
            lines.append(f"    a run would use all {taken}, floor {shape.min_verdicts}")
        lines.append("")

    if args.json:
        import json

        print(json.dumps({"shapes": payload}, indent=2, sort_keys=True))
    else:
        print("\n".join(lines).rstrip())
    return 0


def _cmd_run(args) -> int:
    corpus = load(args.config)
    shapes = _shapes_for(corpus, args.shape)
    override = ...
    if args.sample is not None:
        if args.sample < 0:
            raise ConfigError("--sample cannot be negative")
        override = None if args.sample == 0 else args.sample

    runs = []
    for shape in shapes:
        located = find(shape.roots, shape.names, shape.skip_names)
        runs.append(
            run_shape(shape, located, sample_limit=override, dry_run=args.dry_run)
        )
    outcome = Outcome(runs=runs)

    if args.json:
        print(as_json(outcome))
    else:
        print(text(outcome, dry_run=args.dry_run))

    return 0 if args.dry_run else outcome.exit_code


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return EXIT_USAGE
    try:
        if args.command == "roots":
            return _cmd_roots(args)
        return _cmd_run(args)
    except ConfigError as exc:
        print(f"stockbox: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("stockbox: interrupted", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())

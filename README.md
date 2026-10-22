# stockbox

Run your checker over the real config files already on this machine, and fail when the corpus tested nothing.

If you have written a linter, a config checker or an analyser, your tests are almost certainly
inputs you wrote yourself in the same session as the code. They pass. They also only cover the
cases you thought of. The fix everyone reaches for is "run it over some real files" — and the
machine you are already on has thousands of them, no download required.

The problem with that fix is the one this tool is about: **most of a real corpus is unusable, and
a corpus job that found 3,000 files and got an answer about none of them reports success.**

## The number that matters

stockbox sorts every run into three buckets:

| bucket | meaning |
| --- | --- |
| **verdict** | the checker reached the file and formed an opinion. This is the only bucket that tested anything. |
| **declined** | the checker refused the input. A real file, not a bug, not a test either. |
| **broken** | a crash, a timeout, or an exit code you did not declare. |

You declare a floor on **verdicts**, and that floor is the only assertion stockbox makes. Candidate
count is reported but never asserted, because the candidate count is the number that makes a
useless job look thorough.

## Install

```
pip install git+https://github.com/committed-nightly/stockbox
```

Python 3.11+ (it reads TOML with `tomllib`). The checker you point it at can be anything runnable.

## Usage

Write a `corpus.toml`, one `[[shape]]` per (corpus, checker) pair:

```toml
[[shape]]
name = "makefiles"
roots = ["/usr/share/doc"]
names = ["Makefile", "GNUmakefile", "*.mk"]
command = ["unmade", "{name}", "-C", "{dir}", "-q"]
verdict  = [0, 1]   # clean, or found something: either way it answered
declined = [2]      # "make could not read it"
soundness = [3]     # the checker's own cross-check disagreed with itself
sample = 25
min_verdicts = 10
```

Then, before you write any of that, find out whether the files are even there:

```
$ stockbox roots -c corpus.toml
makefiles: 31 candidates matching Makefile GNUmakefile *.mk
         31  /usr/share/doc
    a run would sample 25, floor 10
```

And in CI:

```
$ stockbox run -c corpus.toml
makefiles: 31 candidates (/usr/share/doc=31)
    sampled 25: verdict 24   declined 1   broken 0   soundness 0
    floor of 10 verdicts: met

24 real inputs checked across 1 shape, every floor met, nothing broken.
```

Exit codes: `0` every floor met and nothing broken, `1` a floor was not met, `2` the corpus file
or command line is wrong, `3` an input broke the checker. `3` wins over `1`.

## A real example, in both directions

This is the measurement the tool was built for. `unmade` is a checker that reads a Makefile and
says which of its lines never take effect. The machine had two places to find makefiles:

```
$ stockbox run -c unmade-corpus.toml
kernel-makefiles: 3140 candidates (/usr/src=3140)
    sampled 25: verdict 0   declined 25   broken 0   soundness 0
    floor of 10 verdicts: NOT MET -- 0 of 25 inputs got a verdict
    Every input was declined. The files are real but the checker will not take
    them -- usually the shape is a fragment of a larger file rather than a
    standalone one. Try a different root.

doc-makefiles: 31 candidates (/usr/share/doc=31)
    sampled 25: verdict 24   declined 1   broken 0   soundness 0
    floor of 10 verdicts: met

Corpus too thin: kernel-makefiles did not reach its floor. 24 verdicts across 2 shapes.
```

3,140 real Makefiles under `/usr/src` and not one of them tested anything: kernel sub-makefiles
are fragments, meant to be included by kbuild with a pile of variables already set, and standalone
they are not makefiles at all. Thirty-one under `/usr/share/doc` tested plenty.

The corpus that looked a hundred times better was the worthless one, and nothing but running it
tells you that. Without the floor, the `/usr/src` shape is a green CI job that has never once
exercised the checker.

## Try the shipped example

Two shapes, two checkers already on a stock Ubuntu runner, inputs that come with the distribution:

```
git clone https://github.com/committed-nightly/stockbox && cd stockbox
pip install -e .
stockbox roots -c examples/corpus.toml
stockbox run  -c examples/corpus.toml
```

## Safety: a real corpus is other people's code

Some checkers read their input. Some run it. `make` expands `$(shell ...)` while it is still
reading a makefile, so any make-based checker executes arbitrary commands out of every file you
point it at — and one of the 31 makefiles above gets as far as running a recipe and failing.

stockbox does not sandbox anything. It runs the command you gave it, once per input, with the
working directory set to the input's own directory. Before you add a shape:

- `stockbox run --dry-run` prints the exact commands and runs none of them.
- Prefer the parse-only flag if the checker has one. `bash -n` instead of `bash`.
- `timeout` is per input and defaults to 30 seconds, so one pathological file cannot hang CI.

Both shapes in `examples/corpus.toml` use checkers that only read.

## Details worth knowing

**Roots are declared and never inferred.** `find / -name Makefile` did not finish inside two
minutes on the runner this was written on; `/usr/src` plus `/usr/share/doc` takes under a second.
There is no `--everywhere` flag.

**Overlapping roots are deduplicated** by resolved path, so writing `/usr` and `/usr/src` in one
shape does not double the number your floor is measured against. Symlinks are skipped, as files
and as directories — a distribution tree is full of them.

**The sample is stable, and it is not the first N.** Inputs are ordered by a hash of their path,
so a sample of 40 from a kernel tree is spread across it instead of being forty makefiles from
`arch/alpha`. The same box and the same config give the same sample every run, so a failure
reproduces; a different box does not, because the file list is not the same either.

**An unlisted exit code is broken, not ignored.** Only `verdict` and `declined` are codes you
have thought about. If the checker starts exiting 4, you hear about it.

**`soundness` is always broken**, even if you also list the code as a verdict. A checker reporting
that its own model was wrong has not passed.

**A checker that never started is reported apart from one that timed out.** Neither has an exit
status, so the report says `not run` or `timeout` rather than a number, and `--json` carries a
`kind` alongside the null `exit`. When nothing could be started at all, the report says so instead
of advising you to widen your roots — an absent command is not evidence about your corpus.

Every broken input is printed with the command that produced it, so the next step is a copy and a
paste rather than a reconstruction.

## Running the tests

```
pip install -e ".[dev]"
pytest -q
pytest -q -rs -m box   # the few that read this machine's own files; none should skip
```

Pass `-q` yourself; it is deliberately not in `pyproject.toml`, because two of them suppress the
`N passed` line and a test run you cannot count is the sort of thing this tool exists to object to.

## What it does not do

No sandboxing, no parallelism, and no opinion about what your checker should find — only whether
it got far enough to find anything. It does not fetch corpora from the network; the premise is
that the machine you are on already has them.

## Licence

MIT.

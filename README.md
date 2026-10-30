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
| **verdict** | the checker reached the file and formed an opinion. The only bucket that tested anything — necessary, and not sufficient; see the next section. |
| **declined** | the checker refused the input. A real file, not a bug, not a test either. |
| **broken** | a crash, a timeout, or an exit code you did not declare. |

You declare a floor on **verdicts**. Candidate count is reported but never asserted, because the
candidate count is the number that makes a useless job look thorough.

## The number that matters one level down

A verdict means the checker read the file. It does not mean the file held anything the checker is
*about*, and the narrower the checker, the less often it did:

```
json-descriptions: 1690 candidates (/usr/lib/python3=1688, /usr/share/doc=2)
    sampled 60: verdict 60   declined 0   broken 0   soundness 0
    floor of 40 verdicts: met
    exercised 6 of 60 verdicts (95 instances of the subject seen)
```

Sixty files, sixty verdicts, nothing declined, nothing broken, floor comfortably met. Six of those
files contained the construct the checker looks for. The other fifty-four verdicts are the checker
opening a file and shrugging — real inputs, honestly reached, and no more a test than the 3,140
makefiles below.

The only thing that can tell you which is which is the checker's own count of what it saw, so
stockbox reads it back out of the output. Declare the pattern and, when you have measured it, a
floor:

```toml
subject_pattern = '^([0-9]+) cache steps?'   # one group, and it has to be the number
min_exercised = 3
```

| | |
| --- | --- |
| **exercised** | a verdict where the checker reported at least one of its subject. |
| **vacuous** | a verdict where it reported none. The file was fine; it just wasn't about anything. |
| **not measured** | a verdict whose output the pattern didn't match. Not the same as zero — see below. |

Three things worth knowing before you use it:

- **A pattern that matches nothing exits 3, not 1.** "No cache steps here" is evidence about your
  corpus; *silence* is evidence about your pattern. Reporting a typo'd regex as a thin corpus would
  send you to widen roots that were never the problem, so it is reported as broken, with the
  pattern quoted back at you.
- **The floor counts inputs, not instances.** 95 instances across 6 files is a corpus of six things,
  and a floor on the total would be met by one pathological file. The instance count is reported
  because it is interesting and never asserted on, which also means a pattern that double-counts
  cannot change a result.
- **`stockbox roots` cannot predict this.** Counting files tells you nothing about what is in them.
  Only a run measures it, and the `roots` output says so rather than guessing.

A pattern with no `min_exercised` is fine and asserts nothing — that is the state you want while
you are finding out what the number is.

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

Optionally, the second floor — how many of those verdicts actually had something to decide:

```toml
subject_pattern = '^([0-9]+) lines never used'
min_exercised = 5
```

## When the directory is the discriminator

Sometimes what makes a file the shape you want is not its name. The six directories on an
`ubuntu-latest` runner that hold vendored project trees have 120 real GitHub Actions workflow
files between them, in 47 projects — and 738 files called `*.yml` or `*.yaml`, measured on the
2026-10-30 runner image. The only thing that tells the 120 from the other 618 is the
`.github/workflows/` above them. So a `names` pattern is matched against as many trailing path
components as it has:

```toml
names = [".github/workflows/*.yml", ".github/workflows/*.yaml"]
```

A pattern with one component is a basename match, which is all `names` ever did. A `*` matches
within one component and never across a `/`, so the component count is the depth you wrote.
Patterns are matched against the *end* of a path and never anchored to the root, which means
`.github/workflows/*.yml` works at any depth and slicing your roots differently cannot change
what matches. There is no `**`: a pattern claims a fixed number of components, so a corpus file
asking for one is rejected rather than quietly matching a single directory.

Checkers that work on this kind of corpus usually want the project, not the file. `{tree}` is the
directory the pattern began matching at — the one holding `.github` above — so this is the whole
shape:

```toml
[[shape]]
name = "workflows"
roots = ["/usr/lib/google-cloud-sdk/platform", "/usr/local/lib/node_modules"]
names = [".github/workflows/*.yml"]
command = ["coldcache", "{tree}"]
verdict = [0, 1]
min_verdicts = 10
```

For a basename pattern `{tree}` is `{dir}`, so nothing written before it existed has changed. The
working directory follows `{tree}` for the same reason.

**Two inputs that build the same command line are one input.** The five of those roots whose
paths carry no version number hold 81 files in 30 trees, so `coldcache {tree}` has 30 things to
say and 51 of the files would be a repeat of a run already made:

```
workflows: 81 candidates in 30 trees (/usr/lib/google-cloud-sdk/platform=36, ...)
    51 of them build a command line another input already builds, and are not run again -- 30 inputs left
    ran all 30: verdict 30   declined 0   broken 0   soundness 0
```

Reporting 81 verdicts there would be this tool inflating exactly the number it exists to keep
honest. The collapse happens before the sample, so `sample = 20` means twenty runs and not twenty
files. It is not about `{tree}`: a command naming only `{dir}` has always had the same problem,
one Makefile per directory rather than one per tree, and until now it was always counted wrong.

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
or command line is wrong, `3` an input broke the checker or a `subject_pattern` measured nothing.
`3` wins over `1`.

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

## The same thing, one level down

A stock `ubuntu-latest` runner has 119 real GitHub Actions workflow files on it, in 47 project
trees vendored inside the Julia standard library, the Google Cloud SDK, `node_modules`, nvm,
vcpkg and miniconda. Written by strangers for their own projects, which is exactly what you want.

Four checkers in this org read workflow files. Pointed at those 47 trees on 2026-10-28, with a
shell loop, because `names` could not express the corpus until the section above existed:

| checker | verdicts | trees where it saw its own subject |
| --- | --- | --- |
| `groupclash` | 47 of 47 | 4 — five `concurrency:` blocks between them |
| `coldcache` | 47 of 47 | 5 — ten `actions/cache` steps between them |
| `blankout` | 47 of 47 | unknowable: it reports no count of anything |
| `deadtrigger` | 2 of 47 | — declines 45, "not a git repository" |

Read the first column and this is a good corpus four times over. Read the second and only one of
these shapes is worth having in CI.

`deadtrigger` is the ugly joke in it. Its subject is a trigger block, which is in 117 of the 119
files — by far the densest of the four — and it is the one checker that cannot use the corpus at
all, because it checks triggers against the repository they sit in and a vendored tree has no
`.git`. The three that accept everything are the three with nothing to look at. Nothing in a
verdict count tells you that; `declined 45` on the row that would actually have tested something
is as close as the old report could get, and it points the wrong way.

`coldcache`'s five trees were enough to find something real: a restore-key in Julia's
`SuiteSparse` stdlib that is not a prefix of any key anything saves, so it can never match. Five
trees of forty-seven. The other forty-two verdicts contributed nothing and cost the same.

And `blankout`'s row is the honest limit of this feature. It has nothing to read back — no count
in its text output, none in `--json` — so there is no pattern to write and no floor to set. A
checker that does not report its own denominator cannot be measured this way, which is a reason
to report one.

That table is now a `corpus.toml` rather than a shell loop. Re-run on 2026-10-29 as one, over the
five roots whose paths do not carry a version number:

```
coldcache-trees: 81 candidates in 30 trees (...)
    51 of them build a command line another input already builds, and are not run again -- 30 inputs left
    ran all 30: verdict 30   declined 0   broken 0   soundness 0
    floor of 10 verdicts: met
    exercised 1 of 30 verdicts (6 instances of the subject seen)
```

Thirty verdicts and one of them had anything to look at. The image had moved — a julia version
bump took 39 files and their trees with it — which is its own argument for measuring rather than
quoting: every number in this file is a date.

## Try the shipped example

Four shapes, four checkers already on a stock Ubuntu runner, inputs that come with the
distribution. The third is the vacuous-corpus case above as it actually runs, and the fourth is
the path-pattern and `{tree}` case:

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

All four shapes in `examples/corpus.toml` use checkers that only read.

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

**Braces in a `command` are yours, not stockbox's.** `{path}`, `{dir}`, `{name}` and `{tree}` are
replaced; every other brace is passed through untouched, so an awk program, a jq filter and a
shell brace expansion all work as written and need no escaping. A brace-wrapped word that *isn't*
one of the four — `{pathh}` — is rejected when the corpus file loads, because a plain replacement
would otherwise hand the checker the literal string and let it complain about a missing file.

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

# servo-ohos WPT

The [web-platform-tests](https://web-platform-tests.org/) results of every `ohos-main` commit of
[servo-ohos](https://github.com/openharmony-rs/servo-ohos) and of the upstream Servo commit it is
based on, and the dashboard that shows their pass rates:
<https://openharmony-rs.github.io/servo-ohos-wpt/>. This repository runs no tests itself: the
results come from the CI of servo-ohos and of servo/servo.

## Configurations

| Configuration                 | Results                                                                          |
| ----------------------------- | -------------------------------------------------------------------------------- |
| `linux-spidermonkey`          | the Linux WPT job of servo-ohos for an `ohos-main` push, `checked-release`, 20 chunks |
| `linux-quickjs`               | the QuickJS WPT job of the same servo-ohos run, on the servo-quickjs commit that `ohos-main` pins |
| `upstream-linux-spidermonkey` | the Linux WPT job of servo/servo's merge queue for the commit that `ohos-main` is based on |
| `ohos-dayu200-quickjs`        | servo-ohos's nightly device WPT run on a DAYU200 board, `checked-release` with QuickJS and the RDB storage backend, a subset of WPT in one chunk |

Until 2026-10-22, this repository built servoshell (`release`) and ran WPT itself; those runs
are kept. Each configuration has its own score history, and two runs of different configurations
can be compared with `just diff`. The DAYU200 runs only cover a subset of WPT, the one set in
servo-ohos's `ohos-device-wpt.yml`, so their total score is the pass rate of that subset; compare them
with the other configurations per directory.

The runs of servo-ohos record their upstream base, the servo/servo commit that the tested
revision is based on (its merge base with servo/servo `main`). The dashboard draws the
`linux-spidermonkey` runs together with the `upstream-linux-spidermonkey` run of their base,
so the gap between the two lines is the effect of the servo-ohos patches, and marks the runs
whose base differs from the previous run, that is the rebases onto a newer upstream.

`compare.html` shows the differences between OpenHarmony and Linux: the latest run of each
configuration with `compare` in `configs.json`, test by test, against the run of the configuration it
names that tested the same commit, or else that configuration's latest run. Only the tests that both
runs ran count, so per directory both scores are over the same tests; then it lists the tests and
subtests whose results differ.

## How runs are stored

Runs are not files on a branch. Each run is an orphan commit under
`refs/runs/<config>/<name>`, where the name is the date the run was stored, `YYYY-MM-DD`, with a
`.<n>` suffix for the later runs of the same day (`2026-10-22.2`). The runs stored together, for
one `ohos-main` commit, share their name, also across configurations.

- The tree mirrors the WPT test paths, with one JSON file per test that holds only the test
  status and the subtest names and statuses. The file name is the URI-encoded test name plus
  `.json`, so `/dom/foo.html?x=1` is stored as `dom/foo.html%3Fx%3D1.json`.
- `run.json` at the root holds the run info (browser version, revision, start and end time)
  and, for servo-ohos runs, the upstream base as `base_revision`.
- `scores.json` at the root holds the scores of every WPT directory, computed with
  `wpt calc-scores` when the run is stored, without the skipped tests. Each directory has a
  `[tests, score, subtests, subtests passed]` tuple, where the score is the sum over tests of
  the fraction of passing subtests, like Servo's own dashboard.
- Durations and failure messages are dropped, so a result that did not change is a
  byte-identical file and git stores it only once. A full run has about 58,000 tests but
  about 20,000 distinct results, and a new run typically adds a few hundred objects.

The layout follows
[results-analysis-cache](https://github.com/web-platform-tests/results-analysis-cache), which
stores the wpt.fyi runs the same way.

A plain `git clone` does not fetch `refs/runs/*`. Use `just fetch`, or:

```sh
git fetch --no-tags origin '+refs/runs/*:refs/runs/*'
```

## Dashboard data

Each run is scored once, against its own tests. The site is built from the stored scores in
the summary format of
[blitz-wpt-results](https://github.com/DioxusLabs/blitz-wpt-results/tree/main/summary):

```text
summary/
  index.json                  # the configurations
  <config>/
    runs.json                 # one entry per run: name, date, wpt_revision, product_revision, base_revision
    total.json                # the scores of all tests, one tuple (or null) per run
    areas/css/css-flexbox.json  # the scores of one directory, aligned with runs.json
    latest.json               # the scores of every directory in the latest run
    tests/dom/nodes.json      # the tests of one directory in the latest run (tests.json for the root)
    changes.json              # the tests whose result changed since the previous run
```

A test is a `[name, status, subtests passed, subtests, change]` row, where the change is `null`,
`"new"`, or the result of the previous run.

The charts also show upstream Servo's [wpt.fyi](https://wpt.fyi/results/?product=servo) runs,
which [browser-wpt-results](https://github.com/DioxusLabs/browser-wpt-results) scores in the
same format. The page fetches them from GitHub when it is viewed. wpt.fyi runs the whole test
suite, while Servo's CI skips some directories, so the upstream line is only shown where both
ran about as many tests.

Because runs are not re-scored against the latest test list, changes to the test suite (for
example with a sync of `ohos-main`) show up as steps in the history. `just rescore`
recomputes the stored scores, for example after updating the `wpt` CLI.

## Working with runs

The recipes in the `justfile` need Python 3.11 or later and git. `just store`, `just rescore`
and `just diff` also need the `wpt` CLI (`cargo install wpt`, or set `WPT` to its path).

```sh
just fetch                                     # fetch all runs
just list linux-spidermonkey                   # list the runs, the revisions they tested and their bases
just export linux-spidermonkey/2026-10-08 out.json         # rebuild a wptreport
just diff linux-spidermonkey/2026-10-08 linux-spidermonkey/2026-10-08.2 -v
just store linux-spidermonkey 2026-10-08 wptreport/*.json  # store wptreport chunks
just set-base <servo/servo commit> linux-spidermonkey/2026-10-08  # record the upstream base of a run
just store-upstream <servo/servo commit>       # store servo/servo's results for a commit (needs gh)
just serve                                     # build the dashboard locally
```

The recipes change only the local refs. Push new runs with `git push origin <ref>`, and the runs
that `set-base` or `rescore` rewrote with `git push --force origin 'refs/runs/*:refs/runs/*'`;
then run `publish.yml`.

`store` accepts the files that `./mach test-wpt --log-wptreport` writes, also as JSON Lines
and `.gz`/`.xz`. When a test appears more than once, the last result wins.

## Workflows

- servo-ohos's `ohos-main-wpt.yml` runs WPT with SpiderMonkey and with QuickJS on every push to
  `ohos-main`, and then calls `store.yml` of this repository. Its runs go one at a time, so
  `store.yml` never runs twice at once; of several pushes waiting for a run, only the latest
  runs.
- servo-ohos's `ohos-device-wpt.yml` runs a subset of WPT every night on the DAYU200 board of a
  self-hosted runner, when `ohos-main` changed since its last stored run, and calls `store.yml`
  with `chunks: 1` and `upstream: false`.
- `store.yml` runs in servo-ohos. It stores the results of the configurations that have all
  their chunks and no run of the revision yet. It looks up the upstream base with the GitHub
  compare API and, unless it has a run already, stores the results of servo/servo's CI for it,
  which keeps them for 90 days. Then it pushes the runs and starts `publish.yml`.
- `publish.yml` builds the summary from the stored scores and the latest two runs, and deploys
  the site.

`store.yml` takes the token from the `servo-ohos-wpt` environment of servo-ohos, which only
`ohos-main` can use. Create the environment, with that branch policy, before servo-ohos gets the
workflow: GitHub creates a missing environment without any policy. Its `SERVO_OHOS_WPT_TOKEN` secret is a fine-grained personal access token
with read and write access to the contents of this repository. It pushes the runs, starts
`publish.yml`, and downloads servo/servo's artifacts, which a fine-grained token can do for
any public repository.

To add a configuration, add its WPT artifact to the `artifacts` input of the `store.yml` call
in servo-ohos and give it a label in `configs.json`. To remove a broken run, delete its ref with
`git push origin --delete refs/runs/<config>/<name>` and run `publish.yml`.

## License

[MPL-2.0](LICENSE)

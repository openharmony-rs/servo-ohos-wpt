# servo-ohos WPT

Nightly [web-platform-tests](https://web-platform-tests.org/) runs of
[servo-ohos](https://github.com/openharmony-rs/servo-ohos) (`ohos-main`), and the dashboard
that shows their pass rates: <https://openharmony-rs.github.io/servo-ohos-wpt/>.

## Configurations

| Configuration        | What runs                                                                       |
| -------------------- | ------------------------------------------------------------------------------- |
| `linux-spidermonkey` | servoshell built from `ohos-main` on a GitHub-hosted Linux runner, 20 WPT chunks |
| `linux-quickjs`      | the same, built with `js-quickjs` on the servo-quickjs commit that `ohos-main` pins |

Runs on OpenHarmony are planned. Each configuration has its own score history, and two runs
of different configurations can be compared with `just diff`.

## How runs are stored

Runs are not files on a branch. Each run is an orphan commit under
`refs/runs/<config>/<YYYY-MM-DD>`:

- The tree mirrors the WPT test paths, with one JSON file per test that holds only the test
  status and the subtest names and statuses. The file name is the URI-encoded test name plus
  `.json`, so `/dom/foo.html?x=1` is stored as `dom/foo.html%3Fx%3D1.json`.
- `run.json` at the root holds the run info (browser version, revision, start and end time).
- `scores.json` at the root holds the scores of every WPT directory, computed with
  `wpt calc-scores` when the run is stored, without the skipped tests. Each directory has a
  `[tests, score, subtests, subtests passed]` tuple, where the score is the sum over tests of
  the fraction of passing subtests, like Servo's own dashboard.
- Durations and failure messages are dropped, so a result that did not change is a
  byte-identical file and git stores it only once. A full run has about 58,000 tests but
  about 20,000 distinct results, and a new night typically adds a few hundred objects.

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
    runs.json                 # one entry per run: date, wpt_revision, product_revision
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
just list linux-spidermonkey                   # list the runs and the revisions they tested
just export linux-spidermonkey/2026-10-08 out.json         # rebuild a wptreport
just diff linux-spidermonkey/2026-10-07 linux-spidermonkey/2026-10-08 -v
just store linux-spidermonkey 2026-10-08 wptreport/*.json  # store wptreport chunks
just serve                                     # build the dashboard locally
```

`store` accepts the files that `./mach test-wpt --log-wptreport` writes, also as JSON Lines
and `.gz`/`.xz`. When a test appears more than once, the last result wins.

## Workflows

- `nightly.yml` runs every night. It calls `run-linux.yml` for each configuration and then
  `publish.yml`. Run it by hand to test another `servo-ohos` ref, or with `force` to test a
  revision again and replace the runs of the day.
- `run-linux.yml` skips the revision if the configuration already has a run of it, as
  `ohos-main` only moves with syncs. Otherwise it builds servoshell, runs WPT in chunks, and
  scores and pushes the run.
- `publish.yml` builds the summary from the stored scores and the latest two runs, and deploys
  the site.

To add a configuration, add a job to `nightly.yml`, list it in the `needs` of the `publish`
job and give it a label in `configs.json`.

## License

[MPL-2.0](LICENSE)

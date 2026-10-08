#!/usr/bin/env python3
"""Stores WPT runs in git and builds the dashboard data from them.

Every run is an orphan commit under refs/runs/<config>/<YYYY-MM-DD>. Its tree
mirrors the WPT test paths, with one JSON file per test that holds only the
test status and the subtest names and statuses. Results that did not change
between runs are byte-identical files, which git stores only once. The layout
follows web-platform-tests/results-analysis-cache. Two files at the root of
the tree describe the run: `run.json` holds the run info, and `scores.json`
the scores of every WPT directory, computed once when the run is stored.

The dashboard data uses the summary format of DioxusLabs/blitz-wpt-results.
"""

import argparse
import gzip
import json
import lzma
import math
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote

REF_PREFIX = "refs/runs/"
RUN_FILE = "run.json"
SCORES_FILE = "scores.json"
CONFIG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VERSION_RE = re.compile(r"^(?:Version: )?Servo (\S+?)(?:-dirty)?$")
REPO_ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=input, stdout=subprocess.PIPE, check=True).stdout


def ref_exists(ref: str) -> bool:
    result = subprocess.run(["git", "rev-parse", "--verify", "-q", ref], stdout=subprocess.DEVNULL, check=False)
    return result.returncode == 0


def run_ref(run: str) -> str:
    config, _, date = run.partition("/")
    if not CONFIG_RE.match(config) or not DATE_RE.match(date):
        sys.exit(f"invalid run '{run}', expected <config>/<YYYY-MM-DD>")
    return REF_PREFIX + run


def cat_files(names: list[str]) -> list[bytes]:
    """Reads git objects (e.g. `<commit>:run.json`), in the order given."""
    out = git("cat-file", "--batch", input="".join(f"{name}\n" for name in names).encode())
    contents = []
    pos = 0
    for name in names:
        header_end = out.index(b"\n", pos)
        header = out[pos:header_end].decode().split(" ")
        if header[-1] == "missing":
            sys.exit(f"git object {name} is missing")
        start = header_end + 1
        size = int(header[2])
        contents.append(out[start : start + size])
        pos = start + size + 1
    return contents


def open_text(path: Path):
    if path.suffix == ".xz":
        return lzma.open(path, "rt", encoding="utf-8")
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, encoding="utf-8")


def read_json_values(path: Path):
    """Yields every JSON value in a file, so that both plain JSON and JSON Lines work."""
    with open_text(path) as f:
        text = f.read()
    decoder = json.JSONDecoder()
    pos = 0
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos == len(text):
            return
        value, pos = decoder.raw_decode(text, pos)
        yield value


def normalize_browser_version(run_info: dict) -> None:
    version = run_info.get("browser_version")
    if isinstance(version, str):
        match = VERSION_RE.match(version.strip())
        if match:
            run_info["browser_version"] = match.group(1)


def merge_reports(paths: list[Path]) -> dict:
    """Merges wptreport chunks. A later result for the same test replaces an earlier one."""
    run_info = None
    time_start = None
    time_end = None
    results = {}
    for path in paths:
        for report in read_json_values(path):
            if run_info is None:
                run_info = report["run_info"]
            elif report["run_info"] != run_info:
                sys.exit(f"{path}: run_info differs from the other reports")
            time_start = report["time_start"] if time_start is None else min(time_start, report["time_start"])
            time_end = report["time_end"] if time_end is None else max(time_end, report["time_end"])
            for result in report["results"]:
                if result.get("subsuite"):
                    sys.exit(f"{path}: subsuites are not supported ({result['test']})")
                results[result["test"]] = result
    if run_info is None:
        sys.exit("no reports given")
    normalize_browser_version(run_info)
    return {"run_info": run_info, "time_start": time_start, "time_end": time_end, "results": results}


def split_test(test: str) -> tuple[str, str]:
    """Splits a test id into its directory (without the leading slash) and its name, which may contain a slash
    in the query, like results-analysis' splitTestPath()."""
    query = test.find("?")
    slash = test.rfind("/", 0, query if query >= 0 else len(test))
    return "/".join(d for d in test[:slash].split("/") if d), test[slash + 1 :]


def test_file_path(test: str) -> str:
    """Maps a test id to its path in the tree, like results-analysis' splitTestPathEncodedName()."""
    directory, name = split_test(test)
    file = quote(name, safe="!'()*") + ".json"
    return f"{directory}/{file}" if directory else file


def test_id(file_path: str) -> str:
    dirs, _, name = file_path.rpartition("/")
    prefix = f"/{dirs}" if dirs else ""
    return f"{prefix}/{unquote(name.removesuffix('.json'))}"


def stored_result(result: dict) -> dict:
    stored = {"status": result["status"]}
    subtests = [{"name": s["name"], "status": s["status"]} for s in result.get("subtests") or []]
    if subtests:
        stored["subtests"] = subtests
    return stored


def report_json(report: dict, results: list[dict]) -> str:
    return json.dumps(
        {
            "run_info": report["run_info"],
            "time_start": report["time_start"],
            "time_end": report["time_end"],
            "results": results,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def score_report(report: dict, wpt: str) -> dict:
    """Scores every directory of a report with `wpt calc-scores`, without the skipped tests.

    Returns {"scorer": <wpt version>, "areas": {<area>: [tests, score, subtests, subtests passed]}}, where the
    area is the directory without the leading slash ("" for the whole run) and the score is the sum over tests
    of the fraction of passing subtests, rounded to one decimal, as in blitz-wpt-results.
    """
    results = [
        {"test": test, "duration": 0, **stored_result(result)}
        for test, result in sorted(report["results"].items())
        if result["status"] != "SKIP"
    ]
    with tempfile.TemporaryDirectory() as tmp:
        report_path = Path(tmp, "report.json")
        scores_path = Path(tmp, "scores.json")
        report_path.write_text(report_json(report, results), encoding="utf-8")
        subprocess.run(
            [wpt, "calc-scores", "--in", str(report_path), "--out", str(scores_path)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        by_area = json.loads(scores_path.read_text())["scores_by_area"]
    version = subprocess.run([wpt, "--version"], stdout=subprocess.PIPE, check=True, text=True).stdout.strip()
    areas = {
        area.removeprefix("/"): [
            s["tests"]["total"],
            math.floor(s["pass_fraction_sum"] * 10 + 0.5) / 10,
            s["subtests"]["total"],
            s["subtests"]["pass"],
        ]
        for area, s in sorted(by_area.items())
    }
    return {"scorer": version, "areas": areas}


def fast_import_path(path: str) -> bytes:
    if path.startswith('"') or any(c in path for c in '\\\n"'):
        path = '"' + path.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    return path.encode()


def committer_identity() -> str:
    ident = git("var", "GIT_COMMITTER_IDENT").decode().strip()
    return ident.rsplit(" ", 2)[0]


def write_run(config: str, date: str, report: dict, wpt: str, force: bool) -> None:
    """Writes a merged report as the orphan commit refs/runs/<config>/<date>."""
    ref = run_ref(f"{config}/{date}")
    if ref_exists(ref) and not force:
        sys.exit(f"{ref} already exists, pass --force to replace it")

    marks: dict[bytes, int] = {}
    blobs = []
    entries = []
    paths = {RUN_FILE, SCORES_FILE}
    for test, result in sorted(report["results"].items()):
        path = test_file_path(test)
        if path in paths:
            sys.exit(f"test {test} maps to a path that is already taken: {path}")
        paths.add(path)
        content = json.dumps(stored_result(result), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        mark = marks.get(content)
        if mark is None:
            mark = marks[content] = len(marks) + 1
            blobs.append(b"blob\nmark :%d\ndata %d\n%s\n" % (mark, len(content), content))
        entries.append(b"M 100644 :%d %s\n" % (mark, fast_import_path(path)))

    run = {
        "config": config,
        "date": date,
        "time_start": report["time_start"],
        "time_end": report["time_end"],
        "run_info": report["run_info"],
    }
    scores = score_report(report, wpt)
    areas = ",\n".join(f"{json.dumps(area)}:{json.dumps(value)}" for area, value in scores["areas"].items())
    for name, text in [
        (RUN_FILE, json.dumps(run, indent=1)),
        (SCORES_FILE, f'{{"scorer":{json.dumps(scores["scorer"])},"areas":{{\n{areas}\n}}}}'),
    ]:
        content = text.encode() + b"\n"
        entries.append(b"M 100644 inline %s\ndata %d\n%s\n" % (name.encode(), len(content), content))

    info = report["run_info"]
    version = info.get("browser_version") or "unknown version"
    message = f"{config} {date}\n\n{version} ({info.get('revision')})\n".encode()
    stream = b"".join(
        [
            *blobs,
            b"reset %s\n" % ref.encode(),
            b"commit %s\n" % ref.encode(),
            b"committer %s %d +0000\n" % (committer_identity().encode(), report["time_end"] // 1000),
            b"data %d\n%s\n" % (len(message), message),
            *entries,
            b"done\n",
        ]
    )
    command = ["git", "fast-import", "--quiet", "--done"]
    if force:
        command.append("--force")
    subprocess.run(command, input=stream, check=True)
    print(f"stored {len(report['results'])} tests ({len(marks)} distinct results) as {ref}")


def export_run(ref: str) -> dict:
    """Rebuilds the merged report of a stored run, with the results keyed by test."""
    files = {}
    for record in git("ls-tree", "-r", "-z", "--full-tree", ref).split(b"\0"):
        if record:
            meta, _, path = record.decode().partition("\t")
            files[path] = meta.split(" ")[2]
    run_oid = files.pop(RUN_FILE)
    files.pop(SCORES_FILE, None)
    oids = sorted(set(files.values()))
    blobs = dict(zip(oids, cat_files(oids), strict=True))
    run = json.loads(cat_files([run_oid])[0])
    results = {}
    for path, oid in files.items():
        test = test_id(path)
        results[test] = {"test": test, "duration": 0, "subtests": [], **json.loads(blobs[oid])}
    return {
        "run_info": run["run_info"],
        "time_start": run["time_start"],
        "time_end": run["time_end"],
        "results": results,
    }


def stored_runs() -> dict[str, dict[str, str]]:
    """Returns {config: {date: commit}} for all stored runs."""
    runs: dict[str, dict[str, str]] = {}
    out = git("for-each-ref", "--format=%(refname) %(objectname)", REF_PREFIX).decode()
    for line in out.splitlines():
        ref, oid = line.split(" ")
        config, _, date = ref.removeprefix(REF_PREFIX).partition("/")
        if CONFIG_RE.match(config) and DATE_RE.match(date):
            runs.setdefault(config, {})[date] = oid
    return runs


def read_run_files(commits: list[str], name: str) -> list[dict]:
    return [json.loads(content) for content in cat_files([f"{commit}:{name}" for commit in commits])]


def store(args: argparse.Namespace) -> None:
    write_run(args.config, args.date, merge_reports(args.reports), args.wpt, args.force)


def export(args: argparse.Namespace) -> None:
    report = export_run(run_ref(args.run))
    output = report_json(report, [report["results"][test] for test in sorted(report["results"])])
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    else:
        sys.stdout.write(output)


def list_runs(args: argparse.Namespace) -> None:
    for config, dates in sorted(stored_runs().items()):
        if args.config in (None, config):
            ordered = sorted(dates.items())
            runs = read_run_files([oid for _, oid in ordered], RUN_FILE)
            for (date, _), run in zip(ordered, runs, strict=True):
                print(f"{config}/{date} {run['run_info'].get('revision')}")


def tested(args: argparse.Namespace) -> None:
    """Exits with 0 if a run of the configuration tested the revision, and 1 otherwise."""
    dates = stored_runs().get(args.config, {})
    for date, run in zip(dates, read_run_files(list(dates.values()), RUN_FILE), strict=True):
        if run["run_info"].get("revision") == args.revision:
            print(f"{args.config}/{date} tested {args.revision}")
            return
    sys.exit(1)


def rescore(args: argparse.Namespace) -> None:
    for config, dates in sorted(stored_runs().items()):
        if args.config in (None, config):
            for date, oid in sorted(dates.items()):
                write_run(config, date, export_run(oid), args.wpt, force=True)


def run_meta(run: dict) -> dict:
    """The run metadata of the blitz-wpt-results summary format."""
    revision = run["run_info"].get("revision") or ""
    date = datetime.fromtimestamp(run["time_start"] / 1000, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"date": date, "wpt_revision": revision[:9], "product_revision": revision}


def write_json_lines(path: Path, key: str, lines: list) -> None:
    """Writes {key: [...]} with one entry per line, like blitz-wpt-results."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(json.dumps(line, separators=(",", ":")) for line in lines)
    path.write_text(f'{{\n"{key}":[\n{body}\n]}}\n')


def write_summary(directory: Path, runs: list[dict], scores: list[dict]) -> None:
    """Writes the scores of runs sorted by date in the blitz-wpt-results summary format."""
    write_json_lines(directory / "runs.json", "runs", runs)
    for area in sorted({area for run in scores for area in run}):
        path = directory / "total.json" if area == "" else directory / "areas" / f"{area}.json"
        write_json_lines(path, "scores", [run.get(area) for run in scores])
    (directory / "latest.json").write_text(json.dumps({"run": runs[-1], "scores": scores[-1]}) + "\n")


def result_summary(result: dict | None) -> list:
    """[status, subtests passed, subtests] of a test, or [None, 0, 0] for a test that did not run."""
    if result is None:
        return [None, 0, 0]
    subtests = result.get("subtests") or []
    return [result["status"], sum(s["status"] == "PASS" for s in subtests), len(subtests)]


def write_tests(directory: Path, latest: dict, previous: dict | None, header: dict) -> None:
    """Writes the tests of the latest run per directory and the changes since the previous run.

    Every test is a [name, status, subtests passed, subtests, change] row in tests/<directory>.json (tests.json for
    the root), where change is null when the result did not change, "new" for a test that the previous run did
    not have, and the previous [status, subtests passed, subtests] otherwise. changes.json lists the changed tests
    of all directories, with the test id in place of the name.
    """
    tests = latest["results"].keys() | (previous["results"].keys() if previous else set())
    by_directory = defaultdict(list)
    changes = []
    for test in sorted(tests):
        now = result_summary(latest["results"].get(test))
        change = None
        if previous:
            before = previous["results"].get(test)
            if before is None:
                change = "new"
            elif result_summary(before) != now:
                change = result_summary(before)
        directory_name, name = split_test(test)
        by_directory[directory_name].append([name, *now, change])
        if change is not None:
            changes.append([test, *now, change])
    for directory_name, rows in by_directory.items():
        path = directory / "tests.json" if directory_name == "" else directory / "tests" / f"{directory_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**header, "tests": rows}, separators=(",", ":"), ensure_ascii=False))
    (directory / "changes.json").write_text(
        json.dumps({**header, "changes": changes}, separators=(",", ":"), ensure_ascii=False)
    )


def site(args: argparse.Namespace) -> None:
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(REPO_ROOT / "site", out)
    known = json.loads((REPO_ROOT / "configs.json").read_text())
    order = {c["id"]: slot for slot, c in enumerate(known)}
    labels = {c["id"]: c["label"] for c in known}
    all_runs = stored_runs()
    index = []
    for config in sorted(all_runs, key=lambda c: (order.get(c, len(order)), c)):
        commits = list(all_runs[config].values())
        runs = [run_meta(run) for run in read_run_files(commits, RUN_FILE)]
        by_date = sorted(range(len(runs)), key=lambda i: (runs[i]["date"], runs[i]["product_revision"]))
        commits = [commits[i] for i in by_date]
        runs = [runs[i] for i in by_date]
        scores = [s["areas"] for s in read_run_files(commits, SCORES_FILE)]
        write_summary(out / "summary" / config, runs, scores)
        previous = export_run(commits[-2]) if len(commits) > 1 else None
        header = {"run": runs[-1]["date"], "previous": runs[-2]["date"] if previous else None}
        write_tests(out / "summary" / config, export_run(commits[-1]), previous, header)
        index.append(
            {
                "id": config,
                "label": labels.get(config, config),
                "slot": order.get(config, len(order) + len(index)),
                "runs": len(runs),
            }
        )
    (out / "summary").mkdir(exist_ok=True)
    (out / "summary" / "index.json").write_text(json.dumps({"configs": index}, indent=1) + "\n")
    print(f"wrote {out} with {len(index)} configurations")


def matching(pattern: re.Pattern, what: str):
    def check(value: str) -> str:
        if not pattern.match(value):
            raise argparse.ArgumentTypeError(f"invalid {what} '{value}'")
        return value

    return check


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(required=True)
    wpt_help = "path to the wpt CLI (crates.io: wpt)"

    p = commands.add_parser("store", help="store a run from wptreport files (JSON or JSON Lines, optionally .gz/.xz)")
    p.add_argument("--config", required=True, type=matching(CONFIG_RE, "configuration name"))
    p.add_argument("--date", required=True, type=matching(DATE_RE, "date"), help="YYYY-MM-DD")
    p.add_argument("--force", action="store_true", help="replace an existing run")
    p.add_argument("--wpt", default="wpt", help=wpt_help)
    p.add_argument("reports", nargs="+", type=Path)
    p.set_defaults(func=store)

    p = commands.add_parser("export", help="write a stored run as a wptreport")
    p.add_argument("run", help="<config>/<YYYY-MM-DD>")
    p.add_argument("-o", "--output")
    p.set_defaults(func=export)

    p = commands.add_parser("list", help="list the stored runs and the revision they tested")
    p.add_argument("config", nargs="?")
    p.set_defaults(func=list_runs)

    p = commands.add_parser("tested", help="exit with 0 if a run of the configuration tested the revision")
    p.add_argument("--config", required=True)
    p.add_argument("revision")
    p.set_defaults(func=tested)

    p = commands.add_parser("rescore", help="recompute the scores of stored runs, e.g. after a wpt update")
    p.add_argument("config", nargs="?")
    p.add_argument("--wpt", default="wpt", help=wpt_help)
    p.set_defaults(func=rescore)

    p = commands.add_parser("site", help="build the dashboard and its summary data")
    p.add_argument("--out", default="_site")
    p.set_defaults(func=site)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

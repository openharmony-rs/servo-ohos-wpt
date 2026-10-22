#!/usr/bin/env bash
# Usage: upstream_reports.sh <commit> <directory> [chunks]
#
# Downloads the wptreport files that the Linux WPT job of servo/servo's CI wrote for a commit
# into a directory. Needs `gh` with a token, as artifacts of public repositories cannot be
# downloaded anonymously; a fine-grained personal access token for any repository works.
set -euo pipefail

commit=$1
out=$2
chunks=${3:-20}

tmp=$(mktemp -d)
trap 'rm -rf "${tmp:?}"' EXIT

# The merge queue tested the commit before it landed, and the push to main tested it again.
runs=$(gh api "repos/servo/servo/actions/runs?head_sha=$commit&per_page=100" \
  --jq '[.workflow_runs[] | select(.name == "Main")] | sort_by(.event != "merge_group") | .[].id')
for run in $runs; do
  dir=$tmp/$run
  if ! gh run download "$run" --repo servo/servo --name wpt-full-logs-linux --dir "$dir"; then
    continue
  fi
  complete=true
  for chunk in $(seq 1 "$chunks"); do
    [ -s "$dir/linux/wptreport/$chunk.json" ] || complete=false
  done
  if [ "$complete" = true ]; then
    mkdir -p "$out"
    mv "$dir"/linux/wptreport/*.json "$out"/
    echo "Downloaded the WPT results of servo/servo run $run"
    exit 0
  fi
done
echo "No servo/servo run of $commit has the WPT results of all $chunks chunks" >&2
exit 1

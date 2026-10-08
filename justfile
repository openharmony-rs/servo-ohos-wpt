wpt := env("WPT", "wpt")

# List the recipes
default:
    @just --list

# Fetch all stored runs from origin
fetch:
    git fetch --no-tags origin '+refs/runs/*:refs/runs/*'

# List the stored runs and the revisions they tested, optionally of a single configuration
list config="":
    python3 scripts/wpt_store.py list {{ config }}

# Store wptreport files as the run <config>/<date>
store config date +reports:
    python3 scripts/wpt_store.py store --wpt {{ wpt }} --config {{ config }} --date {{ date }} {{ reports }}

# Recompute the scores of the stored runs, e.g. after updating the wpt CLI
rescore config="":
    python3 scripts/wpt_store.py rescore --wpt {{ wpt }} {{ config }}

# Write the stored run <config>/<date> as a wptreport
export run output:
    python3 scripts/wpt_store.py export {{ run }} -o {{ output }}

# Compare two stored runs, e.g. `just diff linux-spidermonkey/2026-10-08 linux-spidermonkey/2026-10-09 -v`
diff a b *args:
    #!/usr/bin/env bash
    set -euo pipefail
    tmp=$(mktemp -d)
    trap 'rm -rf "${tmp:?}"' EXIT
    python3 scripts/wpt_store.py export {{ a }} -o "$tmp/a.json"
    python3 scripts/wpt_store.py export {{ b }} -o "$tmp/b.json"
    {{ wpt }} diff "$tmp/a.json" "$tmp/b.json" {{ args }}

# Build the dashboard into _site
site:
    python3 scripts/wpt_store.py site --out _site

# Build the dashboard and serve it on http://localhost:8000
serve: site
    python3 -m http.server -d _site 8000

# Format the scripts
fmt:
    uvx ruff format scripts

# Lint the scripts and the workflows
lint:
    uvx ruff format --check scripts
    uvx ruff check scripts
    actionlint

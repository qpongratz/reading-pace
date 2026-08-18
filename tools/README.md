# Building the catalog

Turns Open Library's bulk dumps into `data/catalog.json` — the local book index
the planner searches. Optional: without it, search falls through to the
OpenLibrary API alone.

## Get a dump

Put it in `data/ol/`. Either shape works; the newest file of each kind is picked
automatically, so refreshing later means downloading a new dump and re-running
the same command.

```
mkdir -p data/ol && cd data/ol

# combined — editions, works and authors in one file
curl -L -C - -O https://openlibrary.org/data/ol_dump_latest.txt.gz

# or split, if you'd rather skip the 4 GB works dump
curl -L -C - -O https://openlibrary.org/data/ol_dump_editions_latest.txt.gz
curl -L -C - -O https://openlibrary.org/data/ol_dump_authors_latest.txt.gz

# either way, grab ratings — it's the recency signal and it's tiny
curl -L -C - -O https://openlibrary.org/data/ol_dump_ratings_latest.txt.gz
```

`-C -` makes them resumable. Dumps are served from Internet Archive, so this
costs Open Library nothing — which is the reason to use them rather than making
hundreds of thousands of API calls against a small nonprofit.

## Build

```
python3 tools/build_catalog.py
```

Stages run in order and skip anything already done, so an interrupted run picks
up where it stopped. `--force` redoes everything.

A stage counts as done only when it has written a `.done` marker recording its
output size. Checking for the output file alone is not enough: an interrupted
stage leaves a perfectly plausible partial behind — non-empty, valid, missing
most of the corpus — and resuming from it builds a catalog from a fraction of
the dump while reporting success.

| stage | what it does |
|---|---|
| `ratings` | work → rating count and latest rating year |
| `project` | stream the dump once, keep one short line per usable edition |
| `sort` | sort that file by work key, on disk |
| `group` | collapse each work's editions into one record |
| `emit` | select, resolve author names, write `catalog.json` |

Memory stays flat regardless of corpus size. Holding per-work state for millions
of works would run to several GB, so the pipeline projects, sorts on disk, and
groups adjacent runs instead.

## Tune without rebuilding

`emit` is separate and takes seconds. The expensive stages never repeat:

```
python3 tools/build_catalog.py --stage emit \
    --min-ratings 2 --min-editions 5 --since-year 2018 --limit 200000
```

A work is kept if **any** of these hold, so no single signal has to carry it:

- **`--min-ratings`** — any rating at all. Recency-neutral and current: 248k of
  Open Library's ratings landed in 2026, and 320k of 496k rated works have
  activity since 2024.
- **`--min-editions`** — catches classics and backlist, which accumulate
  editions over decades.
- **`--since-year`** — catches new releases that have neither yet. A 2025 debut
  with one edition and no ratings still gets in.

`--limit` caps the result, keeping the most-read books first.

## Refreshing later

Open Library publishes **full monthly dumps only** — no deltas. There's a
`recentchanges.json` API, but tracking millions of records through it would cost
more than a rebuild.

Incremental processing wouldn't help much anyway: the bottleneck is downloading
and streaming the file, not the compute. So refresh once or twice a year and let
the planner's live API fallback cover new releases in between — that's the real
incremental mechanism.

On rebuild, `emit` diffs against the previous catalog, reports what was added
and dropped, warns if more than 10% of the old catalog vanished, and keeps the
old file as `catalog.json.previous`. A silent rebuild that quietly halved the
catalog would be worse than a stale one.

## Making it faster

Measured: **110M rows in 12 minutes**, about 154k rows/sec, for a job that runs
twice a year. So none of this is worth a dependency or a rewrite — it is here
because the ordering is genuinely wrong, not because the speed is a problem.

The projection stage parses every record and then discards 86% of them.
Roughly in order of payoff:

- **Pre-filter on the raw line before `json.loads`.** A record with no
  `"number_of_pages"` substring, or without `/languages/eng`, can never survive.
  Byte scans are far cheaper than parsing, and most records are dropped, so this
  alone should be worth around 3×.
- **Decompress with `gzip -dc` via a subprocess.** Hands decompression to C and
  puts it on a different core from the parsing.
- **Optional `orjson`.** Several times faster, but it must stay a
  `try: import orjson` with a stdlib fallback — no-dependencies is worth more
  than the speed.
- **Parallel parsing.** A gzip stream can't be seeked, so decompression stays
  serial, but parsing could fan out to a process pool. Probably more complexity
  than it earns.

The first two need no dependencies and no structural change, and would probably
land the projection stage under five minutes. Worth doing only if someone is
already in this file.

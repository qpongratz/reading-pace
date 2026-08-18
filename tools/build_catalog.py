#!/usr/bin/env python3
"""Build a searchable book catalog from the Open Library bulk dumps.

    python3 tools/build_catalog.py                  # run everything, skip done work
    python3 tools/build_catalog.py --stage emit \
        --min-ratings 2 --min-editions 5 --limit 200000
    python3 tools/build_catalog.py --force          # redo from scratch

Drop a dump in data/ol/ and run it. Any of these shapes work — the newest file
of each kind is picked automatically, so refreshing means downloading a new dump
and running the same command:

    ol_dump_YYYY-MM-DD.txt.gz            the combined dump: editions + authors
    ol_dump_editions_*.txt.gz            editions only
    ol_dump_authors_*.txt.gz             author key -> name
    ol_dump_ratings_*.txt.gz             popularity signal (separate export)

Uses the dumps rather than the API on purpose: Open Library is a small nonprofit
and the dumps are served from Internet Archive, so a one-time download costs them
nothing while hundreds of thousands of API calls would not.

Memory stays flat no matter how big the corpus gets. Holding per-work state for
millions of works runs to several GB, so each edition is filtered and projected
to a short line, sorted by work key on disk, and grouped by reading adjacent
runs. Tuning the catalog afterwards only re-runs `emit`, which takes seconds —
the hour-long part never repeats.
"""

import argparse
import glob
import gzip
import json
import os
import statistics
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OL = os.path.join(ROOT, "data", "ol")
WORK = os.path.join(ROOT, "data", "build")

PROJECTED = os.path.join(WORK, "editions_projected.tsv")
SORTED = os.path.join(WORK, "editions_sorted.tsv")
AUTHOR_NAMES = os.path.join(WORK, "author_names.tsv")
GROUPED = os.path.join(WORK, "works_grouped.ndjson")
RATINGS_JSON = os.path.join(WORK, "ratings.json")
CATALOG = os.path.join(ROOT, "data", "catalog.json")
BUILT_FROM = None   # set once inputs are located, recorded in the output

# Formats whose page count means something other than "length of the prose".
SKIP_FORMATS = ("audio", "cassette", "cd", "microform", "microfilm", "microfiche",
                "vhs", "dvd", "video", "map", "globe", "poster", "kit", "braille")
MIN_PAGES, MAX_PAGES = 20, 5000
UNIT = "\x1f"          # separator that will never appear in a title


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def newest(*patterns):
    """Newest file matching any pattern, so a fresh dump wins automatically."""
    hits = [p for pat in patterns for p in glob.glob(os.path.join(OL, pat))]
    return max(hits, key=os.path.getmtime) if hits else None


def find_inputs():
    combined = newest("ol_dump_2*.txt.gz", "ol_dump_latest.txt.gz")
    editions = newest("ol_dump_editions_*.txt.gz")
    authors = newest("ol_dump_authors_*.txt.gz")
    ratings = newest("ol_dump_ratings_*.txt.gz")
    # The combined dump carries authors, so a separate authors file is optional.
    if not (combined or editions):
        sys.exit(f"no dump found in {OL}\n"
                 f"expected ol_dump_YYYY-MM-DD.txt.gz or ol_dump_editions_*.txt.gz\n"
                 f"see tools/README.md")
    return {"combined": combined, "editions": editions,
            "authors": authors, "ratings": ratings}


def done_marker(path):
    return path + ".done"


def mark_done(path):
    """Record that a stage finished. Written only after the output is closed."""
    with open(done_marker(path), "w") as f:
        f.write(str(os.path.getsize(path)))


def fresh(path, force):
    """True when a stage's output can be reused.

    Checks for a completion marker rather than just the file, because a stage
    that was interrupted leaves a perfectly plausible-looking partial file
    behind — non-empty, valid, and missing most of the corpus. Resuming from
    that would silently build a catalog from a fraction of the dump.
    """
    if force or not os.path.exists(path):
        return False
    marker = done_marker(path)
    if not os.path.exists(marker):
        log(f"  {os.path.basename(path)} is incomplete (no completion marker)"
            f" — redoing it")
        return False
    try:
        expected = int(open(marker).read().strip())
    except Exception:
        return False
    actual = os.path.getsize(path)
    if actual != expected:
        log(f"  {os.path.basename(path)} changed since it was written"
            f" ({actual:,} vs {expected:,}) — redoing it")
        return False
    return actual > 0


# ── ratings ──────────────────────────────────────────────────────────────────

def stage_ratings(inp, force):
    if fresh(RATINGS_JSON, force):
        log("  reusing ratings.json")
        return
    if not inp["ratings"]:
        log("  no ratings dump — popularity selection will fall back to editions")
        os.makedirs(WORK, exist_ok=True)
        json.dump({}, open(RATINGS_JSON, "w"))
        return
    counts, latest = Counter(), {}
    with gzip.open(inp["ratings"], "rt", encoding="utf8", errors="replace") as f:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 4 or not p[0].startswith("/works/"):
                continue
            key = p[0][7:]
            counts[key] += 1
            if p[3][:4] > latest.get(key, ""):
                latest[key] = p[3][:4]
    os.makedirs(WORK, exist_ok=True)
    with open(RATINGS_JSON, "w") as f:
        json.dump({k: [c, latest.get(k, "")] for k, c in counts.items()}, f)
    mark_done(RATINGS_JSON)
    log(f"  {sum(counts.values()):,} ratings over {len(counts):,} works")


# ── project ──────────────────────────────────────────────────────────────────

def ref_key(x):
    """Pull an Open Library key out of a reference.

    The dump is not uniform: a reference is usually {"key": "/authors/OL1A"} but
    is sometimes the bare string. Ninety million records in, both shapes appear,
    so never assume one.
    """
    if isinstance(x, dict):
        return str(x.get("key") or "")
    if isinstance(x, str):
        return x
    return ""


def first_ref(seq, prefix):
    for x in seq or []:
        k = ref_key(x)
        if k.startswith(prefix):
            return k[len(prefix):]
    return ""


def is_english(rec):
    return any(ref_key(l).endswith(("/eng", "/en"))
               for l in (rec.get("languages") or []))


def project_edition(rec):
    """One short line per usable edition, or None to drop it."""
    works = rec.get("works") or []
    if not works:
        return None, "no work"
    pages = rec.get("number_of_pages")
    if not isinstance(pages, int) or not (MIN_PAGES <= pages <= MAX_PAGES):
        return None, "no usable page count"
    if not is_english(rec):
        return None, "not english"
    fmt = str(rec.get("physical_format") or "").lower()
    if any(s in fmt for s in SKIP_FORMATS):
        return None, "non-book format"
    wkey = first_ref(works, "/works/")
    title = " ".join(str(rec.get("title") or "").split())[:160]
    if not wkey or not title:
        return None, "no title"

    akey = first_ref(rec.get("authors"), "/authors/")
    isbn = ""
    for field in ("isbn_13", "isbn_10"):
        v = rec.get(field) or []
        if isinstance(v, (list, tuple)) and v:
            isbn = str(v[0]).replace("-", "")[:13]
            break
        if isinstance(v, str) and v:
            isbn = v.replace("-", "")[:13]
            break
    year = ""
    for tok in str(rec.get("publish_date") or "").replace(",", " ").split():
        if len(tok) == 4 and tok.isdigit() and "1400" < tok < "2100":
            year = tok
            break
    subj = ";".join(str(s)[:40] for s in (rec.get("subjects") or [])[:4])
    return UNIT.join([wkey, str(pages), year, isbn, akey, title, subj]), None


def stage_project(inp, force):
    if fresh(PROJECTED, force) and fresh(AUTHOR_NAMES, force):
        log("  reusing projected editions")
        return
    src = inp["combined"] or inp["editions"]
    combined = src == inp["combined"]
    log(f"  reading {os.path.basename(src)}"
        f"{' (combined: editions + authors in one pass)' if combined else ''}")
    os.makedirs(WORK, exist_ok=True)

    seen = kept = auths = 0
    reasons = Counter()
    with gzip.open(src, "rt", encoding="utf8", errors="replace") as fin, \
            open(PROJECTED, "w", encoding="utf8") as fout, \
            open(AUTHOR_NAMES, "w", encoding="utf8") as faut:
        for line in fin:
            seen += 1
            if seen % 5_000_000 == 0:
                log(f"    {seen:,} rows, {kept:,} editions kept, {auths:,} authors")
            p = line.rstrip("\n").split("\t")
            if len(p) < 5:
                continue
            kind = p[0]

            # The combined dump carries authors too, so grab their names here
            # rather than making a second pass over 17 GB later.
            if combined and kind == "/type/author":
                try:
                    a = json.loads(p[4])
                    name = (a.get("name") or "") if isinstance(a, dict) else ""
                except Exception:
                    continue
                if name:
                    faut.write(f"{p[1][9:]}\t{name[:80]}\n")
                    auths += 1
                continue
            if combined and kind != "/type/edition":
                continue

            try:
                rec = json.loads(p[4])
                if not isinstance(rec, dict):
                    continue
                row, why = project_edition(rec)
            except Exception as e:
                # A single malformed record out of a hundred million should
                # never cost the whole pass. Count them; if the count is large
                # something structural is wrong and the summary will show it.
                reasons[f"malformed ({type(e).__name__})"] += 1
                continue
            if row is None:
                reasons[why] += 1
                continue
            fout.write(row + "\n")
            kept += 1

    mark_done(PROJECTED)
    mark_done(AUTHOR_NAMES)
    log(f"  {seen:,} rows read, {kept:,} editions projected, {auths:,} authors")
    for r, c in reasons.most_common():
        log(f"    dropped, {r}: {c:,}")


def stage_authors_split(inp, force):
    """Only needed when the authors dump is separate from the editions dump."""
    if inp["combined"] or fresh(AUTHOR_NAMES, force):
        return
    if not inp["authors"]:
        log("  no authors dump — catalog will have titles but no author names")
        return
    n = 0
    with gzip.open(inp["authors"], "rt", encoding="utf8", errors="replace") as f, \
            open(AUTHOR_NAMES, "w", encoding="utf8") as out:
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or not p[1].startswith("/authors/"):
                continue
            try:
                name = json.loads(p[4]).get("name") or ""
            except Exception:
                continue
            if name:
                out.write(f"{p[1][9:]}\t{name[:80]}\n")
                n += 1
    mark_done(AUTHOR_NAMES)
    log(f"  {n:,} author names")


# ── sort and group ───────────────────────────────────────────────────────────

def stage_sort(force):
    if fresh(SORTED, force):
        log("  reusing sorted editions")
        return
    log("  sorting on disk (this is what keeps memory flat)")
    subprocess.run(["sort", "-t", UNIT, "-k1,1", "-S", "40%",
                    "-T", WORK, "-o", SORTED, PROJECTED],
                   check=True, env=dict(os.environ, LC_ALL="C"))
    mark_done(SORTED)


def stage_group(force):
    if fresh(GROUPED, force):
        log("  reusing grouped works")
        return
    ratings = json.load(open(RATINGS_JSON)) if os.path.exists(RATINGS_JSON) else {}
    n = 0

    def flush(key, rows, out):
        nonlocal n
        if not key or not rows:
            return
        pages = sorted(r[0] for r in rows)
        years = [r[1] for r in rows if r[1]]
        isbns = [r[2] for r in rows if r[2]]
        akeys = [r[3] for r in rows if r[3]]
        titles = Counter(r[4] for r in rows)
        subj = Counter()
        for r in rows:
            for s in r[5].split(";"):
                if s:
                    subj[s] += 1
        rc, ry = ratings.get(key, [0, ""])
        out.write(json.dumps({
            "w": key,
            "t": titles.most_common(1)[0][0],
            "a": Counter(akeys).most_common(1)[0][0] if akeys else "",
            # median across editions: more robust than any single edition, and
            # what Open Library's own number_of_pages_median does
            "p": int(statistics.median(pages)),
            "y": min(years) if years else "",
            "i": isbns[0] if isbns else "",
            "e": len(rows), "r": rc, "ry": ry,
            "s": [s for s, _ in subj.most_common(3)],
        }, separators=(",", ":")) + "\n")
        n += 1

    with open(SORTED, encoding="utf8") as fin, \
            open(GROUPED, "w", encoding="utf8") as fout:
        cur, rows = None, []
        for line in fin:
            p = line.rstrip("\n").split(UNIT)
            if len(p) < 7:
                continue
            if p[0] != cur:
                flush(cur, rows, fout)
                cur, rows = p[0], []
            try:
                rows.append((int(p[1]), p[2], p[3], p[4], p[5], p[6]))
            except ValueError:
                continue
        flush(cur, rows, fout)
    mark_done(GROUPED)
    log(f"  {n:,} distinct works")


# ── emit ─────────────────────────────────────────────────────────────────────

def stage_emit(min_ratings, min_editions, since_year, limit):
    if not os.path.exists(GROUPED):
        sys.exit("run the earlier stages first")
    picked, dist = [], Counter()
    with open(GROUPED, encoding="utf8") as f:
        for line in f:
            r = json.loads(line)
            if r["r"] >= min_ratings:
                why = "rated"
            elif r["e"] >= min_editions:
                why = "editions"
            elif r["y"] and r["y"] >= str(since_year) and r["i"]:
                why = "recent"
            else:
                why = None
            dist[why or "dropped"] += 1
            if why:
                r["why"] = why
                picked.append(r)

    log("  selection:")
    for k, v in dist.most_common():
        log(f"    {k:<10} {v:>10,}")

    # rank so that a size cap keeps the books people actually read
    picked.sort(key=lambda r: (-(r["r"] * 10 + r["e"]), r["t"]))
    if limit and len(picked) > limit:
        log(f"  capping {len(picked):,} -> {limit:,}")
        picked = picked[:limit]

    names = {}
    if os.path.exists(AUTHOR_NAMES):
        want = {r["a"] for r in picked if r["a"]}
        with open(AUTHOR_NAMES, encoding="utf8") as f:
            for line in f:
                k, _, nm = line.rstrip("\n").partition("\t")
                if k in want:
                    names[k] = nm

    books = [{"title": r["t"], "author": names.get(r["a"]) or None,
              "pages": r["p"], "isbn": r["i"] or None,
              "year": r["y"] or None, "subjects": r["s"] or None}
             for r in picked]

    # Refreshing from a newer dump replaces the whole file, so say what moved.
    # A silent rebuild that quietly dropped half the catalog would be worse than
    # a stale one, and this is the only check that would catch it.
    if os.path.exists(CATALOG):
        try:
            old_books = json.load(open(CATALOG)).get("books", [])
            was = {(b.get("title"), b.get("author")) for b in old_books}
            now = {(b.get("title"), b.get("author")) for b in books}
            added, gone = now - was, was - now
            log(f"\n  vs previous catalog: {len(old_books):,} -> {len(books):,}"
                f"  (+{len(added):,} / -{len(gone):,})")
            if gone and len(gone) > len(was) * 0.10:
                log(f"  WARNING: {len(gone) / max(len(was), 1) * 100:.0f}% of the "
                    f"previous catalog is missing — check the thresholds before "
                    f"trusting this")
            for t, a in list(gone)[:5]:
                log(f"    dropped: {t} — {a}")
            os.replace(CATALOG, CATALOG + ".previous")
            log(f"  previous kept at {os.path.basename(CATALOG)}.previous")
        except Exception as e:
            log(f"  (could not diff previous catalog: {type(e).__name__})")

    json.dump({"books": books, "source": "openlibrary-dump", "count": len(books),
               "built_from": os.path.basename(BUILT_FROM or "")},
              open(CATALOG, "w"), separators=(",", ":"))

    size = os.path.getsize(CATALOG)
    log(f"\n  wrote {len(books):,} books -> {CATALOG}  ({size / 1e6:.1f} MB raw)")
    if books:
        log(f"  with author {sum(1 for b in books if b['author']) / len(books) * 100:.0f}%"
            f" · with isbn {sum(1 for b in books if b['isbn']) / len(books) * 100:.0f}%"
            f" · median {statistics.median(b['pages'] for b in books):.0f} pages")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="all",
                    choices=["all", "ratings", "project", "sort", "group", "emit"])
    ap.add_argument("--force", action="store_true", help="redo completed stages")
    ap.add_argument("--min-ratings", type=int, default=1)
    ap.add_argument("--min-editions", type=int, default=3)
    ap.add_argument("--since-year", type=int, default=2015)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap the catalog, keeping the most-read books")
    a = ap.parse_args()

    global BUILT_FROM
    inp = find_inputs()
    BUILT_FROM = inp["combined"] or inp["editions"]
    log("inputs:")
    for k, v in inp.items():
        if v:
            log(f"  {k:<9} {os.path.basename(v)}  "
                f"({os.path.getsize(v) / 1e9:.2f} GB)")

    todo = ["ratings", "project", "sort", "group", "emit"] \
        if a.stage == "all" else [a.stage]
    for s in todo:
        log(f"\n[{s}]")
        if s == "ratings":
            stage_ratings(inp, a.force)
        elif s == "project":
            stage_project(inp, a.force)
            stage_authors_split(inp, a.force)
        elif s == "sort":
            stage_sort(a.force)
        elif s == "group":
            stage_group(a.force)
        elif s == "emit":
            stage_emit(a.min_ratings, a.min_editions, a.since_year, a.limit)


if __name__ == "__main__":
    main()

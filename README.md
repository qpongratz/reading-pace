# reading-pace

Plan what you'll read next, and see when you'd actually finish it.

Add books to a queue, drag them around, and watch a calendar redraw. The point
is the "what if" — add the next Dungeon Crawler Carl and see how far it pushes
Sun Eater into next year.

It's a back-of-the-napkin visualiser, not a tracker. There are plenty of
trackers; this reads *from* them rather than replacing them.

---

## Running it

```
python3 src/serve.py        # → http://localhost:8420
```

Standard library only. No pip, no virtualenv, no build step — this should still
start in five years without a package manager having opinions about it.

It works immediately with nothing configured: type a book, give it a page count,
get a date. Everything below is optional sharpening.

The planner is heading toward being a **static page** running entirely in the
browser with `localStorage`, deployable to GitHub Pages. The local server exists
today only because it can reach OpenLibrary, which a published page cannot. See
`ROADMAP.md`.

---

## How the estimate works

```
days = words / (words-per-minute × 60 × hours-per-day)
```

Two terms, kept apart because they behave completely differently:

- **Reading speed** is fairly predictable. It varies by author and holds steady
  within one.
- **Hours per day** is not predictable at all, so the tool never guesses it.
  It's a slider you set.

Which matters more is worth knowing. Measured across one reader's 62 books,
author speed spanned **1.5×** while their own hours-per-day spanned **4.3×**.
The slider you drag by hand moves the answer about three times more than any
measured reading speed does. Precision in the speed term is a nicety; the pace
control is the foundation.

Speed resolves down a chain and always shows which link answered, so you can see
which estimates to trust:

| basis | when | confidence |
|---|---|---|
| series | 2+ books read in it | high |
| author | 1+ book read | medium |
| global | nothing to go on | low |

---

## Optional local data

Drop either of these in `data/` and the planner picks them up on restart. Both
are gitignored; neither is required.

**`catalog.json`** — books you can search, answered instantly and offline.
Without it, search falls through to OpenLibrary alone.

```json
{"books": [{"title": "Demon in White", "author": "Christopher Ruocchio",
            "series": "Sun Eater", "series_num": 3, "isbn": "9780756413033",
            "pages": 776, "percent_read": 0}]}
```

**`rates.json`** — calibration, so speeds come from your own reading instead of
a generic default.

```json
{"global_wpm": 207,
 "by_author": {"steven erikson": [185, 181]},
 "by_series": {"sun eater": [193, 204]},
 "daily": {"2026-08-16": {"hours": 2.93}}}
```

`daily` seeds the pace presets from your recent history. Anything absent falls
back to a default, so a partial file is fine.

[**kobo-reading-stats**](../kobo-reading-stats) generates both from a Kobo
e-reader. It's a separate project on purpose — a companion, never a requirement.

---

## Tests

```
python3 tests/test_plan.py
```

No test dependencies either. Covers the rate-resolution chain, length handling,
date arithmetic, percent-read, and throughput windows.

---

## Layout

```
src/
  serve.py             local server + JSON API
  plan.py              projection engine, rate resolution, throughput
  validate_palette.py  colour checks — CVD separation, contrast, lightness
web/
  index.html           the planner
tests/
  test_plan.py         projection engine
data/                  gitignored — your queue, catalog, rates
```

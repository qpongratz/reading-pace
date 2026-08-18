# Roadmap

Where this is going and why. Decisions here came out of building the first
version against real data; the reasoning matters more than the list.

## What it is

A back-of-the-napkin visualiser for a reading queue. Add books, shove them
around, watch months move. Not a tracker — there are plenty of those, and it
reads *from* them rather than replacing them.

## Decisions already settled

**Static page first, server second.** The planner should be a single page with
`localStorage`, deployable to GitHub Pages, working forever with no install. The
Python server exists only because it can reach OpenLibrary, which a published
page cannot. Two things block the static version: search needs a bundled
catalog, and `plan.py` needs porting to JavaScript. Until both are done there is
nothing for Pages to usefully host.

**Calibration is a companion project, not a dependency.** Measuring reading
speed from an e-reader is a genuinely interesting artifact on its own, but it
moves the estimate 1.5× while the pace slider moves it 4.3× — as a *required*
step it is a wall, as an optional one it is a flourish. So it lives in
[kobo-reading-stats](../kobo-reading-stats) and hands over two optional JSON
files, `rates.json` and `catalog.json`. No shared code, no imports across the
boundary, and the planner runs on defaults when neither exists.

**Words, not pages.** Page counts are an edition artifact; 365 words per print
page is the working conversion, calibrated against exact EPUB counts.

**Days out, hours in.** The output is always days and dates. Hours per day is an
input dial. Reading speed is internal plumbing and mostly stays hidden.

**Averages already handle skip days.** Hours-per-*calendar*-day includes the days
you read nothing. Layering a "I read 5 days a week" setting on top of that
double-counts. A per-weekday schedule is a genuinely different feature, and an
advanced one.

**Simple by default, advanced behind a disclosure.** Per-author rate overrides,
per-weekday schedules, and explicit parallel weights are all advanced. The
default interface should be addable books, one pace control, and a calendar.

**Statistics are opt-in, not ambient.** The build has drifted toward showing its
working by default, which reads as a dashboard rather than a planner. Someone
opening this wants to know *when they'll finish*, not their words-per-minute by
authorial basis. Everything numeric beyond the day count and the dates should
sit behind a single "details" switch, off by default and remembered.

Currently over-exposed, in rough order of how much it clutters:

| shown by default | should be |
|---|---|
| confidence chip on every row (`198 wpm · series`) | behind details |
| `59 books measured · 44 authors · 1,450 in library` header | behind details |
| pace presets labelled `30d · 1.16`, `90d · 1.10` | plain wording, numbers behind details |
| per-series/author totals under the charts | behind details |
| stats line — books, days, words, hours | days and finish date only |
| word counts in each row's subtitle | behind details |
| pace slider expressed in hours/day | a plainer phrasing; the number behind details |
| the working panel and the density dial | already correct — behind a tap |

The two things already behind a tap are the model for the rest. Nothing should
be deleted — the numbers are the reason to trust the estimate, and someone who
wants them should find them one switch away.

## Next

### 1. Calibrate from one remembered book
Nobody knows their words per minute. Everybody can answer *"think of a book you
finished recently — roughly how many days did it take?"* Pick it from the
catalog, say twelve days, and every downstream number is seeded. This is the
feature that makes the tracker genuinely optional.

### 2. Bundled catalog
Not because live lookups are impossible — OpenLibrary and Wikidata both send
`access-control-allow-origin: *`, so a page on GitHub Pages can query them
directly. The CSP restriction that blocks this applies to Artifacts, not Pages.

The reason to precompute is **speed and reliability**: 88% hit rate, ~4s a book,
one 20-second stall during testing. Harvest a seed list of fiction series and
authors through the search API — which already returns `number_of_pages_median`,
doing the edition-to-work aggregation for us — and ship the result. Roughly
50–100k books lands around 2–4 MB gzipped.

Processing the full dumps (`ol_dump_works` 4.0 GB, `ol_dump_editions` 12.5 GB)
would yield 1M+ books, which is far more than anyone queues. Not worth it.

Staleness is narrow but lands badly — a handful of new releases a year, and they
are exactly the books you most want to plan. The live fallback already in the
search UI covers that case: local results render first, OpenLibrary folds in
underneath when the index misses.

### 3. Mobile — mostly done
- ~~Reorder does not work on touch~~ — move up/down buttons, which also made it
  keyboard- and screen-reader-reachable. Drag stays as a desktop shortcut.
- ~~Everything explanatory is hover-only~~ — timeline blocks, lane labels and
  calendar days open a detail bar on tap.
- ~~The timeline drops its month ruler on narrow screens~~ — the ruler now spans
  the single column instead of being hidden.
- ~~Queue rows are a five-column grid~~ — they stack under 34rem.

Still open: **not verified on a real phone.** The layout is built for it but
nobody has held it yet. The calendar may still want to lead on small screens.

### 4. Explain by showing the arithmetic — partly done
Tapping a queue row now expands the sum behind its estimate: pages → words,
percentage already read, where the speed came from, and the division that
produced the day count.

Still open: the numbers in that panel are not yet **links to the settings behind
them**, and settings do not yet preview their effect as you change them. Also
missing: every default saying where it came from ("1.16 h/day — from *The
Hobbit* taking you 5 days").

### 5. Uncertainty bands
One hard date five months out is a lie told confidently. Ranges that widen down
the queue are more honest and barely more work.

### 6. Parallel reading
Reading three books at once splits the daily budget between them, so each takes
proportionally longer and the blocks overlap. The lane-per-series timeline
already renders this correctly. Simple form: select books, "reading together",
split evenly. Advanced form: explicit weights.

### 7. Pinned dates
Library due dates, book club, release dates. Show what collides.

### 8. Series progress — partly done
Per-series totals for what is queued now show beneath the charts: book count,
words remaining, days. Still missing the "2 of 7" part, which needs to know how
long a series actually is — that waits on the bundled catalog.

## Deferred

- Per-weekday schedules — a lot of interface for a small gain over an average.
- StoryGraph CSV import — useful, but the bundled catalog covers most of it.
- Kobo account/sync API — no public API exists; the unofficial one returns less
  than the local database already has, and page-level events never leave the
  device in retrievable form. Not worth building.
- Bundling the calibrator back in. It was a detour on the way here, and keeping
  it separate is what forced the planner to stand on its own.

**Series metadata is not worth chasing.** Measured against one reader's history,
knowing the series told us almost nothing the author didn't: 25 of 29 books had
an identical rate either way, only 3 of 44 authors had more than one series
measured, and the largest apparent difference turned out to be one series filed
under two names rather than real signal. Grouping and colour now fall back to
**author**, which every metadata source provides — unlike series, which
OpenLibrary returns empty even when asked for it and Wikidata only covers ~80%
of a real TBR after name normalisation. This drops Wikidata from the data
pipeline entirely and leaves the join a single-source problem.

**Density is a per-book input; grip is not.** Measured across 58 books, reading
speed and how much of the day a book takes are independent (r = +0.11), and it
is tempting to treat both as things you set per book. Only one is.

*Density* — words per minute the prose costs — is real and statable in advance.
Erikson measures 181 wpm against Dungeon Crawler Carl's ~300, and that gap holds
however long you sit with the book. History measures it per author; the
**per-book rate multiplier** covers authors with no history, or a book unusually
heavy for its author.

*Grip* is observable, not settable. You cannot plan to give one book fewer hours
a day — you set a daily budget and read whatever is current. A book that grips
you shows up as more hours that day, and the plan corrects itself the next time
progress is updated: bump the percentage, drop the finished book, everything
reflows. Modelling it in advance would be modelling something the user cannot
know and does not control.

This is also the general principle for the whole tool: **a plan is a projection
you re-check, not a simulation.** Deviations resolve by re-syncing, not by
adding terms.

StoryGraph's crowd "pace" tag was considered and dropped. It conflates the two
(a reader calling a book "slow" may mean thick prose, a crawling plot, or that
it took them four months), there is no API, and scraping a small indie company
is worse than scraping Open Library, not better.

## Open questions

- Do the author multipliers transfer between readers well enough to ship as
  defaults for someone with no history at all?
- Is `pages × 365` improvable without word counts? The factor is systematically
  per-author (Erikson's pages run 443 words, Egan's 324), so the calibration
  file could carry words-per-page alongside wpm wherever both are known.

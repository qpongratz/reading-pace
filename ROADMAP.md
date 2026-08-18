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

## Next

### 1. Calibrate from one remembered book
Nobody knows their words per minute. Everybody can answer *"think of a book you
finished recently — roughly how many days did it take?"* Pick it from the
catalog, say twelve days, and every downstream number is seeded. This is the
feature that makes the tracker genuinely optional.

### 2. Bundled catalog
OpenLibrary at runtime is the blocker for the static version, and it's flaky
anyway — 88% hit rate, ~4s a book, one 20-second stall during testing.
OpenLibrary publishes bulk dumps; distil popular fiction with page counts into a
compressed index and ship it. Search becomes instant, offline, and quota-free.
Refresh yearly.

### 3. Mobile
Currently broken in ways that matter more than any feature:

- **Reorder does not work at all.** HTML5 drag events don't fire on touch, and
  reordering is the core interaction. Replace with move up/down buttons — fixes
  touch, keyboard, and screen readers in one change; keep drag as a desktop
  accelerator.
- **Everything explanatory is hover-only.** Rate basis, full titles on narrow
  blocks, calendar day tooltips, truncated lane labels. Touch has no hover.
- **The timeline degrades badly** — the narrow-viewport rule drops the month
  ruler, leaving bars with no time axis. Worse than scrolling. The calendar
  should probably lead on small screens.
- **Queue rows are a six-column grid** and need to stack.

### 4. Explain by showing the arithmetic
Not help text — derivation. Every estimate expands to the sum that produced it:

> **Demon in White — 20 days**
> 776 pages → 283,240 words *(365 words/page — change)*
> ÷ 11,880 words/day = 198 wpm *(2 Sun Eater books)* × 1.16 h/day *(your pace)*

Every italic is a link to the setting behind it. Two supporting rules: each
setting previews its effect live as you change it, and every default says where
it came from ("1.16 h/day — from *The Hobbit* taking you 5 days").

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

### 8. Series progress
"2 of 7 through Sun Eater, 1.4M words remain."

## Deferred

- Per-weekday schedules — a lot of interface for a small gain over an average.
- StoryGraph CSV import — useful, but the bundled catalog covers most of it.
- Kobo account/sync API — no public API exists; the unofficial one returns less
  than the local database already has, and page-level events never leave the
  device in retrievable form. Not worth building.
- Bundling the calibrator back in. It was a detour on the way here, and keeping
  it separate is what forced the planner to stand on its own.

## Open questions

- Where does per-book difficulty come from for someone with no reading history?
  Crowd pace tags, genre, or prose features measured from an EPUB — all
  plausible, none tested.
- Do the author multipliers transfer between readers? If dense prose is slower
  for everyone by roughly the same factor, they ship as defaults. Untested with
  one reader's data.

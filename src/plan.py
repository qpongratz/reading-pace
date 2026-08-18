#!/usr/bin/env python3
"""Projection engine: how long a book will take, and when a queue finishes.

Two quantities, deliberately kept apart because they behave differently:

  speed      words per minute for this book. Predictable from what you have read
             before, and measured directly where Kobo instrumented the book.
  throughput hours per day you actually give to reading. Varies enormously and is
             not predictable from the data, so it is the user's dial, seeded with
             their own recent history rather than guessed at.

days = words / (speed * 60 * hours_per_day)

Speed is resolved down a chain of decreasing confidence — this series, then this
author, then everything — and which link answered is reported alongside the
number so the UI can show how much to trust it.
"""

import json
import os
import statistics
from collections import defaultdict
from datetime import date, timedelta

DATA = os.path.expanduser("~/projects/reading-pace/data")
RATES_FILE = os.path.join(DATA, "rates.json")

WORDS_PER_PRINT_PAGE = 365   # calibrated against exact EPUB word counts
MIN_FOR_SERIES = 2           # observations before a series rate beats the author
MIN_FOR_AUTHOR = 1
COMIC_WORDS_PER_PAGE = 80    # below this it is manga, not prose

# Used when nothing has been calibrated. Typical adult silent reading of fiction
# sits around here; it is a starting point to be corrected, not a claim.
DEFAULT_WPM = 250.0
DEFAULT_HOURS_PER_DAY = 1.0


def load_rates():
    """Optional calibration produced by a companion tool.

    Shape:
      {"global_wpm": 207,
       "by_author": {"steven erikson": [185, 181]},
       "by_series": {"sun eater": [193, 204]},
       "daily": {"2026-08-16": {"hours": 2.93}}}

    Absent is the normal case — the planner works on defaults, and the user's
    own pace setting dominates the estimate anyway.
    """
    if not os.path.exists(RATES_FILE):
        return {}
    with open(RATES_FILE) as f:
        return json.load(f)


def norm(s):
    return (s or "").strip().lower()


def first_author(a):
    return (a or "").split(",")[0].strip()


class Rates:
    """Reading speeds, resolved by series then author, then a global fallback.

    Built from a calibration file when one exists, and from defaults when it
    does not. Nothing here requires an e-reader — a user can override any of it
    by hand, which is the intended path for most people.
    """

    def __init__(self, calibration=None):
        cal = calibration or {}
        self.by_series = defaultdict(list, {
            norm(k): [float(x) for x in v]
            for k, v in (cal.get("by_series") or {}).items() if v})
        self.by_author = defaultdict(list, {
            norm(k): [float(x) for x in v]
            for k, v in (cal.get("by_author") or {}).items() if v})
        self.all = [w for v in self.by_author.values() for w in v]
        self.global_wpm = float(
            cal.get("global_wpm")
            or (statistics.median(self.all) if self.all else DEFAULT_WPM))
        self.calibrated = bool(self.all or cal.get("global_wpm"))

    def resolve(self, author=None, series=None):
        """Return (wpm, basis, n, spread) for the most specific match available."""
        s = self.by_series.get(norm(series), [])
        if len(s) >= MIN_FOR_SERIES:
            return self._pack(s, "series", series)
        a = self.by_author.get(norm(first_author(author)), [])
        if len(a) >= MIN_FOR_AUTHOR:
            return self._pack(a, "author", first_author(author))
        if s:
            return self._pack(s, "series", series)
        # global_wpm is authoritative when the calibration names one — the
        # observations still describe the spread, but must not silently
        # override the figure the file states.
        return self._pack(self.all or [self.global_wpm], "global",
                          "your overall pace" if self.calibrated
                          else "typical reading pace",
                          median=self.global_wpm)

    @staticmethod
    def _pack(vals, basis, label, median=None):
        med = statistics.median(vals) if median is None else float(median)
        lo, hi = (min(vals), max(vals)) if len(vals) > 1 else (med, med)
        return {"wpm": med, "basis": basis, "label": label,
                "n": len(vals), "low": min(lo, med), "high": max(hi, med),
                "confidence": {"series": "high", "author": "medium",
                               "global": "low"}[basis]}


MIN_MULTIPLIER, MAX_MULTIPLIER = 0.25, 4.0


def clamp_multiplier(v):
    """A per-book rate correction, bounded so a typo cannot produce a plan
    measured in decades or in minutes."""
    try:
        m = float(v)
    except (TypeError, ValueError):
        return 1.0
    if m <= 0:
        return 1.0
    return max(MIN_MULTIPLIER, min(MAX_MULTIPLIER, m))


def words_for(book):
    """Word count from whatever the book carries: words, then pages, else None."""
    if book.get("words"):
        return int(book["words"]), "given"
    if book.get("pages"):
        return int(book["pages"]) * WORDS_PER_PRINT_PAGE, "from pages"
    return None, "unknown"


def project(queue, hours_per_day, rates, start=None, start_fraction=None):
    """Lay a queue of books out on the calendar.

    Any book carrying `percent_read` is projected from where it actually is —
    Kobo tracks that per book, and a queue often opens with something already
    part-finished. `start_fraction` still overrides for the first book.
    """
    day = start or date.today()
    out = []
    for i, book in enumerate(queue):
        words, wsrc = words_for(book)
        rate = rates.resolve(book.get("author"), book.get("series"))
        if not words:
            out.append({**book, "error": "no length", "rate": rate})
            continue
        pct = book.get("percent_read") or 0
        done = pct / 100.0 if 0 < pct < 100 else 0.0
        if i == 0 and start_fraction:
            done = start_fraction
        remaining = words * (1.0 - done)

        # Per-book density correction: how many words an hour of reading covers.
        # Measured rates already capture this per author; the dial is for authors
        # with no history, or a book unusually heavy or light for its author.
        #
        # Not a "how gripping is it" control. You cannot plan to give one book
        # fewer hours a day — you set a daily budget and read whatever is
        # current. A gripping book shows up as more hours that day, and the plan
        # corrects itself when progress is next updated.
        mult = clamp_multiplier(book.get("rate_multiplier"))
        per_day = rate["wpm"] * 60 * hours_per_day * mult
        days = max(1, round(remaining / per_day))
        finish = day + timedelta(days=days - 1)
        out.append({**book, "words": words, "words_source": wsrc,
                    "percent_done": round(done * 100),
                    "remaining_words": round(remaining), "days": days,
                    "rate_multiplier": mult, "words_per_day": round(per_day),
                    "start": str(day), "end": str(finish), "rate": rate})
        day = finish + timedelta(days=1)
    scheduled = [b for b in out if b.get("end")]
    return {"books": out, "hours_per_day": hours_per_day,
            "finish": scheduled[-1]["end"] if scheduled else None,
            "unscheduled": [b["title"] for b in out if b.get("error")],
            "total_days": sum(b.get("days", 0) for b in out)}


def load_daily(calibration=None):
    cal = load_rates() if calibration is None else calibration
    return {date.fromisoformat(k): v for k, v in (cal.get("daily") or {}).items()}


def throughput(daily=None, today=None, windows=(30, 90, 180, 365)):
    """Hours per day over recent windows, from actual per-day page timings.

    Counts every day in the window, not just days with reading — the zeros are
    the point. Sideloaded books contribute here even though they yield no speed,
    which is a large part of why they are worth keeping.
    """
    daily = load_daily() if daily is None else daily
    if not daily:
        return {}
    today = today or max(daily)
    out = {}
    for w in windows:
        lo = today - timedelta(days=w)
        sel = {d: v for d, v in daily.items() if lo < d <= today}
        if not sel:
            continue
        span = min(w, (today - min(sel)).days + 1)
        hours = sum(v["hours"] for v in sel.values())
        out[w] = {"hours_per_day": round(hours / span, 3),
                  "total_hours": round(hours, 2),
                  "reading_days": len(sel), "span_days": span,
                  "share": round(len(sel) / span, 3)}
    return out


if __name__ == "__main__":
    cal = load_rates()
    rates = Rates(cal)
    print("calibration: " + (
        f"{len(rates.all)} measured books, global {rates.global_wpm:.0f} wpm, "
        f"{len(rates.by_series)} series, {len(rates.by_author)} authors"
        if rates.calibrated else
        f"none — using defaults ({rates.global_wpm:.0f} wpm)"))
    print()

    tp = throughput()
    print("throughput:")
    for w, v in sorted(tp.items()):
        print(f"  last {w:>3}d  {v['hours_per_day']:>5.2f} h/day  "
              f"{v['reading_days']:>3} reading days ({v['share'] * 100:.0f}%)")

    demo = [
        {"title": "The Crippled God", "author": "Steven Erikson",
         "series": "Malazan Book of the Fallen", "words": 378501},
        {"title": "Demon in White", "author": "Christopher Ruocchio",
         "series": "Sun Eater", "pages": 776},
        {"title": "Kingdoms of Death", "author": "Christopher Ruocchio",
         "series": "Sun Eater", "pages": 912},
        {"title": "The Eye of the Bedlam Bride", "author": "Matt Dinniman",
         "series": "Dungeon Crawler Carl", "pages": 762},
    ]
    hpd = tp.get(30, {}).get("hours_per_day", DEFAULT_HOURS_PER_DAY)
    plan = project(demo, hpd, rates, start=date(2026, 8, 17), start_fraction=0.23)
    print(f"\ndemo queue at {hpd} h/day:")
    for b in plan["books"]:
        r = b["rate"]
        print(f"  {b['title'][:34]:<35} {b['days']:>4}d  {b['start']} .. {b['end']}"
              f"   {r['wpm']:>3.0f} wpm ({r['basis']}, n={r['n']})")
    print(f"  finish {plan['finish']}  ({plan['total_days']} days)")

#!/usr/bin/env python3
"""Tests for the projection engine.

    python3 tests/test_plan.py

Standard library only, like everything else here — unittest, no pytest.
"""

import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import plan  # noqa: E402


CAL = {
    "global_wpm": 200.0,
    "by_author": {"steven erikson": [180.0, 190.0],
                  "china mieville": [160.0],
                  "brandon sanderson": [230.0, 240.0, 250.0]},
    "by_series": {"malazan book of the fallen": [180.0, 190.0],
                  "sun eater": [190.0, 210.0],
                  "one off": [300.0]},
}


class TestRateResolution(unittest.TestCase):
    def setUp(self):
        self.r = plan.Rates(CAL)

    def test_series_wins_when_it_has_enough_observations(self):
        got = self.r.resolve(author="Steven Erikson",
                             series="Malazan Book of the Fallen")
        self.assertEqual(got["basis"], "series")
        self.assertEqual(got["confidence"], "high")
        self.assertEqual(got["n"], 2)
        self.assertAlmostEqual(got["wpm"], 185.0)

    def test_falls_back_to_author_when_series_is_thin(self):
        # "one off" has a single observation, below MIN_FOR_SERIES
        got = self.r.resolve(author="Brandon Sanderson", series="One Off")
        self.assertEqual(got["basis"], "author")
        self.assertEqual(got["confidence"], "medium")
        self.assertAlmostEqual(got["wpm"], 240.0)

    def test_thin_series_still_beats_global_when_author_is_unknown(self):
        got = self.r.resolve(author="Nobody At All", series="One Off")
        self.assertEqual(got["basis"], "series")
        self.assertAlmostEqual(got["wpm"], 300.0)

    def test_global_when_nothing_matches(self):
        got = self.r.resolve(author="Someone New", series="Unseen Series")
        self.assertEqual(got["basis"], "global")
        self.assertEqual(got["confidence"], "low")

    def test_author_matching_ignores_case_and_extra_authors(self):
        got = self.r.resolve(author="  STEVEN ERIKSON, ed. someone else  ")
        self.assertEqual(got["basis"], "author")
        self.assertAlmostEqual(got["wpm"], 185.0)

    def test_spread_is_reported(self):
        got = self.r.resolve(author="Brandon Sanderson")
        self.assertEqual((got["low"], got["high"]), (230.0, 250.0))


class TestUncalibrated(unittest.TestCase):
    """The whole point of the split: it has to work with no calibration."""

    def setUp(self):
        self.r = plan.Rates({})

    def test_uses_default_speed(self):
        self.assertFalse(self.r.calibrated)
        self.assertEqual(self.r.global_wpm, plan.DEFAULT_WPM)

    def test_resolve_still_returns_a_usable_rate(self):
        got = self.r.resolve(author="Anyone", series="Anything")
        self.assertEqual(got["basis"], "global")
        self.assertEqual(got["wpm"], plan.DEFAULT_WPM)
        self.assertIn("typical", got["label"])

    def test_projection_works_with_no_history(self):
        out = plan.project([{"title": "X", "pages": 300}], 1.0, self.r,
                           start=date(2026, 1, 1))
        self.assertEqual(out["books"][0]["days"], 7)   # 109500w / 15000w per day


class TestLength(unittest.TestCase):
    def test_explicit_words_win_over_pages(self):
        self.assertEqual(plan.words_for({"words": 1000, "pages": 999}),
                         (1000, "given"))

    def test_pages_are_converted(self):
        w, src = plan.words_for({"pages": 100})
        self.assertEqual(w, 100 * plan.WORDS_PER_PRINT_PAGE)
        self.assertEqual(src, "from pages")

    def test_no_length_is_reported_not_guessed(self):
        self.assertEqual(plan.words_for({"title": "?"}), (None, "unknown"))


class TestProjection(unittest.TestCase):
    def setUp(self):
        self.r = plan.Rates(CAL)
        # 200 wpm x 60 x 1.0 h/day = 12,000 words/day
        self.hpd = 1.0

    def book(self, **kw):
        return dict({"title": "T", "words": 120000}, **kw)

    def test_days_and_dates(self):
        out = plan.project([self.book()], self.hpd, self.r, start=date(2026, 1, 1))
        b = out["books"][0]
        self.assertEqual(b["days"], 10)
        self.assertEqual(b["start"], "2026-01-01")
        self.assertEqual(b["end"], "2026-01-10")      # inclusive of the last day
        self.assertEqual(out["finish"], "2026-01-10")

    def test_books_run_back_to_back_without_overlapping(self):
        out = plan.project([self.book(), self.book(title="U")], self.hpd, self.r,
                           start=date(2026, 1, 1))
        first, second = out["books"]
        self.assertEqual(first["end"], "2026-01-10")
        self.assertEqual(second["start"], "2026-01-11")
        self.assertEqual(out["total_days"], 20)

    def test_percent_read_shortens_the_book(self):
        out = plan.project([self.book(percent_read=50)], self.hpd, self.r,
                           start=date(2026, 1, 1))
        b = out["books"][0]
        self.assertEqual(b["remaining_words"], 60000)
        self.assertEqual(b["percent_done"], 50)
        self.assertEqual(b["days"], 5)

    def test_percent_read_of_zero_or_a_hundred_is_ignored(self):
        for pct in (0, 100):
            b = plan.project([self.book(percent_read=pct)], self.hpd, self.r,
                             start=date(2026, 1, 1))["books"][0]
            self.assertEqual(b["remaining_words"], 120000, f"percent_read={pct}")

    def test_start_fraction_overrides_percent_on_the_first_book_only(self):
        out = plan.project([self.book(percent_read=10), self.book(percent_read=50)],
                           self.hpd, self.r, start=date(2026, 1, 1),
                           start_fraction=0.25)
        self.assertEqual(out["books"][0]["remaining_words"], 90000)
        self.assertEqual(out["books"][1]["remaining_words"], 60000)

    def test_book_without_a_length_is_flagged_but_does_not_stop_the_plan(self):
        out = plan.project([self.book(), {"title": "No length"}, self.book(title="C")],
                           self.hpd, self.r, start=date(2026, 1, 1))
        self.assertEqual(out["books"][1]["error"], "no length")
        self.assertEqual(out["unscheduled"], ["No length"])
        # the ones that can be scheduled still are, and the finish is the last
        # book that actually has an end date
        self.assertEqual(out["finish"], "2026-01-20")

    def test_a_very_short_book_still_takes_a_day(self):
        b = plan.project([{"title": "tiny", "words": 5}], self.hpd, self.r,
                         start=date(2026, 1, 1))["books"][0]
        self.assertEqual(b["days"], 1)

    def test_slower_pace_takes_longer(self):
        fast = plan.project([self.book()], 2.0, self.r, start=date(2026, 1, 1))
        slow = plan.project([self.book()], 0.5, self.r, start=date(2026, 1, 1))
        self.assertLess(fast["total_days"], slow["total_days"])
        # quartering the pace roughly quadruples the days; day counts are
        # rounded to whole days, so this is a ratio rather than an identity
        ratio = slow["total_days"] / fast["total_days"]
        self.assertAlmostEqual(ratio, 4.0, delta=0.3)

    def test_global_wpm_from_the_file_is_authoritative(self):
        """A stated global_wpm must not be overridden by the median of the
        per-author observations, which is a different number."""
        got = self.r.resolve(author="Unknown", series=None)
        self.assertEqual(got["basis"], "global")
        self.assertAlmostEqual(got["wpm"], CAL["global_wpm"])

    def test_empty_queue(self):
        out = plan.project([], self.hpd, self.r)
        self.assertEqual(out["books"], [])
        self.assertIsNone(out["finish"])
        self.assertEqual(out["total_days"], 0)

    def test_rate_basis_travels_with_each_book(self):
        out = plan.project(
            [self.book(author="Steven Erikson", series="Malazan Book of the Fallen"),
             self.book(author="Nobody")], self.hpd, self.r, start=date(2026, 1, 1))
        self.assertEqual(out["books"][0]["rate"]["basis"], "series")
        self.assertEqual(out["books"][1]["rate"]["basis"], "global")


class TestThroughput(unittest.TestCase):
    def test_counts_days_with_no_reading(self):
        daily = {date(2026, 1, 1): {"hours": 2.0},
                 date(2026, 1, 10): {"hours": 2.0}}
        # 4 hours across a 10-day span, not across the 2 days that had reading
        out = plan.throughput(daily, today=date(2026, 1, 10), windows=(30,))
        self.assertAlmostEqual(out[30]["hours_per_day"], 0.4)
        self.assertEqual(out[30]["reading_days"], 2)
        self.assertEqual(out[30]["span_days"], 10)

    def test_window_excludes_older_days(self):
        daily = {date(2025, 1, 1): {"hours": 100.0},
                 date(2026, 1, 10): {"hours": 1.0}}
        out = plan.throughput(daily, today=date(2026, 1, 10), windows=(30,))
        self.assertAlmostEqual(out[30]["total_hours"], 1.0)

    def test_no_data_is_empty_not_an_error(self):
        self.assertEqual(plan.throughput({}, today=date(2026, 1, 1)), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

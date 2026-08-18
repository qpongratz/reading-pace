#!/usr/bin/env python3
"""Local server for the reading planner.

Runs on your machine, so it can do the two things a published page cannot: read
the Kobo databases directly, and look books up on OpenLibrary without a content
security policy in the way.

    python3 src/serve.py          -> http://localhost:8420

Standard library only, deliberately: this should still start in five years
without a package manager having opinions about it.
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import plan as engine  # noqa: E402

ROOT = os.path.expanduser("~/projects/reading-pace")
DATA = os.path.join(ROOT, "data")
WEB = os.path.join(ROOT, "web")
QUEUE_FILE = os.path.join(DATA, "queue.json")
PORT = int(os.environ.get("READING_PACE_PORT", "8420"))
UA = {"User-Agent": "reading-pace/0.1 (personal reading planner)"}

_cache = {"library": None, "readings": None, "rates": None, "mtime": 0}


CATALOG_FILE = os.path.join(DATA, "catalog.json")


def library():
    """Local books available to search.

    Optional. Drop a catalog.json here — exported from a tracker, or eventually
    a bundled index of popular titles — and search answers instantly and
    offline. Without one, search falls through to OpenLibrary alone.

    Shape: [{"title", "author", "series", "series_num", "isbn",
             "pages", "percent_read"}]
    """
    if _cache["library"] is not None:
        return _cache["library"]
    out = []
    if os.path.exists(CATALOG_FILE):
        try:
            with open(CATALOG_FILE) as f:
                raw = json.load(f)
            for b in (raw.get("books") if isinstance(raw, dict) else raw) or []:
                if (b.get("title") or "").strip():
                    out.append(dict(b, source="library"))
        except Exception as e:
            print(f"  catalog.json unreadable: {e}", file=sys.stderr)
    _cache["library"] = out
    return out


def lengths():
    p = os.path.join(DATA, "lengths.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def rates():
    """Reload calibration if the file changed; defaults when there is none."""
    p = engine.RATES_FILE
    m = os.path.getmtime(p) if os.path.exists(p) else 0
    if _cache["rates"] is None or m != _cache["mtime"]:
        _cache["rates"] = engine.Rates(engine.load_rates())
        _cache["mtime"] = m
    return _cache["rates"]


def openlibrary(q):
    url = ("https://openlibrary.org/search.json?q=" + urllib.parse.quote(q) +
           "&fields=title,author_name,number_of_pages_median,first_publish_year,isbn"
           "&limit=8")
    try:
        # Short timeout: this tier is supplementary, and a stalled lookup used
        # to hold the whole search hostage for twenty seconds.
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=UA), timeout=6) as r:
            docs = json.load(r).get("docs", [])
    except Exception as e:
        return {"error": f"{type(e).__name__}", "results": []}
    out = []
    for d in docs:
        out.append({"title": d.get("title"),
                    "author": ", ".join((d.get("author_name") or [])[:2]) or None,
                    "series": None, "series_num": None,
                    "isbn": (d.get("isbn") or [None])[0],
                    "pages": d.get("number_of_pages_median"),
                    "year": d.get("first_publish_year"),
                    "source": "openlibrary"})
    return {"results": out}


def score(book, tokens, q):
    """Rank a library book against a query.

    Every token must appear somewhere, so multi-word queries like
    "ruocchio demon" work — a single raw substring match never finds those,
    because no one field contains both words.
    """
    title = (book.get("title") or "").lower()
    series = (book.get("series") or "").lower()
    author = (book.get("author") or "").lower()
    hay = f"{title} {series} {author}"
    if not all(t in hay for t in tokens):
        return None

    s = 0
    if title == q:
        s += 1000
    elif title.startswith(q):
        s += 500
    elif q in title:
        s += 250
    # A full-query hit on the series or author beats an incidental word match in
    # some unrelated title: searching "sun eater" wants the Sun Eater books, not
    # every title containing "eater".
    if q and series:
        s += 600 if series == q else (400 if q in series else 0)
    if q and q in author:
        s += 350
    for t in tokens:
        if title.startswith(t):
            s += 60
        elif re.search(r"\b" + re.escape(t), title):
            s += 40
        elif t in title:
            s += 20
        if t in series:
            s += 25
        if t in author:
            s += 20
    if book.get("read_status") == 0:      # unread: likelier to be planned
        s += 15
    if book.get("series_num"):
        s += 5
    s -= min(len(title) // 25, 4)          # nudge shorter titles up
    return s


def search_local(q):
    """Instant: scans the in-memory library, never touches the network."""
    ql = " ".join(q.lower().split())
    tokens = [t for t in ql.split() if t]
    if not tokens:
        return []
    L = lengths()
    hits = []
    for b in library():
        sc = score(b, tokens, ql)
        if sc is None:
            continue
        rec = dict(b, score=sc)
        cached = L.get((b.get("title") or "").strip().lower())
        if cached and cached.get("pages"):
            rec["pages"] = cached["pages"]
        hits.append(rec)
    hits.sort(key=lambda r: -r["score"])
    return hits[:25]


def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE) as f:
            return json.load(f)
    return {"books": [], "hours_per_day": None, "start_fraction": 0}


def save_queue(q):
    with open(QUEUE_FILE, "w") as f:
        json.dump(q, f, indent=1)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"  {self.address_string()} {fmt % args}\n")

    def _send(self, obj, code=200, ctype="application/json"):
        body = (json.dumps(obj) if ctype == "application/json" else obj)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            p = os.path.join(WEB, "index.html")
            if not os.path.exists(p):
                return self._send("planner UI not built yet", 404, "text/plain")
            return self._send(open(p, encoding="utf-8").read(), 200, "text/html")

        if u.path == "/api/state":
            R = rates()
            tp = engine.throughput()
            by_author = {a: sorted(v) for a, v in R.by_author.items() if len(v) >= 1}
            by_series = {s: sorted(v) for s, v in R.by_series.items() if len(v) >= 1}
            return self._send({
                "throughput": {str(k): v for k, v in tp.items()},
                "global_wpm": R.global_wpm,
                "calibrated": R.calibrated,
                "n_measured": len(R.all),
                "by_author": by_author,
                "by_series": by_series,
                "library_size": len(library()),
                "queue": load_queue(),
                "words_per_page": engine.WORDS_PER_PRINT_PAGE,
            })

        # Two endpoints on purpose. Local returns in microseconds; OpenLibrary
        # takes seconds and sometimes stalls. Folding them into one request made
        # every thin-result query feel broken, so the client renders local hits
        # immediately and folds web results in when they arrive.
        if u.path == "/api/search":
            return self._send({"results": search_local(qs.get("q", [""])[0])})

        if u.path == "/api/search/web":
            return self._send(openlibrary(qs.get("q", [""])[0]))

        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send({"error": "bad json"}, 400)

        if u.path == "/api/plan":
            R = rates()
            tp = engine.throughput()
            hpd = payload.get("hours_per_day")
            if not hpd:
                hpd = tp.get(30, {}).get("hours_per_day") or 1.0
            start = payload.get("start")
            from datetime import date
            result = engine.project(
                payload.get("books", []), float(hpd), R,
                start=date.fromisoformat(start) if start else None,
                start_fraction=payload.get("start_fraction") or 0)
            return self._send(result)

        if u.path == "/api/queue":
            save_queue(payload)
            return self._send({"ok": True})

        return self._send({"error": "not found"}, 404)


def main():
    os.makedirs(WEB, exist_ok=True)
    R = rates()
    tp = engine.throughput()
    print(f"reading-pace  ->  http://localhost:{PORT}")
    print("  rates: " + (
        f"calibrated from {len(R.all)} books, {len(R.by_author)} authors, "
        f"{len(R.by_series)} series"
        if R.calibrated else f"defaults ({R.global_wpm:.0f} wpm) — "
        "drop a rates.json in data/ to calibrate"))
    if tp.get(30):
        print(f"  recent pace {tp[30]['hours_per_day']:.2f} h/day "
              f"({tp[30]['reading_days']}/{tp[30]['span_days']} days)")
    n = len(library())
    print(f"  catalog: {n:,} books searchable" if n
          else "  catalog: none — search uses OpenLibrary only")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    try:
        webbrowser.open(f"http://localhost:{PORT}")
    except Exception:
        pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

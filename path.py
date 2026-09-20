"""Sample every 15-minute market's price every 30 seconds, all the way through.

WHY THIS EXISTS, AND WHY IT IS A DIFFERENT QUESTION FROM EVERYTHING BEFORE IT.
v6 and v7 both asked who wins at SETTLEMENT. That question is answered by a
well calibrated book, and neither rule beat the price. This asks about the
PATH: a 15-minute market that lurches away from its strike in the first
minutes prices the far side cheap, and the question is whether it comes back
often enough, and far enough, to sell into at a profit before the window ends.

That is a bet about the journey, not the destination. Nothing collected so
far can answer it. The Mid Window tab holds two snapshots (+5 and +10
minutes) and a take-profit fires the INSTANT price touches a level, so two
samples miss most touches and would understate any such strategy badly.

So: 28 samples per market per window, every 30 seconds. At that spacing a
level that is touched for a minute is almost certainly seen; one touched for
ten seconds is still missed, and any result here is therefore a FLOOR on how
often a take-profit would have triggered, never an overstatement.

WHAT IS STORED. One row per market per window, with the bid and ask series
compressed into two strings. One row per sample would be 140 rows a window
and would bury the sheet inside a week; this is five.

The ASK series is what an entry costs and the BID series is what an exit
pays. Both are kept because using a mid price for either would invent an edge
out of half a spread -- which, on contracts this cheap, is most of the
supposed profit.

EVERY market is sampled, not only the ones a rule fired on. The setup being
tested is defined by the price path, so filtering to v6's signals would
inherit v6's selection and answer a narrower question than the one asked.

Read-only. Places nothing.
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

SHEET = "Path"
HEADERS = [
    "Timestamp", "Symbol", "Ticker", "Strike", "Spot at Open",
    "First Offset s", "Step s", "Samples", "Yes Bids", "Yes Asks", "Note",
    # HOW MANY, not just how much. A price alone cannot say whether a bounce
    # was tradeable: 90c with 40 contracts behind it is a real exit, 90c with
    # 2 contracts is a headline you cannot sell into. The backtest sells
    # 30-plus contracts a trade and, without these, silently assumes they all
    # fill at the top of the book.
    #
    # APPENDED AFTER "Note", not slotted in beside the prices where they
    # belong, because 235 rows were already written to this tab. Inserting a
    # column mid-table would leave every one of them with its Note sitting
    # under a size heading -- historical data relabelled rather than extended.
    "Yes Bid Sz", "Yes Ask Sz",
]

# Every 30 seconds from +30s to +13:30. The last 90 seconds are deliberately
# left out: the book goes erratic into the close, and a fill there is not
# something a strategy should be credited with.
FIRST = 30
STEP = 30
COUNT = 27

PRICE_HOST = "https://api.elections.kalshi.com"


def enabled():
    return os.environ.get("MEASURE_PATH", "").strip() == "1"


def _depth(ticker):
    """(contracts at the best YES bid, contracts at the best YES ask).

    Read from the order book, where a YES ask is the mirror of a NO bid -- so
    selling a YES position eats the YES bid, and selling a NO position eats
    the NO bid, which is this market's YES ask side. Both numbers are kept
    because the strategy trades whichever side came up cheap.

    Deliberately a SEPARATE call from the price read, and deliberately
    fail-soft: prices are the data that already works, and a shape surprise
    or a rate limit here must cost a blank size column, never a window of
    collection.
    """
    import predictor as P
    try:
        path = f"/trade-api/v2/markets/{ticker}/orderbook"
        hdrs = P._kalshi_headers("GET", path) or {}
        r = requests.get(f"{PRICE_HOST}{path}", params={"depth": 1},
                         headers=hdrs, timeout=8)
        if not r.ok:
            return None, None
        ob = (r.json() or {}).get("orderbook") or {}
    except Exception:
        return None, None

    def top(side):
        # Kalshi returns each side as [[price, count], ...] ascending, so the
        # best bid is the LAST entry. The key has been spelled both ways
        # across versions; try the plain one first and fall back.
        lv = ob.get(side) or ob.get(f"{side}_dollars")
        if not isinstance(lv, list) or not lv:
            return None
        best = lv[-1]
        if not isinstance(best, (list, tuple)) or len(best) < 2:
            return None
        try:
            return float(best[1])
        except (TypeError, ValueError):
            return None

    # "yes" is the YES bid side; "no" is the NO bid side, which is the same
    # resting interest a YES buyer lifts -- i.e. the size at the YES ask.
    return top("yes"), top("no")


def _book(ticker):
    """(yes_bid_cents, yes_ask_cents) or (None, None)."""
    import predictor as P
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}") or {}
        r = requests.get(f"{PRICE_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs, timeout=8)
        if not r.ok:
            return None, None
        m = r.json().get("market") or {}
    except Exception:
        return None, None

    def c(k):
        try:
            return round(float(m[k]) * 100.0, 1)
        except (KeyError, TypeError, ValueError):
            return None

    return c("yes_bid_dollars"), c("yes_ask_dollars")


def markets_for(boundary):
    """This window's market per coin: (symbol, ticker, strike).

    Matched on close time rather than looked up by price, because at the top
    of a window the market exists but its book is a placeholder, and a
    price-validating lookup reports nothing at exactly the wrong moment.
    """
    import predictor as P
    close = boundary + timedelta(minutes=15)
    want = f"{close:%Y-%m-%dT%H:%M}"
    out = []
    for symbol, series in P.KALSHI_SERIES.items():
        try:
            hdrs = P._kalshi_headers("GET", "/trade-api/v2/markets") or {}
            r = requests.get(f"{PRICE_HOST}/trade-api/v2/markets",
                             params={"series_ticker": series, "limit": 200},
                             headers=hdrs, timeout=10)
            if not r.ok:
                continue
            for mk in r.json().get("markets", []):
                if str(mk.get("close_time", ""))[:16] == want:
                    strike = (mk.get("floor_strike") or mk.get("cap_strike")
                              or mk.get("strike_price") or "")
                    out.append((symbol, mk["ticker"], strike))
                    break
        except Exception:
            continue
    return out


class _Sampler(threading.Thread):
    """Walks the window sampling every market, on its own thread.

    WHY A THREAD. The first eight windows all started at +90 seconds, never
    +30, because the sampler could not begin until the runner had booted, pip
    had installed and the predictions had been written. A whole minute was
    missing from the front of every path -- and the front is the part this
    data exists to study, since the setup being tested is a market that has
    already lurched away from its strike in the first minutes.

    So sampling starts immediately and the rest of the job runs alongside it.
    The thread only COLLECTS; the sheet write happens on the main thread at
    the end, because two threads writing to one gspread client is a race
    nobody needs.
    """

    daemon = True

    def __init__(self, boundary):
        super().__init__(name="path-sampler")
        self.boundary = boundary
        self.mk = []
        self.bids = {}
        self.asks = {}
        self.sched = []
        self.error = ""

    def run(self):
        try:
            self.mk = markets_for(self.boundary)
            if not self.mk:
                self.error = "no markets resolved"
                return
            self.bids = {t: [] for _, t, _ in self.mk}
            self.asks = {t: [] for _, t, _ in self.mk}
            self.bsz = {t: [] for _, t, _ in self.mk}
            self.asz = {t: [] for _, t, _ in self.mk}

            # Only offsets still AHEAD. A late start cannot go back and price
            # +30s, and appending whatever it finds would write a series that
            # claims to start at +30s while really starting minutes later --
            # every later analysis would then read those prices at the wrong
            # point in the window.
            now = (datetime.now(timezone.utc) - self.boundary).total_seconds()
            want = [FIRST + i * STEP for i in range(COUNT)]
            self.sched = [t for t in want if t > now - 5]
            if not self.sched:
                self.error = "window already past the last sample point"
                return

            for target in self.sched:
                wait = target - (datetime.now(timezone.utc) - self.boundary).total_seconds()
                if wait > 0:
                    time.sleep(min(wait, STEP + 5))
                for _, ticker, _ in self.mk:
                    b, a = _book(ticker)
                    self.bids[ticker].append("" if b is None else b)
                    self.asks[ticker].append("" if a is None else a)
                    bs, as_ = _depth(ticker)
                    self.bsz[ticker].append("" if bs is None else bs)
                    self.asz[ticker].append("" if as_ is None else as_)
        except Exception as e:                      # never take the job down
            self.error = str(e)


def start(boundary):
    """Begin sampling now. Returns the handle to hand back to finish()."""
    s = _Sampler(boundary)
    s.start()
    return s


def finish(client, sampler, spots=None):
    """Wait for the walk to end, then write one row per market."""
    if sampler is None:
        return
    # The walk ends at +13:30 by construction; the timeout is only a backstop
    # against a hung request, and is generous enough never to truncate a
    # healthy run.
    sampler.join(timeout=16 * 60)
    if sampler.is_alive():
        print("  [path] sampler did not finish; not logging a partial series")
        return
    if sampler.error:
        print(f"  [path] {sampler.error}")
        return
    if not sampler.mk or not sampler.sched:
        return

    spots = spots or {}
    rows = []
    for symbol, ticker, strike in sampler.mk:
        asks = sampler.asks[ticker]
        got = sum(1 for v in asks if v != "")
        rows.append([
            sampler.boundary.strftime("%Y-%m-%d %H:%M UTC"), symbol, ticker, strike,
            spots.get(symbol, ""), sampler.sched[0], STEP, got,
            ",".join(str(v) for v in sampler.bids[ticker]),
            ",".join(str(v) for v in asks),
            "" if got == len(sampler.sched)
            else f"{len(sampler.sched) - got} sample(s) unpriced",
            ",".join(str(v) for v in sampler.bsz.get(ticker, [])),
            ",".join(str(v) for v in sampler.asz.get(ticker, [])),
        ])
    sized = sum(1 for r in rows for v in str(r[11]).split(",") if v not in ("", "None"))
    print(f"  [path] {len(rows)} market(s) from +{sampler.sched[0]:.0f}s, "
          f"{sum(r[7] for r in rows)}/{len(sampler.sched) * len(rows)} samples priced, "
          f"{sized} with depth")

    import predictor as P
    try:
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
            # The tab predates the size columns; widen its header once so the
            # new values are labelled instead of trailing off the end nameless.
            try:
                have = ws.row_values(1)
                if len(have) < len(HEADERS):
                    ws.update("A1", [HEADERS])
            except Exception:
                pass
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=8000, cols=len(HEADERS))
            ws.update("A1", [HEADERS])
        ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
        print(f"  [path] logged {len(rows)} row(s).")
    except Exception as e:
        print(f"  [path] could not write tab: {e}")

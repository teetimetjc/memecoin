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
import time
from datetime import datetime, timedelta, timezone

import requests

SHEET = "Path"
HEADERS = [
    "Timestamp", "Symbol", "Ticker", "Strike", "Spot at Open",
    "First Offset s", "Step s", "Samples", "Yes Bids", "Yes Asks", "Note",
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


def measure(client, boundary, spots=None):
    """Walk the window, sampling every market every STEP seconds, then log."""
    mk = markets_for(boundary)
    if not mk:
        print("  [path] no markets resolved for this window")
        return

    spots = spots or {}
    bids = {t: [] for _, t, _ in mk}
    asks = {t: [] for _, t, _ in mk}

    # Only sample the offsets that are still AHEAD. A run dispatched mid-window
    # cannot go back and price +30s, and appending whatever it finds would
    # write a series that claims to start at +30s when it really starts at
    # +7min -- every later analysis would read those prices at the wrong point
    # in the window. So late starts produce a shorter, correctly labelled
    # series, and the offset actually used is stored rather than assumed.
    now = (datetime.now(timezone.utc) - boundary).total_seconds()
    sched = [FIRST + i * STEP for i in range(COUNT)]
    sched = [t for t in sched if t > now - 5]
    if not sched:
        print("  [path] window already past the last sample point; nothing to do")
        return
    if sched[0] != FIRST:
        print(f"  [path] started late: first sample at +{sched[0]}s, "
              f"{len(sched)} of {COUNT} points")

    for target in sched:
        wait = target - (datetime.now(timezone.utc) - boundary).total_seconds()
        if wait > 0:
            time.sleep(min(wait, STEP + 5))
        for _, ticker, _ in mk:
            b, a = _book(ticker)
            bids[ticker].append("" if b is None else b)
            asks[ticker].append("" if a is None else a)

    rows = []
    for symbol, ticker, strike in mk:
        got = sum(1 for v in asks[ticker] if v != "")
        rows.append([
            boundary.strftime("%Y-%m-%d %H:%M UTC"), symbol, ticker, strike,
            spots.get(symbol, ""), sched[0], STEP, got,
            ",".join(str(v) for v in bids[ticker]),
            ",".join(str(v) for v in asks[ticker]),
            "" if got == len(sched) else f"{len(sched) - got} sample(s) unpriced",
        ])
    print(f"  [path] {len(rows)} market(s), "
          f"{sum(r[7] for r in rows)}/{len(sched) * len(rows)} samples priced")

    import predictor as P
    try:
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=8000, cols=len(HEADERS))
            ws.update("A1", [HEADERS])
        ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
        print(f"  [path] logged {len(rows)} row(s).")
    except Exception as e:
        print(f"  [path] could not write tab: {e}")

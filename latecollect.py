"""Record the real order book at T-3, T-2 and T-1. Places nothing.

WHY THIS EXISTS SEPARATELY FROM THE BACKTEST. lateentry.py can say whether a
95c favourite with a minute left is underpriced, because candles carry the
quote. It cannot say whether you could BUY it: a quote is not a promise that
anyone will sell you six contracts at it. The live record already shows
partial fills at T-9, where the book is deeper and there is more time for a
seller to appear. At T-1 it is likely thinner, and that -- not the
calibration -- is what decides whether this idea is tradeable.

So this samples the actual orderbook for markets in the band and writes
down what was there: the ask, the size at the ask, the spread, and how many
contracts a $6 order would have needed to sweep. The settlement is joined
later from the exchange.

THREE OFFSETS, NOT ONE, from 2026-10-21. The backtest sweep put T-3 in the
90-94c band at +3.22% net ROI against +0.12% at the T-9 the bot actually
runs -- on 319 markets. T-2 was +2.58%. Those are QUOTES, and the whole
reason this file exists is that a quote at that range is not a promise
anyone will sell you six contracts. Sampling only T-1 would measure the
depth of the one offset the backtest likes least.

So each market is sampled once in each of three windows and the offset is
recorded beside the reading, which makes depth comparable ACROSS offsets on
the same markets -- the comparison that decides whether a later entry is
reachable or merely cheaper on paper.

IT NEVER PLACES AN ORDER. It imports no ordering code, runs in its own
workflow with its own concurrency group, and shares nothing with the live
bot but the read-only market endpoints.

READ IT WITH THE BACKTEST, not instead of it. If the price is fair, depth
does not matter; if the price is wrong but the book holds two contracts,
the edge is unreachable at any stake that matters.
"""
import collections
import os
import sys
import time

import predictor as P
import backfill as B
import collect15 as C
import openmk
import live
import shadow

SHEET = "Late Entry"
HEADERS = [
    "Sampled UTC", "Ticker", "Series", "Secs to close", "Yes ask ¢",
    "Yes bid ¢", "Spread ¢", "Size at ask", "Levels for 6 contracts",
    "Enough for $6", "Note",
    # APPENDED ON THE RIGHT, never inserted. Every row already written has
    # eleven columns, and inserting a column in the middle would silently
    # shift every historical reading one place over.
    "Offset",
]
BAND = (90.0, 97.0)
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3300
POLL = 15
WANT_CONTRACTS = 6
MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")

# (label, low_seconds, high_seconds). A market is sampled at most once per
# window, so one market contributes up to three rows over its last minutes
# and the three are directly comparable.
#
# The windows are wide enough that the 15-second poll cannot miss one, and
# they do not overlap, so a reading cannot be filed under two offsets.
WINDOWS = (("T-1", 25, 89), ("T-2", 90, 149), ("T-3", 150, 215))


def window_for(left):
    for lab, lo, hi in WINDOWS:
        if lo <= left <= hi:
            return lab
    return None


# WHY EVERY REJECTION IS COUNTED. This collector reported "wrote 0" five
# times and each zero was indistinguishable from a quiet market, so the
# first fix was aimed at a guess. longrun.py learned the same lesson the
# same way and the rule it produced applies here: when a filter chain can
# silently empty itself, print the distribution UNCONDITIONALLY so a run
# with no survivors says which stage ate them.
TALLY = collections.Counter()


def rows_for_now(seen):
    """Sample any in-band market inside a T-3/T-2/T-1 window, once each."""
    out = []
    # ONE CALL PER SERIES. The unfiltered version of this returned an
    # arbitrary page of 200 open markets out of thousands, never containing
    # a 15-minute crypto market, and this collector wrote zero rows across
    # four runs while reporting success. See openmk.py.
    markets = openmk.open_markets(MAJORS)
    now = time.time()
    TALLY["polls"] += 1
    TALLY["markets seen"] += len(markets)
    for m in markets:
        tk = str(m.get("ticker") or "")
        if not tk.startswith(MAJORS):
            TALLY["not a major series"] += 1
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            TALLY["no close time"] += 1
            continue
        left = close - now
        off = window_for(left)
        # Keyed by (ticker, offset): the same market is wanted once in each
        # window, and only once in each.
        if off is None:
            TALLY["outside every sampling window"] += 1
            continue
        TALLY["inside a window"] += 1
        if (tk, off) in seen:
            TALLY["already sampled at this offset"] += 1
            continue
        q = C.quote_from_market(m)
        if not q or q.get("ask") is None:
            TALLY["no readable ask"] += 1
            continue
        ask_c = 100.0 * q["ask"]
        if not (BAND[0] <= ask_c <= BAND[1]):
            TALLY["ask outside the band"] += 1
            # THE PRICE ITSELF, not just the fact of rejection. "nothing in
            # band" reads the same whether the market was quoted at 62c or
            # the quote came back empty, and those need different responses.
            print(f"  [skip] {tk.split('-')[0]:<10} {off} {int(left):>3}s "
                  f"ask {ask_c:.0f}c outside {BAND[0]:.0f}-{BAND[1]:.0f}c")
            # THE RAW FIELDS, ONCE. Five coins cannot all have a worthless
            # YES side at the same moment, repeatedly, so a 0c ask is a
            # misread rather than a price -- and which field is being
            # misread cannot be deduced from the derived number. Printed for
            # the first few skips only, so the log stays readable.
            if TALLY["raw dumps"] < 6:
                TALLY["raw dumps"] += 1
                keys = sorted(k for k in m
                              if any(w in k for w in
                                     ("price", "bid", "ask", "last",
                                      "volume", "interest", "status")))
                print("    [raw] " + tk)
                print("    [raw] " + ", ".join(
                    f"{k}={m.get(k)!r}" for k in keys))
            continue
        TALLY["sampled"] += 1
        ob = shadow.orderbook(tk)
        bid_c, book_ask = shadow._best(ob, "UP") if ob else (None, None)
        levels = shadow._levels(ob, "UP") if ob else []
        size = levels[0][1] if levels else None
        need, lv = float(WANT_CONTRACTS), 0
        for _p, sz in levels:
            if need <= 0:
                break
            need -= sz
            lv += 1
        enough = "yes" if (need <= 0 and lv == 1) else (
            "sweeps %d levels" % lv if lv else "no book")
        seen.add((tk, off))
        out.append([
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            tk, tk.split("-")[0], int(left),
            round(book_ask if book_ask is not None else ask_c, 1),
            round(bid_c, 1) if bid_c is not None else "",
            round(book_ask - bid_c, 1)
            if (book_ask is not None and bid_c is not None) else "",
            size if size is not None else "",
            lv, enough,
            "" if ob else "orderbook unreadable",
            off,
        ])
    return out


def main():
    print("=" * 88)
    print("LATE ENTRY COLLECTOR -- records the book at T-3, T-2 and T-1. "
          "Places nothing.")
    print("=" * 88)
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=4000, cols=len(HEADERS))
        ws.update(values=[HEADERS], range_name="A1")
    # WIDEN THE HEADER IF THIS TAB PREDATES THE OFFSET COLUMN. The rows
    # already in it are T-1 readings written with eleven columns; the twelfth
    # is appended on the RIGHT so none of them shift. Old rows keep an empty
    # Offset cell, which is correct -- they were all T-1, and saying so by
    # back-filling would be writing data that was never recorded.
    try:
        have = ws.row_values(1)
        if len(have) < len(HEADERS):
            # GROW THE GRID FIRST. The tab was created with exactly eleven
            # columns, so writing L1 fails with "exceeds grid limits" -- and
            # because the widening is wrapped, that failure was swallowed and
            # the column never appeared.
            if ws.col_count < len(HEADERS):
                ws.add_cols(len(HEADERS) - ws.col_count)
            ws.update(values=[HEADERS[len(have):]],
                      range_name=f"{chr(ord('A') + len(have))}1")
            print(f"  widened the header: added {HEADERS[len(have):]}")
    except Exception as e:
        print(f"  header check skipped (ignored): {str(e)[:80]}")
    deadline = time.time() + SECONDS
    seen, wrote = set(), 0
    while time.time() < deadline:
        try:
            rows = rows_for_now(seen)
        except Exception as e:
            print(f"  sample failed (ignored): {str(e)[:90]}")
            rows = []
        if rows:
            try:
                ws.append_rows(rows, value_input_option="USER_ENTERED",
                               table_range="A1")
                wrote += len(rows)
                for r in rows:
                    print(f"  {r[0]} {r[11]:<4} {r[1][:24]:<24} "
                          f"{r[3]:>3}s left  ask {r[4]}c  size {r[7]}  "
                          f"-> {r[9]}")
            except Exception as e:
                print(f"  write failed (ignored): {str(e)[:90]}")
        time.sleep(POLL)
    print(f"\n  wrote {wrote} sample(s) to '{SHEET}'")
    print("\n  WHERE EVERY CANDIDATE WENT:")
    for k in ("polls", "markets seen", "not a major series", "no close time",
              "outside every sampling window", "inside a window",
              "already sampled at this offset", "no readable ask",
              "ask outside the band", "sampled"):
        print(f"    {k:<34} {TALLY.get(k, 0):>7,}")
    if not wrote:
        # A ZERO HERE IS A FAILURE, NOT A QUIET MARKET, and it needs to say
        # so. This collector ran for the better part of an hour and wrote
        # nothing on four consecutive runs while exiting 0, and the zero was
        # read upstream as "still accumulating". Markets close every fifteen
        # minutes around the clock; a session this long that samples nothing
        # has a broken read, not an empty exchange.
        print("  *** WROTE NOTHING. Over a session this long the sampling "
              "window opens dozens of times, so zero means the market list, "
              "the band filter or the orderbook read is broken -- not that "
              "the market was quiet. Do not report this as collected data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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
import os
import sys
import time

import predictor as P
import backfill as B
import collect15 as C
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


def rows_for_now(seen):
    """Sample any in-band market inside its last 90 seconds, once each."""
    out = []
    d, _e = C.get("/trade-api/v2/markets", status="open", limit=200)
    now = time.time()
    for m in ((d or {}).get("markets") or []):
        tk = str(m.get("ticker") or "")
        if not tk.startswith(MAJORS):
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        left = close - now
        off = window_for(left)
        # Keyed by (ticker, offset): the same market is wanted once in each
        # window, and only once in each.
        if off is None or (tk, off) in seen:
            continue
        q = C.quote_from_market(m)
        if not q or q.get("ask") is None:
            continue
        ask_c = 100.0 * q["ask"]
        if not (BAND[0] <= ask_c <= BAND[1]):
            continue
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

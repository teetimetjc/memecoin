"""Record the real order book one minute before close. Places nothing.

WHY THIS EXISTS SEPARATELY FROM THE BACKTEST. lateentry.py can say whether a
95c favourite with a minute left is underpriced, because candles carry the
quote. It cannot say whether you could BUY it: a quote is not a promise that
anyone will sell you six contracts at it. The live record already shows
partial fills at T-9, where the book is deeper and there is more time for a
seller to appear. At T-1 it is likely thinner, and that -- not the
calibration -- is what decides whether this idea is tradeable.

So this samples the actual orderbook at T-1 for markets in the band and
writes down what was there: the ask, the size at the ask, the spread, and
how many contracts a $6 order would have needed to sweep. The settlement is
joined later from the exchange.

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
]
BAND = (90.0, 97.0)
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3300
POLL = 20
WANT_CONTRACTS = 6
MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def rows_for_now(seen):
    """Sample any in-band market inside its last 90 seconds, once each."""
    out = []
    d, _e = C.get("/trade-api/v2/markets", status="open", limit=200)
    now = time.time()
    for m in ((d or {}).get("markets") or []):
        tk = str(m.get("ticker") or "")
        if not tk.startswith(MAJORS) or tk in seen:
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        left = close - now
        if not (25 <= left <= 90):
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
        seen.add(tk)
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
        ])
    return out


def main():
    print("=" * 88)
    print("LATE ENTRY COLLECTOR -- records the book at T-1. Places nothing.")
    print("=" * 88)
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=4000, cols=len(HEADERS))
        ws.update(values=[HEADERS], range_name="A1")
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
                    print(f"  {r[0]} {r[1][:26]:<26} {r[3]:>3}s left  "
                          f"ask {r[4]}c  size {r[7]}  -> {r[9]}")
            except Exception as e:
                print(f"  write failed (ignored): {str(e)[:90]}")
        time.sleep(POLL)
    print(f"\n  wrote {wrote} sample(s) to '{SHEET}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

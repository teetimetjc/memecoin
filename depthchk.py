"""Can six contracts actually be bought in WTI and GOLD at T-9? Places nothing.

WHY THIS COMES BEFORE ANY ORDER. goldoil.py put KXWTI15M at +3.56% net ROI
in the 90-96c band, positive in both chronological halves and second only
to SOL across every series tested. That is the strongest candidate this
project has turned up. It is also entirely a QUOTE result.

A quote is one number on one side of a book. It does not say how much size
sits behind it. The whole reason the live record disappoints against its own
backtest is that the quote and the fill are different numbers: orders sweep
up the book, and an order that sweeps pays more than the ask it was sized
on. In a thin book that difference is the entire edge. shortmkt measured
WTI's median volume at 111,910 against DOGE's 28,730, which says a book
EXISTS -- it does not say six contracts sit at the ask at the moment of
entry, which is the only thing that matters here.

SO THIS SAMPLES THE REAL BOOK at the same offset the bot enters on, for the
two candidates and for the live series as a baseline. Same treatment, same
moment, so the comparison is between markets rather than between methods.

WHAT IT RECORDS, per in-band market: the ask, the bid, the spread, the size
resting at the best ask, and how many levels a six-contract order would have
to eat. "Enough for $6" is yes only when ONE level covers it -- anything
else is a sweep, and a sweep is how a 93c decision becomes a 96c fill.

IT NEVER PLACES AN ORDER. It imports no ordering code, shares no concurrency
group with the live bot, and touches only read-only market endpoints.

READ IT AGAINST THE BASELINE, NOT AGAINST ZERO. If WTI's depth matches the
coins already being traded, the +3.56% is reachable on the same terms the
bot already lives with. If it is a fraction of that, the gap is real and
unreachable, which is the most expensive kind of finding to act on.
"""
import sys
import time

import predictor as P
import backfill as B
import collect15 as C
import shadow

SHEET = "Depth Check"
HEADERS = [
    "Sampled UTC", "Ticker", "Series", "Secs to close", "Yes ask ¢",
    "Yes bid ¢", "Spread ¢", "Size at ask", "Levels for 6 contracts",
    "Enough for $6", "Role", "Note",
]

CANDIDATES = ("KXWTI15M", "KXGOLD15M")
BASELINE = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
WATCH = CANDIDATES + BASELINE

BAND = (90.0, 96.0)
# The bot enters at T-9. The window is wide enough that a 15s poll cannot
# miss it and narrow enough that the reading means "at entry".
LO_SECS, HI_SECS = 500, 580
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3300
POLL = 15
WANT_CONTRACTS = 6


def rows_for_now(seen):
    out = []
    d, _e = C.get("/trade-api/v2/markets", status="open", limit=200)
    now = time.time()
    for m in ((d or {}).get("markets") or []):
        tk = str(m.get("ticker") or "")
        if not tk.startswith(WATCH) or tk in seen:
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        left = close - now
        if not (LO_SECS <= left <= HI_SECS):
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
        ser = tk.split("-")[0]
        seen.add(tk)
        out.append([
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            tk, ser, int(left),
            round(book_ask if book_ask is not None else ask_c, 1),
            round(bid_c, 1) if bid_c is not None else "",
            round(book_ask - bid_c, 1)
            if (book_ask is not None and bid_c is not None) else "",
            size if size is not None else "",
            lv, enough,
            "candidate" if ser in CANDIDATES else "baseline",
            "" if ob else "orderbook unreadable",
        ])
    return out


def main():
    print("=" * 92)
    print("DEPTH CHECK at T-9 -- can six contracts be bought? Places nothing.")
    print("  candidates:", ", ".join(CANDIDATES))
    print("  baseline:  ", ", ".join(BASELINE))
    print("=" * 92)
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
                    print(f"  {r[0]} {r[10]:<9} {r[2]:<12} {r[3]:>3}s  "
                          f"ask {r[4]}c  bid {r[5]}c  size {r[7]}  -> {r[9]}")
            except Exception as e:
                print(f"  write failed (ignored): {str(e)[:90]}")
        time.sleep(POLL)
    print(f"\n  wrote {wrote} sample(s) to '{SHEET}'")
    print("  Compare the candidates' 'Size at ask' and 'Enough for $6' against")
    print("  the baseline rows from the SAME minutes. A candidate whose book")
    print("  is a fraction of the coins already traded has an edge that cannot")
    print("  be reached at any stake worth betting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

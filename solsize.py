"""How big can a SOL bet get before the book eats the edge? Places nothing.

THE QUESTION. At $5 a bet the SOL paper record is +$15.83 over 123 bets --
about $1 a day, which is not worth the trouble. Scaling the stake scales
the profit ONLY if the fills hold, and that is not a given: the entire SOL
edge is 2.57c per contract ($15.83 across 615 contracts). An order larger
than the size resting at the best ask sweeps up the book and pays more than
the ask it was sized on. Two or three cents of sweep and there is no edge
left, however many contracts were bought.

SO THIS MEASURES THE REAL BOOK at the moment of entry and walks it for each
candidate stake, reporting the volume-weighted average fill against the best
ask. That difference -- the sweep cost in cents per contract -- is directly
comparable to the 2.57c the edge is worth.

WHAT IT DOES NOT TELL YOU. The book is a snapshot of resting orders; it does
not include size that would appear, or disappear, in the moment an order
arrives. It is also one side of the round trip: these markets settle rather
than being sold out of, so there is no exit sweep, which is the one thing
working in favour here.

AND IT IS STILL CONDITIONAL. The 2.57c edge itself is p=0.73 once SOL is
treated as the best of six series. A stake that preserves a chance edge
preserves nothing. This answers "would size destroy it", not "is it real".

IT NEVER PLACES AN ORDER. It imports no ordering code and reads only the
read-only market and orderbook endpoints.
"""
import collections
import os
import statistics as st
import sys
import time

import backfill as B
import collect15 as C
import openmk
import shadow

CANDIDATE = "KXSOL15M"
BASELINE = ("KXBTC15M", "KXETH15M", "KXXRP15M", "KXDOGE15M")
WATCH = (CANDIDATE,) + BASELINE

# The same band and the same entry offset the paper record was built on, so
# the depth is measured where the bets actually happen, not at a quiet moment.
BAND = (86.0, 96.0)
LO_SECS, HI_SECS = 500, 580
POLL = 15
SECONDS = int(sys.argv[1] if len(sys.argv) > 1
              else os.environ.get("SOLSIZE_SECONDS") or 3300)

# The measured edge, in cents per contract, that a sweep has to stay under.
EDGE_CENTS = float(os.environ.get("SOLSIZE_EDGE_CENTS") or 2.57)
STAKES = [5, 25, 50, 100, 250, 500, 1000]


def sweep(levels, stake):
    """Walk the book for `stake` dollars. Returns (contracts, avg fill c).

    Buys whole contracts, cheapest ask first, until the money runs out or
    the book does. avg fill is volume-weighted, in cents.
    """
    left, got, spent = float(stake), 0, 0.0
    for price_c, size in levels:
        if left <= 0 or size <= 0:
            break
        unit = price_c / 100.0
        if unit <= 0:
            continue
        can = min(int(size), int(left / unit))
        if can <= 0:
            break
        got += can
        spent += can * unit
        left -= can * unit
    if not got:
        return 0, None
    return got, 100.0 * spent / got


def sample(seen):
    out = []
    markets = openmk.open_markets(WATCH)
    now = time.time()
    for m in markets:
        tk = str(m.get("ticker") or "")
        ser = tk.split("-")[0]
        if ser not in WATCH or tk in seen:
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
        levels = shadow._levels(ob, "UP") if ob else []
        if not levels:
            # NOT RECORDED AS ZERO DEPTH. An unreadable book is a failed
            # read, and counting it as an empty book would understate the
            # depth of every market whose read happened to fail.
            seen.add(tk)
            out.append(dict(ticker=tk, series=ser, ok=False))
            continue
        best = levels[0][0]
        row = dict(ticker=tk, series=ser, ok=True, best=best,
                   top_size=levels[0][1],
                   total=sum(sz for _p, sz in levels), fills={})
        for s in STAKES:
            c, avg = sweep(levels, s)
            row["fills"][s] = (c, (avg - best) if avg is not None else None)
        seen.add(tk)
        out.append(row)
    return out


def main():
    print("=" * 104)
    print("HOW BIG CAN A BET GET BEFORE THE BOOK EATS THE EDGE")
    print("=" * 104)
    print(f"  sampling for {SECONDS}s, markets {BAND[0]:.0f}-{BAND[1]:.0f}c "
          f"at T-{HI_SECS}..T-{LO_SECS}s")
    print(f"  the SOL edge to protect is {EDGE_CENTS:.2f}c per contract")
    print("  PLACES NO ORDER.\n")

    rows, seen = [], set()
    t_end = time.time() + SECONDS
    polls = 0
    while time.time() < t_end:
        try:
            got = sample(seen)
        except Exception as e:
            print(f"  poll failed: {str(e)[:100]}")
            got = []
        polls += 1
        for r in got:
            rows.append(r)
            if r["ok"]:
                c5, s5 = r["fills"][100]
                head = (f"  {r['ticker']:<26} ask {r['best']:>5.1f}c  "
                        f"top {r['top_size']:>6}  book {r['total']:>7}  ")
                if s5 is None:
                    print(head + "$100 -> no fill")
                else:
                    print(head + f"$100 -> {c5:>4} contracts, "
                                 f"sweep {s5:+.2f}c")
            else:
                print(f"  {r['ticker']:<26} ORDERBOOK UNREADABLE")
        time.sleep(POLL)

    ok = [r for r in rows if r["ok"]]
    print(f"\n  {polls} poll(s), {len(rows)} market(s) sampled, "
          f"{len(ok)} with a readable book")
    if not ok:
        print("\n  NOTHING MEASURED. Either no market fell in the band and")
        print("  window during the run, or every orderbook read failed.")
        print("  This is not a finding of thin books -- it is no data.")
        print("=" * 104)
        return 0

    by = collections.defaultdict(list)
    for r in ok:
        by[r["series"]].append(r)

    for ser in [CANDIDATE] + [s for s in BASELINE if s in by]:
        rs = by.get(ser)
        if not rs:
            continue
        tag = "CANDIDATE" if ser == CANDIDATE else "baseline"
        print(f"\n{'-' * 104}")
        print(f"{ser}  ({tag}, {len(rs)} market(s))")
        print("-" * 104)
        print(f"  median size resting at the best ask: "
              f"{st.median([r['top_size'] for r in rs]):,.0f} contracts")
        print(f"  median total size in the visible book: "
              f"{st.median([r['total'] for r in rs]):,.0f} contracts")
        print(f"\n  {'stake':>8}{'contracts':>12}{'sweep c/contract':>20}"
              f"{'vs edge':>26}")
        for s in STAKES:
            cs = [r["fills"][s][0] for r in rs]
            sw = [r["fills"][s][1] for r in rs if r["fills"][s][1] is not None]
            if not sw:
                print(f"  {'$' + str(s):>8}{'-- no fill':>12}")
                continue
            msw = st.median(sw)
            mc = st.median(cs)
            if ser != CANDIDATE:
                verdict = ""
            elif msw <= 0.0:
                verdict = "fills at the ask"
            elif msw < EDGE_CENTS * 0.5:
                verdict = "keeps most of the edge"
            elif msw < EDGE_CENTS:
                verdict = "eats most of the edge"
            else:
                verdict = "EDGE GONE"
            print(f"  {'$' + str(s):>8}{mc:>12,.0f}{msw:>+20.2f}"
                  f"{verdict:>26}")

    print(f"\n{'=' * 104}")
    print("  READ THE SWEEP COLUMN, NOT THE CONTRACT COUNT. Buying more")
    print("  contracts is never the problem; paying more per contract is.")
    print(f"  A sweep at or above {EDGE_CENTS:.2f}c means the larger stake")
    print("  buys more of something that no longer makes money.")
    print("\n  AND THE EDGE BEING PROTECTED IS NOT YET REAL. SOL is +2.85%")
    print("  on 123 paper bets at quote prices, p=0.73 once treated as the")
    print("  best of six series. This says whether size would survive, not")
    print("  whether there is anything to size up.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

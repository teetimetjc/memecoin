"""How close did each winning bet come to dying?

THE QUESTION THIS ANSWERS IS ABOUT THE PATH, NOT THE OUTCOME. tally.py says
whether a bet settled at $1.00 or at nothing. It says nothing about what
happened in between, and the nine minutes in between are where a stop-loss,
an early exit or a "cash out" button would have acted. You watched two bets
on your iPad swing hard against the target and come back; this counts how
often that happened across every bet, instead of across the two you watched.

WHY IT MATTERS MORE THAN IT SOUNDS. Buying at 91c and holding is a strategy
whose whole shape is "usually fine, occasionally -$3.69". The obvious way to
improve it is to cut the losers early. That only works if losers behave
DIFFERENTLY from winners on the way through. If winners routinely dip to 30c
and recover, then any rule that sells at 30c converts a pile of wins into a
pile of small losses, and the strategy is destroyed by its own safety net.
This script decides which world we are in, from the exchange's own
per-minute history rather than from intuition.

WHAT IS MEASURED, and why it is the BID and not the mid or the ask:

  The lowest YES BID between entry and close is what you could actually have
  SOLD at. The mid is a price nobody was offering and the ask is what you
  would have paid to buy more. Using the mid here would flatter every dip by
  a cent or two and understate exactly the thing being measured.

  "Entry" is the fill timestamp, not the window open: a dip that happened
  before the order existed is not a dip this position survived.

  The FINAL candle is excluded. Its close is the settlement price, which is
  1.00 or 0.00 by definition, so including it would report every loser as
  having dipped to zero and tell you nothing you did not already know.

Read-only. Places nothing, writes nothing.
"""

import collections
import os
import sys
import time

import backfill as B
import live
import predictor as P
import tally


START = os.environ.get("PATH_SINCE") or "2026-10-07 15:49"
# The thresholds the report counts. 50c is "the market stopped believing";
# 25c and 10c are the ones that would have tripped any plausible stop.
MARKS = (0.50, 0.25, 0.10)
PAUSE = 0.25


def low_after(series, ticker, close, after_ts):
    """Lowest yes_bid strictly after `after_ts` and strictly before close.

    Returns (low, n_candles) or (None, 0) when the history is unusable --
    which is reported rather than silently treated as "no dip", because a
    failed read and a calm market are not the same finding.
    """
    end = B.ts(close)
    if end is None:
        return None, 0
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(end) - 20 * 60, end_ts=int(end), period_interval=1)
    if not d:
        return None, 0
    low, n = None, 0
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or t <= after_ts or t >= end:
            continue
        v = c.get("yes_bid")
        if not isinstance(v, dict):
            continue
        # low_dollars is the floor WITHIN the minute; close_dollars would
        # miss a dip that recovered inside the same candle, and those are
        # precisely the fast swings this is looking for.
        b = B._f(v.get("low_dollars"))
        if b is None:
            b = B._f(v.get("close_dollars"))
        if b is None or not (0.0 <= b <= 1.0):
            continue
        low = b if low is None else min(low, b)
        n += 1
    return low, n


def main():
    rows = tally.rows_since(START)
    if not rows:
        print("no live bets recorded since " + START)
        return 0
    # Settlement outcome per ticker, from the exchange rather than from our
    # own arithmetic -- the same source tally.py grades against.
    d = tally.get("/portfolio/settlements", limit=200)
    results = {}
    for s in ((d or {}).get("settlements") or []):
        tk = str(s.get("ticker") or "")
        rev = tally._f(s.get("revenue_dollars"))
        if rev is None:
            rev = tally._f(s.get("revenue"))
            if rev is not None and abs(rev) > 100:
                rev = rev / 100.0
        results[tk] = "yes" if (rev or 0.0) > 0 else "no"

    print("=" * 86)
    print("HOW FAR DID EACH BET FALL BEFORE IT SETTLED?")
    print("lowest yes bid between the fill and the close -- what you could have sold at")
    print("=" * 86)

    seen, out, missing = set(), [], 0
    for r in rows:
        tk = r["ticker"]
        # ONE ROW PER MARKET. Two orders in one market share a single price
        # path, so counting both would double-weight that path -- the same
        # error that made the live record read 3 losses where the exchange
        # shows 2 positions.
        if tk in seen:
            continue
        seen.add(tk)
        res = results.get(tk)
        if res is None:
            continue
        ser = tk.split("-")[0]
        mk, err = B.get("/trade-api/v2/markets/" + tk)
        ct = ((mk or {}).get("market") or {}).get("close_time")
        if not ct:
            missing += 1
            continue
        t0 = B.ts(str(r["ts"]).replace(" ", "T") + "Z") or 0
        low, n = low_after(ser, tk, ct, t0)
        time.sleep(PAUSE)
        if low is None or n == 0:
            missing += 1
            continue
        out.append(dict(ticker=tk, series=ser, entry=r["entry"],
                        won=(res == "yes"), low=low, n=n))

    if not out:
        print("  no usable candle history for any bet")
        return 0

    out.sort(key=lambda x: x["low"])
    # entry comes off the sheet already in CENTS; low comes back in DOLLARS.
    # Mixing the two silently is how a 91c entry reads as a 90c fall.
    print(f"\n  {'entry':>6} {'low':>6} {'fell':>7}  {'result':<6} market")
    for b in out:
        lo_c = 100.0 * b["low"]
        print(f"  {b['entry']:>5.0f}c {lo_c:>5.0f}c {b['entry'] - lo_c:>6.0f}c  "
              f"{('LOST' if not b['won'] else 'won'):<6} {b['ticker']}")

    W = [b for b in out if b["won"]]
    L = [b for b in out if not b["won"]]
    print("\n" + "=" * 86)
    print(f"  {len(out)} markets with usable history"
          + (f"   ({missing} unreadable)" if missing else ""))
    print(f"\n  {'dipped below':<16} {'winners':>18} {'losers':>16}")
    for m in MARKS:
        nw = sum(1 for b in W if b["low"] < m)
        nl = sum(1 for b in L if b["low"] < m)
        print(f"  {100*m:>13.0f}c   {nw:>7} of {len(W):<8} {nl:>7} of {len(L):<6}")

    if W:
        print(f"\n  deepest dip among the WINNERS: "
              f"{100*min(b['low'] for b in W):.0f}c")
    print("\n" + "=" * 86)
    # THE READING IS NOT AUTOMATIC, so state what would have to be true.
    # A stop-loss only helps if losers visit the stop and winners do not.
    if W and L:
        for m in MARKS:
            nw = sum(1 for b in W if b["low"] < m)
            nl = sum(1 for b in L if b["low"] < m)
            if nl == len(L) and nw == 0:
                print(f"  A stop at {100*m:.0f}c would have caught every loss "
                      f"and touched no winner -- ON THIS SAMPLE, which is "
                      f"{len(L)} losses and proves nothing yet.")
                break
        else:
            print("  No threshold separates them: every level a loser visits,"
                  "\n  some winner visits too. A stop there sells winners to"
                  "\n  avoid losses, which is the trade this rule cannot afford"
                  "\n  at 12:1 payoff asymmetry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

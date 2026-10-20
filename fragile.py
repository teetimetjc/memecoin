"""Is a 90-96c favourite that has been JUMPY more likely to fail?

THE HYPOTHESIS, with a mechanism rather than a data trawl. Two markets both
quoted 92c are not the same bet. One has sat at 92c for ten minutes. The
other lurched there from 70c in the last three. Both carry the same implied
8% chance of failing. If the jumpy one fails more often and the market
prices them identically, that is an edge; if it does not, the price already
knows and there is nothing here.

WHY NOT DISTANCE-FROM-STRIKE OVER VOLATILITY, which is the cleaner form of
the idea: the candlestick feed carries the CONTRACT price, not the
underlying, so the strike distance is not directly available. The contract's
own path is the same quantity seen through the market's eyes -- a contract
pinned at 92c is one the market thinks is settled; one swinging into 92c is
one it is still arguing about.

FEATURES, all measured at T-9 from the prior ten minutes only, so nothing
after the decision leaks in:
    vol   standard deviation of the yes mid over T-19..T-9
    mom   yes mid at T-9 minus yes mid at T-12 (the lurch)
    draw  worst drop from a running peak over T-19..T-9
    sprd  the bid-ask spread at T-9

SPLIT CHRONOLOGICALLY, older half to look, newer half to check. A rule found
and scored on the same bets is a description of those bets. The live record
is a third test and is deliberately NOT used here -- 18 losses cannot choose
between a dozen indicators, and the one time this was tried on the live set
it produced a 90c-floor pattern that evaporated.

READ THE CALIBRATION GAP, not the win rate. A bucket winning 88% is fine if
it was priced at 86c and bad if it was priced at 93c.

Read-only.
"""
import statistics as st
import sys
import time

import backfill as B

BAND = (0.90, 0.96)
ENTRY_OFFSET = 9          # minutes before close
LOOKBACK = 10             # minutes of history used for the features
# 400 per series gave 2,000 markets and only 151 in-band observations -- too
# few to split in half, let alone to bucket. The run cost 450s of a 2,300s
# budget, so the constraint was never time; it was that a 90-96c favourite at
# T-9 is about 7% of all markets.
PER_SERIES = 1600
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def mid_series(cs):
    """[(minutes_to_close, mid, spread)] from per-minute candles."""
    out = []
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars"))
                    if isinstance(v, dict) else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        out.append((t, (a + b) / 2.0, a - b, a))
    out.sort()
    return out


def features(path, close_ts):
    """(entry_ask, vol, mom, draw, spread) at T-9, or None."""
    want = close_ts - ENTRY_OFFSET * 60
    at = [p for p in path if p[0] <= want]
    if not at:
        return None
    t9 = at[-1]
    lo = want - LOOKBACK * 60
    hist = [p for p in path if lo <= p[0] <= want]
    if len(hist) < 5:
        return None
    mids = [p[1] for p in hist]
    vol = st.pstdev(mids) if len(mids) > 1 else 0.0
    three = [p for p in path if p[0] <= want - 3 * 60]
    mom = (t9[1] - three[-1][1]) if three else 0.0
    peak, draw = mids[0], 0.0
    for m in mids:
        peak = max(peak, m)
        draw = max(draw, peak - m)
    return t9[3], vol, mom, draw, t9[2]


def main():
    print("=" * 98)
    print("FRAGILE FAVOURITES: does a jumpy path predict a 90-96c bet failing?")
    print("=" * 98)
    rows = []
    started = time.time()
    for ser in SERIES:
        if time.time() - started > BUDGET:
            break
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception:
            continue
        print(f"  {ser}: {len(mk)} settled markets")
        for m in mk:
            if time.time() - started > BUDGET:
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            f = features(mid_series(cs), close)
            time.sleep(B.PAUSE)
            if not f:
                continue
            ask, vol, mom, draw, sprd = f
            if not (BAND[0] <= ask <= BAND[1]):
                continue
            rows.append(dict(ts=close, ask=ask, vol=vol, mom=mom,
                             draw=draw, sprd=sprd, won=(res == "yes")))
    print(f"\n  {len(rows)} in-band observations\n")
    if len(rows) < 200:
        print("  TOO FEW TO SPLIT. Not reporting buckets on this.")
        return 0

    rows.sort(key=lambda r: r["ts"])
    cut = len(rows) // 2
    halves = (("TRAIN (older half)", rows[:cut]),
              ("TEST  (newer half)", rows[cut:]))

    def report(name, data, key, edges):
        print(f"  {name} -- by {key}")
        print("    %-18s %-7s %-9s %-9s %s"
              % ("bucket", "n", "won %", "priced %", "gap"))
        for lo, hi in edges:
            g = [r for r in data if lo <= r[key] < hi]
            if len(g) < 15:
                print("    %-18s %-7d (too few)" % (f"{lo:.3f}-{hi:.3f}", len(g)))
                continue
            w = 100.0 * sum(1 for r in g if r["won"]) / len(g)
            p = 100.0 * st.mean([r["ask"] for r in g])
            print("    %-18s %-7d %-9.2f %-9.2f %+.2f"
                  % (f"{lo:.3f}-{hi:.3f}", len(g), w, p, w - p))
        print()

    for key, edges in (
        ("vol", [(0, .005), (.005, .01), (.01, .02), (.02, .05), (.05, 1)]),
        ("draw", [(0, .01), (.01, .03), (.03, .06), (.06, .12), (.12, 1)]),
        ("mom", [(-1, -.03), (-.03, -.01), (-.01, .01), (.01, .03), (.03, 1)]),
        ("sprd", [(0, .005), (.005, .01), (.01, .02), (.02, 1)]),
    ):
        for name, data in halves:
            report(name, data, key, edges)

    print("  HOW TO READ IT. A bucket is interesting only if its gap is")
    print("  clearly negative in BOTH halves. One half alone is a hypothesis;")
    print("  a sign that flips between halves is noise wearing a pattern.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

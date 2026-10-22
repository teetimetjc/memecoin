"""Is a late NO favourite priced like a late YES favourite? Read-only.

THE QUESTION AND WHY IT IS OPEN. lateentry found the 90-94c band at T-3
holding at +3.29% and +3.13% net ROI across its two chronological halves,
against +0.05% and +0.26% at the T-9 the live bot enters on. That is the
strongest thing this project has found -- and it scored `yes_ask` only, so
it is a statement about the YES side and about nothing else.

The live T-3 session is YES-only and placed nothing in its first 115
market-windows. The band is simply rare on one side: by three minutes out
most markets have resolved towards 0 or 100, and latecollect found three of
its seven in-band late favourites sitting on the NO side. If NO at T-3
behaves like YES at T-3, enabling it roughly doubles the opportunity. If it
does not, the YES-only restriction is a feature and the quiet sessions are
honest.

THE ARGUMENT FOR SYMMETRY IS REAL: a 93c NO favourite is structurally the
same trade as a 93c YES favourite -- buying the likely side of a binary. If
the mispricing is about FAVOURITES, it should appear on both sides. If it is
specific to the YES side, it will not.

THE ARGUMENT AGAINST IS ALSO REAL, and it is the larger sample:

    Polymarket NO   5,561 bets   -$0.024/bet   t=-0.54
    Kalshi NO       2,052 bets   +0.03%        t=+0.04
    Kalshi NO         256 bets   -$0.191/bet   (full history)
    live DOWN          10 bets   70.0% won against 91.70% needed

That last row is the only direct evidence from real money and its interval
excludes break-even on the LOSING side -- one of two things in the whole
record that manages that. But every one of those tested a different rule at
a different offset, so none of them answers this question. That is what
makes it worth measuring rather than assuming.

HOW THE NO PRICE IS BUILT. Kalshi runs ONE book: buying NO at price p is
the mirror of selling YES at 1-p, so the NO ask is 1 minus the YES BID. Not
1 minus the yes ask -- using the wrong one would price every NO bet at the
far side of the spread and make the side look worse than it is by exactly
the spread, which is the kind of error that reads as a finding.

A NO bet wins when the market settles NO. Outcomes come from Kalshi's own
result field, never from a price.

SAME BAR AS EVERYTHING ELSE: chronologically split, and a cell counts only
if it is positive in BOTH halves. Twenty cells per side means several will
look good by chance, which is exactly what the split is for.

Read-only.
"""
import statistics as st
import sys
import time

import backfill as B

OFFSETS = (1, 2, 3, 5, 9)
BANDS = ((0.90, 0.94), (0.94, 0.96), (0.96, 0.98), (0.98, 0.995))
PER_SERIES = 1200
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def quotes(cs, close_ts):
    """{offset: (yes_ask, no_ask)} from per-minute candles, in dollars.

    The NO ask is 1 - yes_bid, because the book is one book. Both sides are
    returned from the same candle so the two are strictly comparable: same
    market, same minute, same spread.
    """
    out = {}
    for off in OFFSETS:
        want = close_ts - off * 60
        best = None
        for cd in cs or []:
            t = B._f(cd.get("end_period_ts"))
            if t is None or t > want:
                continue

            def g(k):
                v = cd.get(k)
                return (B._f(v.get("close_dollars"))
                        if isinstance(v, dict) else None)
            a, b = g("yes_ask"), g("yes_bid")
            if a is None or b is None or not (0 < b <= a < 1):
                continue
            if best is None or t > best[0]:
                best = (t, a, b)
        if best:
            _t, ya, yb = best
            out[off] = (ya, 1.0 - yb)
    return out


def main():
    print("=" * 104)
    print("THE NO SIDE, LATE: is a 93c NO favourite priced like a 93c YES one?")
    print("  NO ask = 1 - yes bid, from the same candle, so the two sides are")
    print("  the same market in the same minute.")
    print("=" * 104)
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
            time.sleep(B.PAUSE)
            q = quotes(cs, close)
            if q:
                rows.append((q, res, close))
    print(f"\n  {len(rows)} markets with readable quotes\n")
    if len(rows) < 300:
        print("  TOO FEW.")
        return 0

    rows.sort(key=lambda r: r[2])
    mid = len(rows) // 2

    def cell(subset, off, lo, hi, side):
        """(n, won%, priced%, net ROI%) for one side's cell, or None."""
        idx = 0 if side == "YES" else 1
        want = "yes" if side == "YES" else "no"
        g = [(q[off][idx], res == want) for q, res, _t in subset
             if off in q and lo <= q[off][idx] < hi]
        if len(g) < 30:
            return None
        n = len(g)
        w = 100.0 * sum(1 for _p, won in g if won) / n
        p = 100.0 * st.mean([p for p, _ in g])
        fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
        cost = p + fee
        return n, w, p, 100.0 * (w - cost) / cost

    survivors = []
    for side in ("YES", "NO"):
        print("-" * 104)
        print(f"{side} SIDE")
        print("-" * 104)
        print("  %-8s %-13s %-22s %-22s %s"
              % ("offset", "band", "OLDER half", "NEWER half", "verdict"))
        for off in OFFSETS:
            for lo, hi in BANDS:
                o = cell(rows[:mid], off, lo, hi, side)
                nw = cell(rows[mid:], off, lo, hi, side)
                band = f"{lo:.2f}-{hi:.2f}"
                if o is None or nw is None:
                    print("  T-%-6d %-13s %-22s %-22s too thin to split"
                          % (off, band, f"n={o[0] if o else 0}",
                             f"n={nw[0] if nw else 0}"))
                    continue
                fo = "n=%-4d roi %+.2f%%" % (o[0], o[3])
                fn = "n=%-4d roi %+.2f%%" % (nw[0], nw[3])
                if o[3] > 0 and nw[3] > 0:
                    verdict = "SURVIVES"
                    survivors.append((side, off, band, o[3], nw[3]))
                elif o[3] <= 0 and nw[3] <= 0:
                    verdict = "negative in both"
                else:
                    verdict = "FLIPS -- noise"
                print("  T-%-6d %-13s %-22s %-22s %s"
                      % (off, band, fo, fn, verdict))
            print()

    print("=" * 104)
    print("  THE COMPARISON THAT DECIDES IT: the T-3 0.90-0.94 row on each")
    print("  side. The YES one is the live candidate at about +3%. If the NO")
    print("  one holds in both halves at a comparable size, the mispricing is")
    print("  about FAVOURITES and letting the T-3 session take NO roughly")
    print("  doubles its opportunities. If NO flips or is negative, the")
    print("  YES-only restriction is a feature and the quiet sessions are")
    print("  honest rather than broken.")
    print()
    if survivors:
        print("  Positive in both halves:")
        for side, off, band, a, b in sorted(
                survivors, key=lambda x: -min(x[3], x[4])):
            print(f"    {side:<4} T-{off} {band}   older {a:+.2f}%  "
                  f"newer {b:+.2f}%  (weaker half {min(a, b):+.2f}%)")
    else:
        print("  Nothing survived on either side.")
    print()
    print("  AND THIS IS STILL ONLY THE PRICE. The live DOWN record is 10")
    print("  bets at a -21.70 gap whose interval excludes break-even, so a")
    print("  positive result here earns a forward test with the drop guard")
    print("  armed, not an immediate switch.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

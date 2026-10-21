"""Is there a bet in a 95c favourite with one minute left?

THE APPEAL IS OBVIOUS and so is the catch. One minute gives the price almost
no room to flip, which is why it is quoted 95c. But at 95c you risk 95 to win
5, and the entry fee of 0.07*p*(1-p) adds about a third of a cent, so the
break-even win rate is about 95.3%. Being right nineteen times in twenty only
treads water.

So the question is not "do these usually win" -- they do -- but "do they win
MORE OFTEN than the price says", which is the same calibration question as
every other idea here, asked at the expensive end of the curve where there is
almost no room for an edge to live.

WHAT IT MEASURES. For every settled market it reads the yes ask at several
offsets before close and reports, per price band, how often that cohort
actually settled YES against what it cost -- fee included. A positive net ROI
means the band was underpriced.

LIQUIDITY IS NOT TESTED HERE and may be the real obstacle. A quote at T-1 is
not a promise that anyone will sell you six contracts at it; the live record
already shows partial fills at T-9 where the book is deeper. Read this as
"is the price wrong", not "can it be traded".

Read-only.
"""
import collections
import statistics as st
import sys
import time

import backfill as B

OFFSETS = (1, 2, 3, 5, 9)
BANDS = ((0.90, 0.94), (0.94, 0.96), (0.96, 0.98), (0.98, 0.995))
PER_SERIES = 1200
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def asks(cs, close_ts):
    """{offset_minutes: yes_ask} from per-minute candles."""
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
                best = (t, a)
        if best:
            out[off] = best[1]
    return out


def main():
    print("=" * 96)
    print("LATE ENTRY: is a 95c favourite with a minute left underpriced?")
    print("=" * 96)
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
            a = asks(cs, close)
            if a:
                rows.append((a, res == "yes"))
    print(f"\n  {len(rows)} markets with readable quotes\n")
    if len(rows) < 300:
        print("  TOO FEW.")
        return 0

    print("  %-9s %-13s %-7s %-9s %-10s %-9s %s"
          % ("offset", "band", "n", "won %", "priced %", "gap", "net ROI"))
    for off in OFFSETS:
        for lo, hi in BANDS:
            g = [(a[off], won) for a, won in rows
                 if off in a and lo <= a[off] < hi]
            if len(g) < 30:
                print("  T-%-7d %-13s %-7d (too few)"
                      % (off, f"{lo:.2f}-{hi:.2f}", len(g)))
                continue
            n = len(g)
            w = 100.0 * sum(1 for _p, won in g if won) / n
            p = 100.0 * st.mean([p for p, _ in g])
            # one contract: pay p plus the entry fee, receive 1 on a win
            fee = st.mean([100.0 * 0.07 * (px) * (1 - px) for px, _ in g])
            cost = p + fee
            roi = 100.0 * ((w / 100.0) * 100.0 - cost) / cost
            print("  T-%-7d %-13s %-7d %-9.2f %-10.2f %+-9.2f %+.2f%%"
                  % (off, f"{lo:.2f}-{hi:.2f}", n, w, p, w - p, roi))
        print()
    print("  'net ROI' already includes the entry fee, so it is the number")
    print("  that matters. A positive gap can still be a losing bet once the")
    print("  fee is paid, and at 95c the fee is about a third of a cent on a")
    print("  five-cent prize.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

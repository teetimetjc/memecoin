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

SPLIT CHRONOLOGICALLY, from 2026-10-21, and that is the point of the file
now. The first run tested twenty cells (five offsets by four bands) and
almost all of them came back positive, which is exactly what a search over
twenty cells looks like whether or not anything is there. T-3 in the
90-94c band read +3.22% net ROI against +0.12% at the T-9 the bot runs --
a difference big enough to act on and therefore big enough to be worth
disbelieving first.

So every cell is now reported in an older and a newer half, split on close
time across all five series pooled. THE BAR: a cell counts only if it is
positive in BOTH halves. A cell that is strongly positive in one half and
negative in the other is the search finding noise, and at twenty cells
several will do exactly that by chance. Nothing here is out-of-sample in
the strict sense -- the halves come from the same mined sweep -- but a
sign that survives the split is worth a forward test, and one that flips
is not.

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
                # CLOSE TIME IS CARRIED so the pooled rows can be ordered.
                # The series are walked one after another, newest first
                # within each, so list order is not time order and splitting
                # on it would split by SERIES, not by date.
                rows.append((a, res == "yes", close))
    print(f"\n  {len(rows)} markets with readable quotes\n")
    if len(rows) < 300:
        print("  TOO FEW.")
        return 0

    rows.sort(key=lambda r: r[2])
    mid = len(rows) // 2
    import datetime as _dt

    def _d(ts):
        return _dt.datetime.utcfromtimestamp(ts).strftime("%d %b")
    print(f"  older half: {_d(rows[0][2])} to {_d(rows[mid - 1][2])}   "
          f"newer half: {_d(rows[mid][2])} to {_d(rows[-1][2])}\n")

    def cell(subset, off, lo, hi):
        """(n, won%, priced%, net ROI%) for one cell, or None if too thin."""
        g = [(a[off], won) for a, won, _t in subset
             if off in a and lo <= a[off] < hi]
        if len(g) < 30:
            return None
        n = len(g)
        w = 100.0 * sum(1 for _p, won in g if won) / n
        p = 100.0 * st.mean([p for p, _ in g])
        # one contract: pay p plus the entry fee, receive 1 on a win
        fee = st.mean([100.0 * 0.07 * (px) * (1 - px) for px, _ in g])
        cost = p + fee
        return n, w, p, 100.0 * ((w / 100.0) * 100.0 - cost) / cost

    survivors = []
    print("  %-8s %-13s %-22s %-22s %s"
          % ("offset", "band", "OLDER half", "NEWER half", "verdict"))
    for off in OFFSETS:
        for lo, hi in BANDS:
            o = cell(rows[:mid], off, lo, hi)
            nw = cell(rows[mid:], off, lo, hi)
            band = f"{lo:.2f}-{hi:.2f}"
            if o is None or nw is None:
                print("  T-%-6d %-13s %-22s %-22s too thin to split"
                      % (off, band,
                         "n=%d" % (o[0] if o else 0),
                         "n=%d" % (nw[0] if nw else 0)))
                continue
            fo = "n=%-4d roi %+.2f%%" % (o[0], o[3])
            fn = "n=%-4d roi %+.2f%%" % (nw[0], nw[3])
            if o[3] > 0 and nw[3] > 0:
                verdict = "SURVIVES -- positive in both"
                survivors.append((off, band, o[3], nw[3]))
            elif o[3] <= 0 and nw[3] <= 0:
                verdict = "negative in both"
            else:
                verdict = "FLIPS -- noise"
            print("  T-%-6d %-13s %-22s %-22s %s"
                  % (off, band, fo, fn, verdict))
        print()

    print("  " + "-" * 92)
    if survivors:
        print(f"  {len(survivors)} cell(s) positive in both halves:")
        for off, band, a, b in sorted(survivors, key=lambda x: -min(x[2], x[3])):
            print(f"    T-{off} {band}   older {a:+.2f}%  newer {b:+.2f}%  "
                  f"(weaker half {min(a, b):+.2f}%)")
        print()
        print("  READ THE WEAKER HALF, not the average. The weaker half is")
        print("  the honest estimate: the better one is the half the search")
        print("  was drawn to. And a surviving cell is a candidate for a")
        print("  FORWARD test, not a reason to move the live offset -- these")
        print("  are quotes, and latecollect is measuring whether the book at")
        print("  that offset can actually fill six contracts.")
    else:
        print("  NOTHING survives the split. Every cell that looked good in")
        print("  the pooled run flips sign across time, which is what a sweep")
        print("  of twenty cells produces when there is nothing there.")
    print("  " + "-" * 92)
    print("  'net ROI' already includes the entry fee, so it is the number")
    print("  that matters. A positive gap can still be a losing bet once the")
    print("  fee is paid, and at 95c the fee is about a third of a cent on a")
    print("  five-cent prize.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

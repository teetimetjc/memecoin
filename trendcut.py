"""Does a favourite already DRIFTING DOWN at T-9 settle worse than a flat one?

THE QUESTION, in the form it has to be asked. Watching a live SOL bet lose,
the obvious reaction is that the entry was wrong: nine minutes out, the
price had already turned away from the target, and it kept going. The
strategy bought anyway because it reads one number, the ask, and 92c was in
band.

SO: among markets in the live 90-96c band at T-9, do the ones whose ask was
ALREADY FALLING settle worse than the ones that were flat or rising?

WHY THIS IS A HARDER CLAIM THAN IT SOUNDS, and the reason to pre-register
rather than go looking. A falling ask at T-9 is ALREADY IN THE 92c PRICE.
The market watched the same decline and repriced for it; that repricing is
why the contract costs 92c instead of 97c. For this filter to be worth
anything, the drift has to predict settlement BEYOND what the price has
already absorbed -- that is, the market must systematically UNDER-react to
its own last five minutes. That is a real and testable claim, and it is also
exactly the kind of claim that a subgroup search will hand you for free if
you let it pick its own threshold afterwards. So the thresholds are fixed
here, in the file, before the numbers are seen.

THE PRE-REGISTERED BAR, the same one the bands had to clear:

  1. n >= 150 in each group compared, or the comparison is not reported.
  2. Net ROI measured against THAT GROUP'S OWN mean cost per contract --
     a winning contract pays exactly $1, so the hurdle moves with the price
     and a group that bought cheaper is not thereby better.
  3. The sign must hold in BOTH chronological halves. A split that is
     positive overall and negative in one half is noise with a trend.
  4. The gap between groups must exceed the standard errors around it. Two
     cells three points apart with four-point error bars are one cell.

AND THE SHAPE TEST, which centsweep taught this project the hard way. The
drift is reported in BUCKETS, not just falling-vs-flat, because a real
effect is a GRADIENT: if a 4c decline hurts, a 2c decline should hurt less
and a 6c decline more. If the buckets alternate in sign, the split is
sampling error and the headline falling-vs-flat number is the luckiest cut
of it. Six of eleven adjacent cents flipped sign the last time this project
looked, INCLUDING inside the live band, so alternation is the default
expectation and not a surprise.

WHAT THIS CANNOT SETTLE. It is all QUOTES, from candle history -- the same
limitation that made T-3 look good on paper (+2.3-3.2%) and lose 6.38% live
on 1.61c of slippage. A clean result here earns a forward paper test, not a
change to the live rule.

Read-only. Places no order, writes no row, changes no setting.
"""
import math
import statistics as st
import sys
import time

import backfill as B

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")

# The live selection, reproduced exactly: the ask at T-9, inside 90-96c.
ENTRY_OFFSET = 9
BAND_LO, BAND_HI = 0.90, 0.96

# The drift is measured from T-14 to T-9: five minutes, the longest look-back
# the candle offsets allow before the window opens. T-12 is also reported as
# a shorter-horizon check, because an effect that exists at one horizon and
# vanishes at the other is a property of the horizon, not of the market.
LOOKBACKS = (14, 12)

# FIXED BEFORE THE NUMBERS ARE SEEN. A cent is the tick size, so these are
# the natural cuts and not a search over thresholds.
FALLING = -0.02          # ask dropped 2c or more into the entry
RISING = 0.02            # ask rose 2c or more
BUCKETS = [(-99, -0.05), (-0.05, -0.03), (-0.03, -0.01),
           (-0.01, 0.01), (0.01, 0.03), (0.03, 99)]

MIN_N = 150              # per group, pre-registered
MIN_BUCKET_N = 60        # buckets are the shape test, so a lower floor
PER_SERIES = 4000
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 3000


def ask_at(cs, close_ts, off):
    """Last clean two-sided ask at or before `off` minutes to close."""
    want = close_ts - off * 60
    best = None
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None or t > want:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars")) if isinstance(v, dict)
                    else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        if best is None or t > best[0]:
            best = (t, a)
    return best[1] if best else None


def collect(series, budget_end):
    """(entry ask, drift from each lookback, won, close ts) per market."""
    out = []
    try:
        mk = B.settled_markets(series, PER_SERIES)
    except Exception as e:
        print(f"  {series}: could not list markets ({str(e)[:60]})")
        return out
    print(f"  {series}: {len(mk)} settled markets", flush=True)
    kept = 0
    for m in mk:
        if time.time() > budget_end:
            print(f"  {series}: budget reached, {kept} usable", flush=True)
            break
        if str(m.get("result") or "").lower() not in ("yes", "no"):
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        cs = B.candles(series, m.get("ticker"), m.get("close_time"))
        time.sleep(B.PAUSE)
        entry = ask_at(cs, close, ENTRY_OFFSET)
        if entry is None or not (BAND_LO <= entry < BAND_HI):
            continue
        drift = {}
        for lb in LOOKBACKS:
            prior = ask_at(cs, close, lb)
            # A missing earlier quote is NOT a zero drift. It is unknown, and
            # recording it as flat would quietly stuff the flat group with
            # markets that had no prior quote at all.
            drift[lb] = None if prior is None else entry - prior
        out.append((entry, drift, str(m.get("result")).lower() == "yes",
                    close))
        kept += 1
    return out


def cell(g, minimum):
    """(n, won%, cost%, roi%, se points) for a list of (price, won)."""
    if len(g) < minimum:
        return None
    n = len(g)
    w = 100.0 * sum(1 for _p, won in g if won) / n
    p = 100.0 * st.mean([p for p, _ in g])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
    cost = p + fee
    se = math.sqrt(max(w * (100.0 - w), 0.0) / n)
    return n, w, cost, 100.0 * (w - cost) / cost, se


def line(label, c):
    if c is None:
        return f"  {label:<26} too thin"
    n, w, cost, roi, se = c
    return (f"  {label:<26} n {n:>5}   won {w:6.2f}%  (+/- {se:4.2f})   "
            f"cost {cost:6.2f}%   net ROI {roi:+6.2f}%")


def halves(rows, lb, lo, hi, minimum):
    """The same cut on each chronological half. Sign must hold in both."""
    sel = [(e, won, t) for e, d, won, t in rows
           if d.get(lb) is not None and lo <= d[lb] < hi]
    if len(sel) < 2 * minimum:
        return None, None
    sel.sort(key=lambda r: r[2])
    mid = len(sel) // 2
    return (cell([(e, w) for e, w, _ in sel[:mid]], minimum),
            cell([(e, w) for e, w, _ in sel[mid:]], minimum))


def main():
    print("=" * 104)
    print("DRIFT AT ENTRY: do 90-96c favourites already falling at T-9 "
          "settle worse?")
    print("  Thresholds and the pass bar were fixed in the file before any "
          "number was read.")
    print("=" * 104)

    end = time.time() + BUDGET
    rows = []
    for s in SERIES:
        if time.time() > end:
            print(f"  [budget reached before {s}]")
            break
        rows += collect(s, end)

    print(f"\n  {len(rows)} settled markets with a clean T-9 ask inside "
          f"{100*BAND_LO:.0f}-{100*BAND_HI:.0f}c")
    if len(rows) < 2 * MIN_N:
        print("  NOT ENOUGH DATA to answer this. Reporting nothing rather")
        print("  than reporting a number that cannot carry the question.")
        return 1

    for lb in LOOKBACKS:
        have = [r for r in rows if r[1].get(lb) is not None]
        print(f"\n{'=' * 104}")
        print(f"DRIFT FROM T-{lb} TO T-{ENTRY_OFFSET}   "
              f"({len(have)} markets with quotes at both)")
        print("=" * 104)
        if len(have) < 2 * MIN_N:
            print("  too thin at this look-back; skipped.")
            continue

        base = cell([(e, w) for e, _d, w, _t in have], MIN_N)
        print(line("ALL (the control)", base))
        print()

        groups = (
            (f"FALLING  (<= {100*FALLING:.0f}c)", -99, FALLING),
            ("FLAT     (between)", FALLING, RISING),
            (f"RISING   (>= +{100*RISING:.0f}c)", RISING, 99),
        )
        got = {}
        for name, lo, hi in groups:
            sel = [(e, w) for e, d, w, _t in have if lo <= d[lb] < hi]
            c = cell(sel, MIN_N)
            got[name] = c
            print(line(name, c))

        # CONDITION 3: the sign has to survive a chronological split.
        print("\n  BOTH HALVES -- a split that flips between halves is noise")
        for name, lo, hi in groups:
            h1, h2 = halves(have, lb, lo, hi, MIN_N // 2)
            if h1 is None or h2 is None:
                print(f"    {name:<26} too thin to halve")
                continue
            ok = "SAME SIGN" if (h1[3] > 0) == (h2[3] > 0) else "*** FLIPS"
            print(f"    {name:<26} first {h1[3]:+6.2f}%   "
                  f"second {h2[3]:+6.2f}%   {ok}")

        # CONDITION 4: is the gap bigger than the noise around it?
        f = got[groups[0][0]]
        fl = got[groups[1][0]]
        if f and fl:
            gap = f[1] - fl[1]
            se = math.sqrt(f[4] ** 2 + fl[4] ** 2)
            print(f"\n  FALLING minus FLAT, on the WIN RATE: {gap:+.2f} "
                  f"points, standard error {se:.2f}")
            if abs(gap) < 2 * se:
                print("  -> INSIDE the noise. On this data the two groups are")
                print("     one group, and the filter has nothing to act on.")
            else:
                print("  -> outside two standard errors. Worth the shape test")
                print("     below before it is believed.")

        # THE SHAPE TEST. A real effect slopes; noise alternates.
        print(f"\n  BUCKET BY BUCKET -- a real effect is a GRADIENT, noise "
              "alternates")
        prev = None
        flips = 0
        for lo, hi in BUCKETS:
            sel = [(e, w) for e, d, w, _t in have if lo <= d[lb] < hi]
            c = cell(sel, MIN_BUCKET_N)
            lbl = (f"{100*lo:+.0f}c .. {100*hi:+.0f}c" if abs(lo) < 90
                   else f"below {100*hi:+.0f}c")
            if abs(hi) > 90:
                lbl = f"above {100*lo:+.0f}c"
            print(line("   " + lbl, c))
            if c is not None:
                if prev is not None and (prev > 0) != (c[3] > 0):
                    flips += 1
                prev = c[3]
        print(f"    adjacent sign flips across the buckets: {flips}")
        if flips >= 2:
            print("    -> ALTERNATING. That is the signature of sampling")
            print("       error, not of a gradient. Whatever the headline")
            print("       rows say, this does not support a drift filter.")

    print("\n" + "=" * 104)
    print("  HOW TO READ THIS, including if it looks good.")
    print()
    print("  Every price here is a QUOTE from candle history. T-3 cleared a")
    print("  quote test at +2.3 to +3.2% and lost 6.38% live, the whole gap")
    print("  being 1.61c of slippage. So a pass here earns a forward PAPER")
    print("  test on the Paper Bets tab, not a change to the live rule.")
    print()
    print("  And the claim being tested is a strong one: that the market")
    print("  under-reacts to its own last five minutes. The 92c price")
    print("  already contains the decline. A filter only pays if the")
    print("  repricing was incomplete -- which is possible, and is not the")
    print("  way to bet unless the data says so plainly.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

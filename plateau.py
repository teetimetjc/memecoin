"""Is the 80-96c/T-9 result a plateau or a knife-edge?

Five series came out positive at t>=2 in the pooled run -- BTC, ETH, SOL,
XRP, DOGE -- where 0.45 of eighteen would be expected by chance. The pooled
rule itself was null (+$0.034/bet, t=+1.1), so what is on trial is narrower
than what was pre-specified, and the honest question is whether the five
majors show a REGION of the book that pays or a single lucky cell.

WHY THE SHAPE SETTLES IT. A real feature of expensive contracts near expiry
would not switch off at 79c and switch on at 81c. It would fade smoothly
across neighbouring prices and neighbouring offsets, because nothing about
the market changes discontinuously there. A result that exists only in one
band at one offset is almost always a fee boundary, a rounding rule, or the
1,657th cell of a search. So this prints the whole grid -- every band at
every offset, for the majors and for the rest separately -- and lets the
shape answer.

AND THE STATISTICS ARE FIXED. The pooled run's shuffle was degenerate:
permuting values among equal-sized windows preserves the total, so the mean
is mathematically invariant and p=1.000 meant nothing. It was built for a
max-over-cells statistic and reused where it does not apply. Replaced here
with a CLUSTER BOOTSTRAP -- resample whole close-times with replacement,
which is the right null for a clustered mean and gives an interval rather
than a p-value theatre.

WHAT A PLATEAU WOULD AND WOULD NOT PROVE. It would mean the five majors
genuinely pay in a region of the book, on data that chose them. It would
still not be an edge: the selection happened after looking, so the only
honest next step is a frozen spec and FORWARD data. The fallen favourite
scored +3.71pp on discovery, was frozen because it looked good, and returned
-$1.29/bet on 2,355 held-out bets.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

from hunt import SPLIT, bets_for, cse, fee, load, robust, score

MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
OFFSETS = (14, 12, 9, 6, 3, 1)
# Finer than the sweep's bands, deliberately: a knife-edge shows up as one
# lit cell beside dark ones, and that cannot be seen at 16c resolution.
GRID = ((0.60, 0.70), (0.70, 0.78), (0.78, 0.84), (0.84, 0.90),
        (0.90, 0.96), (0.96, 0.99))
BOOT = 2000
STAKE = 10.0
MIN_CELL = 150


def breakeven(px):
    c = int(STAKE / px)
    return ((c * px + fee(c, px)) / c) if c >= 1 else 1.0


def boot_ci(bets):
    """Cluster bootstrap: resample close-times with replacement.

    The right null for a mean whose observations are grouped. Returns
    (lo, hi) at 95% and the share of resamples at or below zero.
    """
    g = collections.defaultdict(list)
    for b in bets:
        g[b[1]].append(b[0])
    keys = list(g)
    if len(keys) < 10:
        return None, None, None
    rnd = random.Random(53)
    means = []
    for _ in range(BOOT):
        tot = cnt = 0.0
        for _ in range(len(keys)):
            v = g[keys[rnd.randrange(len(keys))]]
            tot += sum(v)
            cnt += len(v)
        if cnt:
            means.append(tot / cnt)
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means))]
    share = sum(1 for m in means if m <= 0) / len(means)
    return lo, hi, share


def grid_for(M, label):
    print(f"\n  {label}")
    head = "    " + f"{'band':<12}" + "".join(f"{'T-'+str(o):>12}"
                                              for o in OFFSETS)
    print(head)
    print("    " + "-" * (12 + 12 * len(OFFSETS)))
    for band in GRID:
        cells = []
        for off in OFFSETS:
            b = bets_for(M, "YES", off, band) + bets_for(M, "NO", off, band)
            if len(b) < MIN_CELL:
                cells.append("        --  ")
                continue
            s = score(b)
            star = "*" if (s["mean"] > 0 and s["t"] > 2.0) else " "
            cells.append(f"{s['mean']:>+10.3f}{star} ")
        print(f"    {100*band[0]:.0f}-{100*band[1]:.0f}c".ljust(16)
              + "".join(cells))
    print("    (* = positive with clustered t>2; -- = under "
          f"{MIN_CELL} bets)")


def main():
    M = load()
    if not M:
        return 1
    maj = [m for m in M if m["ser"] in MAJORS]
    rest = [m for m in M if m["ser"] not in MAJORS]
    print(f"\n  majors: {len(maj)} markets   everything else: {len(rest)}")

    print("\n" + "=" * 100)
    print("THE GRID: $/bet by price band and entry offset")
    print("=" * 100)
    print("  A real feature fades smoothly. One lit cell beside dark ones is")
    print("  a knife-edge, and knife-edges are usually arithmetic, not edge.")
    grid_for(maj, "THE FIVE MAJORS (BTC, ETH, SOL, XRP, DOGE)")
    grid_for(rest, "EVERY OTHER SERIES")

    print("\n" + "=" * 100)
    print("THE MAJORS' 80-96c RULE AT T-9, SCORED PROPERLY")
    print("=" * 100)
    times = sorted({m["t"] for m in M})
    cut = times[int(SPLIT * len(times))]
    for name, ms in (("full sample", maj),
                     ("discovery (early 70%)",
                      [m for m in maj if m["t"] < cut]),
                     ("holdout (late 30%)",
                      [m for m in maj if m["t"] >= cut])):
        b = (bets_for(ms, "YES", 9, (0.80, 0.96))
             + bets_for(ms, "NO", 9, (0.80, 0.96)))
        s = score(b)
        if not s:
            print(f"  {name:<24} no bets")
            continue
        lo, hi, share = boot_ci(b)
        b2, w2 = robust(b)
        need = breakeven(s["px"])
        ci = (f"[{100*lo:+.3f}, {100*hi:+.3f}]c" if lo is not None
              else "n/a")
        print(f"  {name:<24} n={s['n']:<6} won {100*s['win']:.1f}% "
              f"needs {100*need:.1f}%  ${s['mean']:+.3f}/bet  t={s['t']:+.1f}")
        print(f"    bootstrap 95% on $/bet {ci}   "
              + (f"resamples at or below zero: {100*share:.1f}%"
                 if share is not None else ""))
        print(f"    minus two best close-times ${b2:+.2f}   "
              f"minus two worst ${w2:+.2f}")
        print(f"    staked ${10*s['n']:,}   profit ${s['mean']*s['n']:+,.2f}")

    print("\n" + "=" * 100)
    print("Read the GRID first. If the majors are positive across adjacent")
    print("bands and offsets, that is a region of the book and worth freezing")
    print("a spec over. If only one cell is lit, it is the 1,657th cell of a")
    print("search and this is where it stops.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

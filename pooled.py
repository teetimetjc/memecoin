"""The pooled 80-96c rule at T-9: the last test this data can support.

The 1,656-cell sweep found no survivor, but one pattern held its sign across
SIX independent series -- buying the expensive side, 80-96c, nine minutes out:

    KXWTI15M/YES/T-6    disc +0.338   holdout +0.497   n=165
    KXDOGE15M/NO/T-9    disc +0.183   holdout +0.359   n=264
    KXWTI15M/NO/T-9     disc +0.202   holdout +0.308   n=177
    KXXRP15M/NO/T-9     disc +0.271   holdout +0.258   n=266
    KXBTC15M/YES/T-9    disc +0.262   holdout +0.244   n=246
    KXETH15M/YES/T-9    disc +0.272   holdout +0.024   n=268

Every one failed on SAMPLE SIZE, not on direction: 165-310 bets gives t near
1, nowhere near the bar. Six separate coins agreeing is a different thing
from one cell getting lucky, so the fix is to stop slicing and pool them.

THE RULE, FIXED BEFORE THIS RUNS.

  Universe: every M15H market with a two-sided quote at the entry offset.
  Both sides eligible: YES at ask9, NO at 1 - bid9. A pattern on one side of
  one book is an artefact of how the data was written down.
  Price band: 0.80 <= price < 0.96.
  Entry: T-9. Stake $10 flat, contracts = int(10/price).
  Fee: 0.07 * C * P * (1-P), unrounded, entry only -- a winner settles at
  $1.00 with nothing left to sell.
  Held to settlement, on Kalshi's own result field.
  ALL SERIES POOLED. No per-series selection, which is the entire point:
  choosing the six that looked good is how the sweep's cells got there.

WHAT THIS CANNOT CLAIM, AND SAYING IT UP FRONT. The band and the offset were
chosen by LOOKING at the sweep's output, so the late-30% holdout is no
longer untouched for this rule. Pooling fixes the power problem, not the
selection problem. A positive result here is a candidate for a frozen spec
and FORWARD data -- it is not a confirmed edge, and the fallen favourite is
the standing reminder: +3.71pp on discovery, -$1.29/bet on 2,355 held-out
bets, with the spec frozen in advance.

THREE CHECKS THAT CAN STILL KILL IT HERE.

  THE MIRROR. Buying the cheap side of the same markets must lose roughly
  what this wins. If both sides look positive, the arithmetic is wrong, not
  the market.

  THE NEIGHBOURS. If 80-96c works and 70-80c and 96-99c do not, that is a
  knife-edge, and knife-edges in a price band are how a fee boundary or a
  rounding rule masquerades as an edge.

  BREAK-EVEN. At an average price near 88c the rule needs to win about 88%
  plus the fee. The favourites rule hit 91.3% and still lost, needing 92.5%.
  The win rate is printed against its break-even line, always.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

from hunt import SPLIT, bets_for, cse, fee, load, robust, score

OFFSET = 9
BAND = (0.80, 0.96)
NEIGHBOURS = ((0.70, 0.80), (0.96, 0.99), (0.60, 0.70))
ALT_OFFSETS = (14, 12, 6, 3, 1)
NPERM = 400
STAKE = 10.0


def breakeven(px):
    """Win rate at which a flat $10 bet at this price returns zero."""
    c = int(STAKE / px)
    if c < 1:
        return 1.0
    return (c * px + fee(c, px)) / c


def line(bets, label, indent="  "):
    s = score(bets)
    if s is None or s["n"] < 40:
        n = 0 if s is None else s["n"]
        print(f"{indent}{label:<40} n={n:<6} too few")
        return None
    need = breakeven(s["px"])
    b2, w2 = robust(bets)
    flag = ""
    if s["mean"] > 0:
        ok = s["t"] > 2.0 and b2 > 0 and w2 > 0
        flag = "  <== POSITIVE" if ok else "  (positive, fails a check)"
    print(f"{indent}{label:<40} n={s['n']:<6} won {100*s['win']:>5.1f}% "
          f"needs {100*need:>5.1f}%  paid {100*s['px']:>4.1f}c  "
          f"${s['mean']:>+7.3f}/bet t={s['t']:>+5.1f}{flag}")
    return s, b2, w2


def shuffle_p(bets, obs):
    """Permute outcomes WITHIN close-time windows of equal size."""
    vals = [b[0] for b in bets]
    g = collections.defaultdict(list)
    for i, b in enumerate(bets):
        g[b[1]].append(i)
    sizes = collections.defaultdict(list)
    for ct, ix in g.items():
        sizes[len(ix)].append(ix)
    rnd = random.Random(41)
    nulls = []
    for _ in range(NPERM):
        sv = [0.0] * len(vals)
        for size, gl in sizes.items():
            order = list(range(len(gl)))
            rnd.shuffle(order)
            for a_, b_ in enumerate(order):
                src, dst = gl[b_], gl[a_]
                for z in range(size):
                    sv[dst[z]] = vals[src[z]]
        nulls.append(sum(sv) / len(sv))
    nulls.sort()
    beat = sum(1 for x in nulls if x >= obs)
    return st.median(nulls), nulls[int(0.9 * NPERM)], (beat + 1) / (NPERM + 1)


def main():
    M = load()
    if not M:
        return 1
    times = sorted({m["t"] for m in M})
    cut = times[int(SPLIT * len(times))]

    def both(ms, off, band):
        return (bets_for(ms, "YES", off, band)
                + bets_for(ms, "NO", off, band))

    allb = both(M, OFFSET, BAND)
    # The split is done on the MARKETS, not on the bets: a bet carries a
    # close-time string, and comparing strings to an epoch cut is how a
    # holdout quietly becomes the full sample.
    dm = [m for m in M if m["t"] < cut]
    hm = [m for m in M if m["t"] >= cut]

    print("\n" + "=" * 112)
    print(f"POOLED RULE: buy the expensive side, "
          f"{100*BAND[0]:.0f}-{100*BAND[1]:.0f}c, at T-{OFFSET}, "
          f"ALL {len({m['ser'] for m in M})} series, both sides")
    print("=" * 112)
    print("  'needs' is the win rate that breaks even at the price paid. "
          "That is the bar, not 50%.\n")

    full = line(allb, "FULL SAMPLE")
    line(both(dm, OFFSET, BAND), "  discovery (early 70%)")
    hb = both(hm, OFFSET, BAND)
    hold = line(hb, "  holdout (late 30%)")

    print("\n  THE MIRROR -- the cheap side of the same markets")
    line(both(M, OFFSET, (0.04, 0.20)), "buy 4-20c instead")

    print("\n  THE NEIGHBOURS -- is the band a knife-edge?")
    for nb in NEIGHBOURS:
        line(both(M, OFFSET, nb),
             f"{100*nb[0]:.0f}-{100*nb[1]:.0f}c at T-{OFFSET}")

    print(f"\n  OTHER OFFSETS at {100*BAND[0]:.0f}-{100*BAND[1]:.0f}c")
    for o in ALT_OFFSETS:
        line(both(M, o, BAND), f"T-{o}")

    print("\n  PER SERIES (information only -- selection is what pooling "
          "exists to avoid)")
    byser = collections.defaultdict(list)
    for b in allb:
        byser[b[2]].append(b)
    for ser in sorted(byser, key=lambda k: -len(byser[k])):
        line(byser[ser], ser, indent="    ")

    if full:
        s, b2, w2 = full
        print("\n" + "=" * 112)
        print(f"  full sample      ${s['mean']:+.3f}/bet on n={s['n']}  "
              f"t={s['t']:+.1f}")
        print(f"  without best two close-times   ${b2:+.2f} total")
        print(f"  without worst two close-times  ${w2:+.2f} total")
        med, p90, p = shuffle_p(allb, s["mean"])
        print(f"  shuffle within close-times: median ${med:+.3f}  "
              f"90th ${p90:+.3f}  p={p:.3f}")
        print(f"  total staked ${10*s['n']:,}   total profit "
              f"${s['mean']*s['n']:+,.2f}")
    print("\n" + "=" * 112)
    print("  The band and offset were chosen by reading the sweep's output,")
    print("  so pooling fixes the POWER problem and not the SELECTION one.")
    print("  A positive result here is a candidate for a frozen spec and")
    print("  FORWARD data. It is not a confirmed edge.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    sys.exit(main())

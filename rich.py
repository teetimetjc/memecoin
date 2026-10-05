"""Hunt for HIGHER-return versions of the expensive-favourite region.

The frozen rule makes +$0.189 a bet, which is 1.9% of a $10 stake. The ask is
for more per bet, so the arithmetic that governs it has to be stated first:

    $/bet = (10 / price) * (true_p - price - fee_rate)

The contract count is the leverage, and it is LARGER at low prices. A 1.7pp
edge at 87c pays $0.19; the same 1.7pp edge at 20c would pay $0.85. So per-bet
return is maximised by cheap contracts -- and the cheap side is exactly where
this project measured its worst losses, -$2.919/bet at 4-20c. The edge lives
where the leverage does not. That is the bind, and no search escapes it.

So three routes are tried here, and only the first two are searchable.

  A BIGGER PROBABILITY EDGE under more specific conditions. Path shape is
  added -- did the contract RISE or FALL into the entry -- which the
  1,656-cell sweep did not test.

  A DIFFERENT EXIT, which the grid's own shape suggests. T-9 is positive and
  T-3 is sharply negative; if the gain accrues early and is then given back
  as the spread widens into expiry, selling at T-6 beats holding to
  settlement. Exits are priced at the BID with a taker fee on both legs,
  because getting out early means crossing.

  SIZING, which is not a search result and is noted in the output rather
  than hunted: a real 1.9% edge at $100 a bet is ten times the money of the
  same edge at $10, with none of the statistical risk of a narrower rule.

EVERY CELL IS REPORTED THREE WAYS: dollars per bet, percent of stake, and the
probability edge in points. Dollars alone make a cheap-contract rule look
brilliant when its edge is identical; percent alone hides how few bets there
are. Both are needed to tell a better rule from a more levered one.

AND THE HOLDOUT IS STILL THE JUDGE. Candidates are found in the early 70% and
printed with their late-30% result beside them. The frozen spec in
specs/expensive_favourite.md is NOT touched by anything here; this is a search
for its richer cousin, and anything found needs its own freeze and its own
forward data.

Read-only.
"""

import collections
import math
import statistics as st
import sys

from hunt import SPLIT, cse, fee, load, robust

MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRIES = (12, 9, 6)
BANDS = ((0.20, 0.45), (0.45, 0.62), (0.62, 0.78), (0.78, 0.84),
         (0.84, 0.90), (0.90, 0.96))
EXITS = ("settle", 6, 3, 1)
PATHS = ("any", "rose", "fell")
STAKE = 10.0
MIN_DISC = 300
MIN_HOLD = 150


def mid(q):
    return (q[0] + q[1]) / 2.0


def bets(M, side, entry, band, exitat, path):
    """Every bet this variant would have taken, priced to the cent."""
    lo, hi = band
    out = []
    for m in M:
        q = m["path"].get(entry)
        if not q:
            continue
        b, a = q
        px = a if side == "YES" else (1.0 - b)
        if not (lo <= px < hi):
            continue
        # Path shape: where was this side's mid at T-14 relative to now?
        if path != "any":
            q0 = m["path"].get(14)
            if not q0:
                continue
            m0 = mid(q0) if side == "YES" else 1.0 - mid(q0)
            now = mid(q) if side == "YES" else 1.0 - mid(q)
            if path == "rose" and not (now > m0 + 0.02):
                continue
            if path == "fell" and not (now < m0 - 0.02):
                continue
        c = int(STAKE / px)
        if c < 1:
            continue
        cost = c * px + fee(c, px)
        if exitat == "settle":
            won = m["yes"] if side == "YES" else (not m["yes"])
            v = (c if won else 0) - cost
        else:
            qx = m["path"].get(exitat)
            if not qx:
                continue
            bx, ax = qx
            # Getting out early means crossing: we SELL at the bid of our
            # own side, and pay the taker fee again.
            sell = bx if side == "YES" else (1.0 - ax)
            if not (0 < sell < 1):
                continue
            v = (c * sell - fee(c, sell)) - cost
        out.append((v, m["ct"], px, (m["yes"] if side == "YES"
                                     else not m["yes"])))
    return out


def score(bs):
    n = len(bs)
    if not n:
        return None
    g = collections.defaultdict(list)
    for v, ct, _, _ in bs:
        g[ct].append(v)
    mean = sum(b[0] for b in bs) / n
    se = cse(g, n)
    px = st.mean([b[2] for b in bs])
    win = sum(1 for b in bs if b[3]) / n
    c = int(STAKE / px)
    need = ((c * px + fee(c, px)) / c) if c >= 1 else 1.0
    return dict(n=n, mean=mean, se=se, t=((mean / se) if se else 0.0),
                px=px, win=win, edge=(win - need) * 100.0,
                pct=100.0 * mean / STAKE)


def main():
    M = [m for m in load() if m["ser"] in MAJORS]
    print(f"  majors only: {len(M)} markets")
    times = sorted({m["t"] for m in M})
    cut = times[int(SPLIT * len(times))]
    dm = [m for m in M if m["t"] < cut]
    hm = [m for m in M if m["t"] >= cut]

    rows = []
    for side in ("YES", "NO"):
        for entry in ENTRIES:
            for band in BANDS:
                for exitat in EXITS:
                    for path in PATHS:
                        bd = bets(dm, side, entry, band, exitat, path)
                        if len(bd) < MIN_DISC:
                            continue
                        sd = score(bd)
                        if not sd or sd["mean"] <= 0:
                            continue
                        bh = bets(hm, side, entry, band, exitat, path)
                        if len(bh) < MIN_HOLD:
                            continue
                        sh = score(bh)
                        rows.append((sd, sh, bh,
                                     (side, entry, band, exitat, path)))

    print(f"\n  variants positive in discovery with a scoreable holdout: "
          f"{len(rows)}")
    if not rows:
        print("  none. No richer version of the rule exists in this data.")
        return 0

    rows.sort(key=lambda r: -r[0]["mean"])
    print("\n" + "=" * 122)
    print("RICHER VARIANTS, best discovery $/bet first")
    print("=" * 122)
    print(f"  {'variant':<44} {'disc $/bet':>10} {'%stake':>7} {'edge pp':>8}"
          f" {'disc t':>7} {'HOLD $/bet':>11} {'hold t':>7} {'hold n':>7}")
    print("  " + "-" * 118)
    keep = []
    for sd, sh, bh, (side, entry, band, exitat, path) in rows[:30]:
        lab = (f"{side}/T-{entry}/{100*band[0]:.0f}-{100*band[1]:.0f}c/"
               f"exit {exitat}/{path}")
        mark = ""
        if sh["mean"] > 0 and sh["t"] > 2.0:
            b2, w2 = robust(bh)
            if b2 > 0 and w2 > 0:
                mark = "  <== SURVIVES"
                keep.append((sd, sh, bh, lab))
            else:
                mark = "  (hold+, fails robustness)"
        elif sh["mean"] > 0:
            mark = "  (hold+, t<2)"
        print(f"  {lab:<44} {sd['mean']:>+10.3f} {sd['pct']:>+6.2f}% "
              f"{sd['edge']:>+8.2f} {sd['t']:>+7.1f} {sh['mean']:>+11.3f} "
              f"{sh['t']:>+7.1f} {sh['n']:>7}{mark}")

    print("\n" + "=" * 122)
    if not keep:
        print("NOTHING RICHER SURVIVED THE HOLDOUT. The frozen 80-96c/T-9")
        print("rule at +$0.189 remains the only candidate, and the way to")
        print("make more money from it is SIZE, not a narrower rule:")
        print("  $10 a bet  -> +$0.19 a bet")
        print("  $50 a bet  -> +$0.95 a bet, same edge, same statistics")
        print("  $100 a bet -> +$1.89 a bet")
        print("A narrower rule with a bigger number is usually a smaller")
        print("sample wearing a costume. Sizing carries no such risk -- only")
        print("the risk that the edge is not real, which is what the frozen")
        print("spec is there to settle.")
    else:
        print(f"{len(keep)} VARIANT(S) SURVIVED -- each needs its own freeze")
        for sd, sh, bh, lab in keep:
            print(f"\n  {lab}")
            print(f"    discovery n={sd['n']:<6} ${sd['mean']:+.3f}/bet "
                  f"({sd['pct']:+.2f}% of stake, edge {sd['edge']:+.2f}pp)")
            print(f"    HOLDOUT   n={sh['n']:<6} ${sh['mean']:+.3f}/bet "
                  f"({sh['pct']:+.2f}% of stake, edge {sh['edge']:+.2f}pp)")
            b2, w2 = robust(bh)
            print(f"    holdout minus two best ${b2:+.2f}, "
                  f"minus two worst ${w2:+.2f}")
    print("=" * 122)
    print("Nothing here touches specs/expensive_favourite.md, which stays")
    print("frozen and is scored only on data collected after 2026-10-05.")
    print("=" * 122)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Is T-9 the right moment to buy, or just the one that got picked?

The live rule reads the book nine minutes before the window closes. The
collector records the quote at six offsets -- 14, 12, 9, 6, 3 and 1 minutes
out -- so the same rule can be scored at each and compared.

TWO THINGS MAKE THIS LESS STRAIGHTFORWARD THAN IT LOOKS, and both are stated
beside the numbers rather than buried:

  IT IS NOT THE SAME BETS AT DIFFERENT TIMES. The rule takes whatever sits in
  the 90-96c band at that instant. Early in a window few markets have picked a
  clear favourite; late, many have, and some have gone past 96c and out of the
  band entirely. So each offset scores a DIFFERENT SET of markets, and a
  column with more bets is not the same column with better timing. The counts
  are printed for exactly this reason.

  T-1 IS NOT A CANDIDATE AND IS SHOWN ONLY AS A CONTROL. One minute from
  settlement the price is nearly the answer, so a high win rate there means
  the market has already resolved in all but name. Any apparent edge is the
  spread on a near-certainty, and a rule that enters there has almost no time
  to be wrong -- which is a different strategy, not a better entry.

AND THE OBVIOUS TRAP. T-9 was itself chosen by searching this data. If some
other offset looks better here, that is the same search finding the same kind
of lucky spot, and the honest response is a frozen forward test, not a change
to a live rule.

Read-only.
"""

import collections
import math
import sys

import predictor as P
import score as S


TABS = ("M15H", "M15")
BAND = (0.90, 0.96)
OFFSETS = (14, 12, 9, 6, 3, 1)
LIVE = 9
STAKE = 4.0
MAJORS = set(S.MAJORS)


def _f(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except Exception:
        return None


def fee(n, p):
    if n <= 0 or not (0 < p < 1):
        return 0.0
    return math.ceil(0.07 * n * p * (1 - p) * 100) / 100.0


def load(tab):
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(tab).get_all_values()
    if len(rows) < 2:
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        if str(c(r, "Series")) not in MAJORS:
            continue
        res = str(c(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        q = {}
        for off in OFFSETS:
            b, a = _f(c(r, f"bid{off}")), _f(c(r, f"ask{off}"))
            # Both sides or neither. A missing quote is no reading.
            q[off] = (b, a) if (b is not None and a is not None
                                and 0 < b <= a < 1) else None
        out.append(dict(ct=str(c(r, "Close Time")), series=str(c(r, "Series")),
                        q=q, yes=(res == "yes")))
    return out


def arm(rows, off):
    out = []
    for m in rows:
        qq = m["q"].get(off)
        if not qq:
            continue
        px = qq[1]                       # the live rule pays the ask
        if not (BAND[0] <= px < BAND[1]):
            continue
        n = int(STAKE // px)
        if n <= 0:
            continue
        cost = n * px + fee(n, px)
        out.append(dict(ct=m["ct"], px=px, cost=cost, won=m["yes"],
                        pnl=(n * 1.0 - cost) if m["yes"] else -cost))
    return out


def clustered_t(rows):
    n = len(rows)
    if n < 3:
        return 0.0
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["pnl"])
    G = len(g)
    if G < 3:
        return 0.0
    m = sum(r["pnl"] for r in rows) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    se = math.sqrt((G / (G - 1.0)) * ss / (n * n))
    return (m / se) if se > 0 else 0.0


def main():
    print("=" * 94)
    print(f"WHEN TO BUY   {100*BAND[0]:.0f}-{100*BAND[1]:.0f}c YES, "
          f"${STAKE:.0f} a bet, held to settlement")
    print("  Each offset scores a DIFFERENT set of markets -- whatever is in "
          "the band at that moment.")
    print("=" * 94)

    mk, seen = [], set()
    for tab in TABS:
        try:
            got = load(tab)
        except Exception as e:
            print(f"  {tab}: unreadable ({str(e)[:70]})")
            continue
        print(f"  {tab}: {len(got)} graded major markets")
        mk.extend(got)
    uniq = []
    for m in mk:
        k = (m["ct"], m["series"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    print(f"  {len(uniq)} distinct markets\n")

    print(f"  {'entry':>6} {'bets':>6} {'won':>7} {'needed':>8} "
          f"{'net':>11} {'per $':>8} {'$/bet':>8} {'t':>7}")
    best = None
    for off in OFFSETS:
        rows = arm(uniq, off)
        if not rows:
            print(f"  T-{off:<4} {'no data':>6}")
            continue
        n = len(rows)
        won = sum(1 for r in rows if r["won"])
        cost = sum(r["cost"] for r in rows)
        pnl = sum(r["pnl"] for r in rows)
        need = 100 * sum(r["px"] for r in rows) / n
        per = 100 * pnl / cost
        t = clustered_t(rows)
        tag = "  <- live" if off == LIVE else ("  <- control" if off == 1 else "")
        print(f"  T-{off:<4} {n:>6} {100*won/n:>6.1f}% {need:>7.1f}% "
              f"${pnl:>+10.2f} {per:>+7.2f}% ${pnl/n:>+7.3f} {t:>+6.2f}{tag}")
        if off != 1 and (best is None or per > best[1]):
            best = (off, per, t, n)

    print("\n" + "=" * 94)
    if best:
        print(f"  Best per dollar, excluding the T-1 control: T-{best[0]} "
              f"at {best[1]:+.2f}% (t = {best[2]:+.2f}, {best[3]} bets)")
        if best[0] == LIVE:
            print("  That is the offset already running. Nothing to change.")
        else:
            print(f"  T-{LIVE} is what runs live. BEFORE MOVING ANYTHING: T-{LIVE}")
            print("  was itself chosen by searching this same data, so a better")
            print("  column here is the same search finding another lucky spot.")
            print("  The honest next step is a frozen forward test of the new")
            print("  offset, not an edit to a live rule.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Is the 90-96c NO-side favourite the same bet as the YES-side one?

THE QUESTION. The live rule buys YES when YES is the 90-96c favourite. When
the FAVOURITE happens to be the NO side -- NO quoted at 92c, YES at 8c -- the
bot skips it. That is not the opposite trade; it is the same trade wearing the
other label, and skipping it discards roughly half the qualifying favourites.

WHY IT MIGHT STILL NOT BE THE SAME, which is the whole point of measuring
rather than assuming. Buying NO costs 1 - yes_bid, so entry crosses the spread
on the CHEAP side of the book. At 92c NO the YES side is quoted near 8c, where
a one-cent spread is proportionally enormous. Two bets can be identical in
probability and quite different in what they cost to get into.

So this prices both sides the way each would actually be entered:

  YES side   pay the yes ASK                 (the live rule)
  NO side    pay 1 - yes BID                 (crossing the cheap side)

and reports them separately over the same markets and the same window, with
the spread paid on each stated beside the result. Nothing here is a strategy
proposal; it answers one factual question.

Read-only.
"""

import collections
import math
import sys

import predictor as P
import score as S


TABS = ("M15H", "M15")
BAND = (0.90, 0.96)
ENTRY = S.ENTRY
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
        bid, ask = _f(c(r, f"bid{ENTRY}")), _f(c(r, f"ask{ENTRY}"))
        # Both sides or neither: a missing quote is no reading, and treating
        # it as a price is how this project has manufactured findings before.
        if bid is None or ask is None or not (0 < bid <= ask < 1):
            continue
        out.append(dict(ct=str(c(r, "Close Time")), series=str(c(r, "Series")),
                        bid=bid, ask=ask, yes=(res == "yes")))
    return out


def arm(rows, side):
    """Price one side's favourite bets, as that side would really be entered."""
    out = []
    for m in rows:
        if side == "YES":
            px = m["ask"]                 # pay the ask
            spread = m["ask"] - m["bid"]
            won = m["yes"]
        else:
            px = 1.0 - m["bid"]           # buying NO means selling YES
            spread = m["ask"] - m["bid"]
            won = not m["yes"]
        if not (BAND[0] <= px < BAND[1]):
            continue
        n = int(STAKE // px)
        if n <= 0:
            continue
        cost = n * px + fee(n, px)
        out.append(dict(ct=m["ct"], px=px, spread=spread, n=n, cost=cost,
                        won=won, pnl=(n * 1.0 - cost) if won else -cost))
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


def wilson(k, n):
    if not n:
        return 0.0, 1.0
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def report(label, rows):
    n = len(rows)
    if not n:
        print(f"  {label:<26} nothing qualified")
        return None
    won = sum(1 for r in rows if r["won"])
    cost = sum(r["cost"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    avg = sum(r["px"] for r in rows) / n
    spr = sum(r["spread"] for r in rows) / n
    lo, hi = wilson(won, n)
    # BREAK-EVEN IS THE PRICE PAID. A win rate is meaningless without it, and
    # the two sides pay different prices, so they need different bars.
    print(f"  {label:<26} {n:>5} bets   won {100*won/n:>5.1f}% "
          f"({100*lo:.1f}-{100*hi:.1f}%)  vs {100*avg:>4.1f}% needed")
    print(f"  {'':26} net ${pnl:>+9.2f} on ${cost:>9,.0f}  "
          f"= {100*pnl/cost:>+6.2f}%   ${pnl/n:>+6.3f}/bet   t = {clustered_t(rows):+.2f}")
    print(f"  {'':26} average entry {100*avg:.1f}c, "
          f"average spread at entry {100*spr:.2f}c")
    return dict(n=n, won=won, pnl=pnl, cost=cost, avg=avg, spread=spr,
                t=clustered_t(rows))


def main():
    print("=" * 92)
    print(f"THE SAME FAVOURITE, BOUGHT FROM EITHER SIDE   "
          f"{100*BAND[0]:.0f}-{100*BAND[1]:.0f}c at T-{ENTRY}, ${STAKE:.0f} a bet")
    print("  YES: pay the ask.   NO: pay 1 - the bid, crossing the cheap side.")
    print("=" * 92)

    mk, seen = [], set()
    for tab in TABS:
        try:
            got = load(tab)
        except Exception as e:
            print(f"  {tab}: unreadable ({str(e)[:70]})")
            continue
        print(f"  {tab}: {len(got)} graded major markets")
        mk.extend(got)
    # The two tabs overlap; a market counted twice halves its standard error
    # for free.
    uniq = []
    for m in mk:
        k = (m["ct"], m["series"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    print(f"  {len(uniq)} distinct markets\n")

    y = report("YES favourite (live rule)", arm(uniq, "YES"))
    print()
    n = report("NO favourite (not run)", arm(uniq, "NO"))

    print("\n" + "=" * 92)
    if not y or not n:
        print("  NOT ENOUGH DATA on one side to compare.")
        return 0
    dy = 100 * y["pnl"] / y["cost"]
    dn = 100 * n["pnl"] / n["cost"]
    print(f"  per dollar staked:  YES {dy:+.2f}%   NO {dn:+.2f}%   "
          f"gap {dn - dy:+.2f} points")
    print(f"  spread paid at entry:  YES {100*y['spread']:.2f}c   "
          f"NO {100*n['spread']:.2f}c")
    print(f"  bets available:  YES {y['n']}   NO {n['n']}   "
          f"-- adding NO would change the bet rate by {100*n['n']/y['n']:.0f}%")
    print()
    if dn >= dy - 0.5:
        print("  The NO side looks like the same bet. That is an argument for a")
        print("  SECOND FROZEN SPEC, scored forward on paper -- not for adding it")
        print("  to a live rule whose own spec says YES only.")
    else:
        print("  The NO side is worse per dollar. The likely reason is above:")
        print("  entry crosses the spread on the cheap side of the book.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

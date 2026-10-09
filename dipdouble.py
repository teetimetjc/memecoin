"""Does a fallen favourite come back? Scores specs/dipdouble.md.

The hypothesis came from fourteen live bets, eleven of which fell below 50c
and recovered. That is 78.6% where break-even at 50c is 50%, and it would be
the most profitable thing in this project if it were real. dip.py already
measured the generic version at 44% recovery and -$2.21 a bet, so the two
numbers disagree by a factor that no amount of hoping will reconcile.

THE CONTROL ARM IS THE TEST. A recovery rate on fallen favourites means
nothing by itself: if every dip in these markets recovers 70% of the time,
then favourites recovering 70% of the time says nothing about favourites.
Only the GAP between fallen favourites and fallen non-favourites, measured
in the same data over the same period, tests the claim. Reporting the
favourite arm alone would produce a confident number answering a question
nobody asked -- which is the failure mode this project keeps finding.

EVERY THRESHOLD IS A CONSTANT HERE AND A LINE IN THE SPEC. There are no
arguments and no environment switches. A pre-registered test whose operator
can adjust it on seeing the result is not one.

Read-only.
"""

import collections
import math
import sys

import predictor as P



HIST = "M15H"
TAB = "M15"
# The live rule's band, unchanged.
FAV_LO, FAV_HI = 0.90, 0.96
# A dip is the YES BID at or below this. The bid is what the market would
# actually pay you -- the mid is a price nobody was offering.
DIP = 0.50
# Offsets at which a dip may be detected AND acted on, in order. T-1 is
# excluded: one minute cannot rebound, and the offset nearest settlement is
# where leakage of the answer into the price would live.
TRIGGERS = (6, 3)
STAKE = 4.0
# Spec pass conditions, fixed 2026-10-09.
MIN_N = 300
MIN_RECOVERY = 0.60
MIN_T = 2.5
MIN_GAP = 0.10


def _f(v):
    try:
        x = float(str(v).strip())
        return x
    except Exception:
        return None


def wilson(k, n):
    """Wilson, not the normal approximation: at these rates and sample sizes
    the textbook interval runs past 1.0 and understates the uncertainty,
    which is the entire quantity in dispute here."""
    if not n:
        return 0.0, 1.0
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def fee(n, p):
    if n <= 0 or not (0 < p < 1):
        return 0.0
    return math.ceil(0.07 * n * p * (1 - p) * 100) / 100.0


def load(tab):
    """Every graded 15-minute market with a usable quote ladder."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(tab).get_all_values()
    if len(rows) < 2:
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def col(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        res = str(col(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        a9 = _f(col(r, "ask9"))
        if a9 is None or not (0 < a9 < 1):
            continue
        leg = []
        for off in TRIGGERS:
            b, a = _f(col(r, f"bid{off}")), _f(col(r, f"ask{off}"))
            # BOTH SIDES OR NEITHER. A missing bid is an empty book, not a
            # price of zero -- reading it as zero is what made path.py report
            # 25 of 42 winners crashing to single digits before it was fixed.
            if b is None or a is None or not (0 < b <= a < 1):
                leg.append(None)
                continue
            leg.append((b, a))
        out.append(dict(ct=str(col(r, "Close Time")), series=str(col(r, "Series")),
                        ask9=a9, legs=leg, yes=(res == "yes")))
    return out


def dips(mkts, favourite):
    """Markets matching the arm, that then dipped, with the leg-2 trade."""
    out = []
    for m in mkts:
        is_fav = FAV_LO <= m["ask9"] < FAV_HI
        if is_fav != favourite:
            continue
        for q in m["legs"]:
            if q is None:
                continue
            bid, ask = q
            if bid > DIP:
                continue
            # ACTED ON AT THE ASK, AT THE OFFSET THE DIP WAS VISIBLE. Buying
            # at the dip's low would be buying the bottom, which nobody can
            # do; buying at the ask at that same moment is a trade that
            # actually existed.
            n = int(STAKE // ask) if ask > 0 else 0
            if n <= 0:
                continue
            cost = n * ask + fee(n, ask)
            out.append(dict(ct=m["ct"], series=m["series"], entry=ask,
                            n=n, cost=cost,
                            pnl=(n * 1.0 - cost) if m["yes"] else -cost,
                            yes=m["yes"]))
            break           # the FIRST dip only; one double-down per market
    return out


def clustered_t(rows):
    """t on mean P&L per bet, clustered by close-time.

    Markets closing at the same instant share the move that decides them, so
    treating them as independent overstates significance -- by a lot, since
    the five majors move together.
    """
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


def drop_extremes(rows):
    """Without the two best and two worst close-times."""
    g = collections.defaultdict(float)
    for r in rows:
        g[r["ct"]] += r["pnl"]
    order = sorted(g, key=lambda k: g[k])
    bad = set(order[:2]) | set(order[-2:])
    return [r for r in rows if r["ct"] not in bad]


def arm(rows, label):
    n = len(rows)
    if not n:
        print(f"  {label:<26} no qualifying dips")
        return None
    won = sum(1 for r in rows if r["yes"])
    staked = sum(r["cost"] for r in rows)
    net = sum(r["pnl"] for r in rows)
    rec = won / n
    lo, hi = wilson(won, n)
    print(f"  {label:<26} {n:>6} dips   recovered {100*rec:>5.1f}% "
          f"({100*lo:.1f}-{100*hi:.1f}%)   avg entry {100*staked/sum(r['n'] for r in rows):.0f}c")
    print(f"  {'':26} net ${net:>+8.2f} on ${staked:,.0f} staked "
          f"= {100*net/staked:+.2f}%   ${net/n:+.3f}/bet   t = {clustered_t(rows):+.2f}")
    return dict(n=n, rec=rec, net=net, staked=staked, t=clustered_t(rows))


def main():
    print("=" * 90)
    print("DOES A FALLEN FAVOURITE COME BACK?   specs/dipdouble.md, frozen 2026-10-09")
    print(f"favourite = ask9 in {100*FAV_LO:.0f}-{100*FAV_HI:.0f}c   "
          f"dip = bid <= {100*DIP:.0f}c at T-6 or T-3   "
          f"then ${STAKE:.0f} at that ask, held")
    print("=" * 90)

    mkts = []
    for tab in (HIST, TAB):
        try:
            got = load(tab)
        except Exception as e:
            print(f"  {tab}: unreadable ({str(e)[:80]})")
            continue
        print(f"  {tab}: {len(got)} graded markets")
        mkts.extend(got)
    if not mkts:
        print("\n  NO DATA.")
        return 0

    # One row per (close time, series): the two tabs overlap, and a market
    # counted twice would halve the standard error for free.
    uniq, seen = [], set()
    for m in mkts:
        k = (m["ct"], m["series"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(m)
    print(f"  {len(uniq)} distinct markets after de-duplication\n")

    fav = dips(uniq, True)
    ctl = dips(uniq, False)
    a = arm(fav, "FALLEN FAVOURITES")
    print()
    b = arm(ctl, "control: everything else")

    print("\n" + "=" * 90)
    if not a:
        print("  VERDICT: NOT ENOUGH DATA YET -- no qualifying dips in the"
              " favourite arm.")
        return 0

    gap = a["rec"] - (b["rec"] if b else 0.0)
    trimmed = drop_extremes(fav)
    t_trim = clustered_t(trimmed)
    net_trim = sum(r["pnl"] for r in trimmed)

    checks = [
        (f"at least {MIN_N} dips", a["n"] >= MIN_N, f"{a['n']}"),
        (f"recovery above {100*MIN_RECOVERY:.0f}%", a["rec"] > MIN_RECOVERY,
         f"{100*a['rec']:.1f}%"),
        ("net above zero", a["net"] > 0, f"${a['net']:+.2f}"),
        (f"clustered t above {MIN_T}", a["t"] > MIN_T, f"{a['t']:+.2f}"),
        (f"beats control by {100*MIN_GAP:.0f}pt", gap >= MIN_GAP,
         f"{100*gap:+.1f}pt"),
        ("survives trimming", net_trim > 0 and t_trim > MIN_T,
         f"${net_trim:+.2f}, t={t_trim:+.2f}"),
    ]
    for name, ok, val in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<28} {val}")

    print()
    if all(ok for _, ok, _ in checks):
        print("  VERDICT: PASSES. Worth a forward paper test -- and ONLY that.")
        print("  It is still a second order on one market, it still cannot buy")
        print("  the bottom in real life, and both sides of these markets still")
        print("  lose about 25% of stake on the round trip.")
    else:
        print("  VERDICT: FAILS. The live sample's 78.6% was fourteen bets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Score the frozen fallen-favourite spec on data it has never seen.

specs/fallen_favourite.md fixes the rule, the entry, the stake, the fee and
the four conditions it must clear. This file only executes it. It takes no
arguments, offers no thresholds to adjust and prints one verdict, because
the point of a pre-registered test is that the person running it cannot
help it along.

THE CUTOFF IS LOAD-BEARING. Only markets closing after the freeze count.
Everything earlier is the discovery set, and a discovery set cannot confirm
what it suggested -- that is the single mistake underneath most of the
twelve dead strategies here.

Read-only. Writes nothing, sends nothing.
"""

import collections
import math
import sys

import predictor as P

SHEET = "M15"
FREEZE = "2026-09-25T00:00:00Z"      # markets closing at or before this are discovery
ENTRY = 3                            # minutes before close
EARLIER = (14, 12, 9, 6)
WAS_FAV = 0.60
NOW_DOG = 0.50
STAKE = 10.0

MIN_CLUSTERS = 400
MIN_T = 2.0
MIN_PROFITABLE_SHARE = 0.45


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def pnl(price, won):
    c = int(STAKE / price)
    if c < 1:
        return None
    fee = math.ceil(round(0.07 * c * price * (1 - price), 9) * 100) / 100.0
    return (c if won else 0) - (c * price + fee)


def clustered_se(groups, n):
    """Windows are the unit, not bets. Six samples of one market are one
    market, and every series closing at the same minute shares a move."""
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(SHEET).get_all_values()
    if len(rows) < 2:
        print("M15 is empty")
        return 1
    hi = {h: i for i, h in enumerate(rows[0])}

    def cell(r, k):
        i = hi.get(k)
        return r[i] if i is not None and i < len(r) else ""

    disc = held = 0
    bets = []
    for r in rows[1:]:
        res = str(cell(r, "Result")).lower().strip()
        close = str(cell(r, "Close Time")).strip()
        if res not in ("yes", "no") or not close:
            continue
        if close <= FREEZE:
            disc += 1
            continue
        held += 1
        b3, a3 = _f(cell(r, "bid3")), _f(cell(r, "ask3"))
        for side in ("YES", "NO"):
            if side == "YES":
                pay = a3
            else:
                pay = (1 - b3) if b3 is not None else None
            if pay is None or not (0.02 < pay < NOW_DOG):
                continue
            peak = None
            for o in EARLIER:
                b, a = _f(cell(r, f"bid{o}")), _f(cell(r, f"ask{o}"))
                if b is None or a is None:
                    continue
                mid = (b + a) / 2.0
                if side == "NO":
                    mid = 1 - mid
                peak = mid if peak is None else max(peak, mid)
            if peak is None or peak < WAS_FAV:
                continue
            won = (res == "yes") if side == "YES" else (res == "no")
            v = pnl(pay, won)
            if v is not None:
                bets.append((v, close, pay, won))

    print("=" * 74)
    print("RETEST: the fallen favourite, on held-out data only")
    print("=" * 74)
    print(f"  spec frozen at   {FREEZE}")
    print(f"  discovery markets skipped   {disc}")
    print(f"  held-out markets available  {held}")
    if not bets:
        print("\n  no qualifying bets yet -- nothing to score")
        return 0

    g = collections.defaultdict(list)
    for v, close, _, _ in bets:
        g[close].append(v)
    n = len(bets)
    mean = sum(v for v, _, _, _ in bets) / n
    se = clustered_se(g, n)
    t = (mean / se) if se else 0.0
    wins = sum(1 for _, _, _, w in bets if w)
    paid = sum(p for _, _, p, _ in bets) / n
    by = {k: sum(v) for k, v in g.items()}
    order = sorted(by.values(), reverse=True)
    minus2 = sum(order[2:])
    share = sum(1 for v in by.values() if v > 0) / len(by)

    print(f"\n  qualifying bets   {n}")
    print(f"  close-times       {len(by)}")
    print(f"  paid              {100*paid:.1f}%")
    print(f"  won               {100*wins/n:.1f}%")
    print(f"  profit per bet    {mean:+.2f} +/- {se or 0:.2f}   t = {t:+.1f}")
    print(f"  total             {sum(by.values()):+.2f}")
    print(f"  without best two  {minus2:+.2f}")
    print(f"  profitable times  {sum(1 for v in by.values() if v>0)}/{len(by)}"
          f"  ({100*share:.0f}%)")

    checks = [
        (len(by) >= MIN_CLUSTERS, f"at least {MIN_CLUSTERS} close-times"),
        (mean > 0, "profit per bet above zero"),
        (t > MIN_T, f"clustered t above {MIN_T}"),
        (minus2 > 0, "still positive without the best two close-times"),
        (share > MIN_PROFITABLE_SHARE,
         f"over {100*MIN_PROFITABLE_SHARE:.0f}% of close-times profitable"),
    ]
    print("\n  " + "-" * 70)
    for ok, name in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {name}")
    if not checks[0][0]:
        print("\n  VERDICT: NOT ENOUGH DATA YET. Not a failure -- come back later.")
        return 0
    print(f"\n  VERDICT: {'PASSES' if all(c[0] for c in checks) else 'FAILS'}"
          " the pre-registered conditions")
    print("  A fail is final for this spec. Re-cutting the thresholds and")
    print("  trying again would make this discovery data, not a test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

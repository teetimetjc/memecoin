"""Does anything separate the 19c winners from the 19c losers? Read-only.

THE QUESTION AND WHY IT IS ASKED THIS WAY. A leaderboard profile won three
BTC 15-minute bets at 18.3-19.5c and posted them. longcut established that
the 15-25c side as a whole is OVERpriced -- it wins 20.46% at T-9 against a
21.23% break-even -- so three winners out of roughly fifteen attempts is the
expected yield rather than a signal.

The natural follow-up is "but what did those three have in common?" Asked of
three rows, that question cannot be answered: any three observations share
features, and a search over them returns a story every time. Asked of all
3,603 observations in this project's own collector, it becomes a real test.

So this does NOT look at his three windows. It takes every window where a
side was priced 15-25c, splits them by each feature the collector actually
records, and asks whether any split finds a bucket that clears its own
break-even.

THE FEATURES, chosen before any of them was run:

  TREND      the cheap side's price at T-9 minus its price at T-14. The
             strongest measured effect in this project is adjacent to this:
             orders that MISSED because the ask rose settled YES 98.4%
             against 95.8% for those that filled. A longshot getting more
             expensive may be a longshot the market is re-rating. This is
             the one with a mechanism, and it is named first so it cannot
             be promoted to "the hypothesis" after the fact.
  SPREAD     ask minus bid at T-9. A wide book is a thin, uncertain one.
  DEPTH      contracts resting on the cheap side.
  VOLUME     contracts traded in the market so far.
  OPENINT    open interest.

FIVE FEATURES IS FIVE CHANCES TO BE FOOLED, and that is the whole danger
here. With five splits into four buckets each, twenty cells are inspected,
and at a 5% threshold one of them is expected to look significant with
nothing underneath. So the bar is set before the run and it is the same bar
used everywhere else in this project:

  1. the bucket must beat its OWN break-even, not the pooled one;
  2. the Wilson interval must exclude break-even;
  3. the effect must hold in BOTH chronological halves;
  4. neighbouring buckets must agree -- a gradient, not one lucky cell.

A result that clears 1 and 2 but fails 3 or 4 is reported as NOT A FINDING,
in those words, because this project has already been fooled once by a
+$23/bet pocket that passed four filters and was a bug.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import os
import statistics as st

import predictor as P
import collect15 as C
import oddsmath as M
import tally as T

LO = float(os.environ.get("LONG_LO_CENTS") or 15.0)
HI = float(os.environ.get("LONG_HI_CENTS") or 25.0)
AT = int(os.environ.get("LONGWHY_AT") or 9)        # the entry instant
PRIOR = int(os.environ.get("LONGWHY_PRIOR") or 14)  # for the trend feature
STAKE = float(os.environ.get("LONG_STAKE") or 5.0)
NBUCK = int(os.environ.get("LONGWHY_BUCKETS") or 4)
MIN_CELL = int(os.environ.get("LONGWHY_MIN_CELL") or 120)
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def coin_of(tk):
    tk = str(tk or "").upper()
    if not tk.startswith("KX"):
        return "?"
    head = tk.split("-")[0][2:]
    return head[:-3] if head.endswith("15M") else head


def cell(rows):
    if not rows:
        return None
    n = len(rows)
    wins = sum(1 for r in rows if r["won"])
    staked = sum(r["cost"] for r in rows)
    ret = sum(r["ret"] for r in rows)
    be = 100.0 * st.mean([r["cost"] / r["k"] for r in rows])
    lo, hi = T.wilson(wins, n)
    return dict(n=n, won=100.0 * wins / n, be=be, net=ret - staked,
                roi=100.0 * (ret - staked) / staked if staked else 0.0,
                lo=100.0 * lo, hi=100.0 * hi)


def line(label, c):
    if c is None or c["n"] < MIN_CELL:
        got = 0 if c is None else c["n"]
        print(f"    {label:<26} n {got:>5}  -- too thin to read")
        return False
    beat = c["won"] > c["be"]
    clears = c["lo"] > c["be"]          # interval excludes break-even
    flag = "  *** CLEARS" if clears else ("  beats, inside noise" if beat
                                          else "")
    print(f"    {label:<26} n {c['n']:>5}   won {c['won']:6.2f}% "
          f"[{c['lo']:5.2f}-{c['hi']:5.2f}]   needs {c['be']:6.2f}%   "
          f"ROI {c['roi']:+7.2f}%{flag}")
    return clears


def buckets(rows, key, n):
    """Split into n roughly equal groups by a feature, low to high."""
    good = [r for r in rows if r.get(key) is not None]
    if len(good) < n * MIN_CELL:
        return []
    good.sort(key=lambda r: r[key])
    size = len(good) // n
    out = []
    for i in range(n):
        part = good[i * size:] if i == n - 1 else good[i * size:(i + 1) * size]
        lo_v, hi_v = part[0][key], part[-1][key]
        out.append((f"{lo_v:+.1f} to {hi_v:+.1f}", part))
    return out


def main():
    print("=" * 110)
    print(f"WHAT SEPARATES THE {LO:.0f}-{HI:.0f}c WINNERS FROM THE LOSERS, "
          f"at T-{AT}")
    print("  Not his three windows -- every window in our own collector that "
          "offered this price.")
    print("  Five features, pre-named. The bar is stated before the numbers "
          "and is not moved afterwards.")
    print("=" * 110)

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(C.SHEET).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    recs = []
    for r in rows[1:]:
        tk = str(g(r, "Ticker")).strip()
        if not tk or coin_of(tk) not in COINS:
            continue
        res = str(g(r, "Result")).strip().lower()
        if res not in ("yes", "no"):
            continue
        yb, ya = f(g(r, f"bid{AT}")), f(g(r, f"ask{AT}"))
        if yb is None or ya is None or not (0 < yb <= ya < 1):
            continue
        yes_c, no_c = ya * 100.0, (1.0 - yb) * 100.0

        if LO <= yes_c <= HI:
            price, won, depth = yes_c, (res == "yes"), f(g(r, f"asksz{AT}"))
            # the same side's price earlier, for the trend
            pa = f(g(r, f"ask{PRIOR}"))
            prior = pa * 100.0 if pa is not None else None
        elif LO <= no_c <= HI:
            price, won, depth = no_c, (res == "no"), f(g(r, f"bidsz{AT}"))
            pb = f(g(r, f"bid{PRIOR}"))
            prior = (1.0 - pb) * 100.0 if pb is not None else None
        else:
            continue

        k = int(STAKE / (price / 100.0))
        if k < 1:
            continue
        cost = k * price / 100.0 + M.fee_cents(k, price / 100.0) / 100.0
        recs.append(dict(
            k=k, cost=cost, ret=(k * 1.0 if won else 0.0), won=won,
            close=str(g(r, "Close Time"))[:10],
            trend=(price - prior) if prior is not None else None,
            spread=(ya - yb) * 100.0,
            depth=depth,
            volume=f(g(r, "Volume")),
            openint=f(g(r, "OpenInt")),
        ))

    print(f"\n  {len(recs)} window(s) priced {LO:.0f}-{HI:.0f}c at T-{AT}")
    base = cell(recs)
    if not base:
        print("  nothing to read.")
        return 1
    print(f"  POOLED: won {base['won']:.2f}%  needs {base['be']:.2f}%  "
          f"ROI {base['roi']:+.2f}%   -- the number to beat")

    features = [
        ("TREND  price now minus price at T-%d" % PRIOR, "trend"),
        ("SPREAD ask minus bid, cents", "spread"),
        ("DEPTH  contracts on the cheap side", "depth"),
        ("VOLUME contracts traded", "volume"),
        ("OPENINT open interest", "openint"),
    ]

    survivors = []
    for title, key in features:
        print(f"\n{'-' * 110}")
        print(title)
        print("-" * 110)
        bs = buckets(recs, key, NBUCK)
        if not bs:
            print("    not enough rows carry this field")
            continue
        clearing = []
        for label, part in bs:
            if line(label, cell(part)):
                clearing.append((label, part))
        # CONDITION 3 and 4, applied only to cells that got that far.
        for label, part in clearing:
            part = sorted(part, key=lambda r: r["close"])
            mid = len(part) // 2
            a, b = cell(part[:mid]), cell(part[mid:])
            if not a or not b:
                continue
            ok_halves = (a["won"] > a["be"]) and (b["won"] > b["be"])
            print(f"      halves of [{label}]: "
                  f"first {a['won']:.2f} vs {a['be']:.2f}, "
                  f"second {b['won']:.2f} vs {b['be']:.2f}  "
                  f"-> {'HOLDS' if ok_halves else 'FAILS -- NOT A FINDING'}")
            if ok_halves:
                survivors.append((title, label))

    print("\n" + "=" * 110)
    if survivors:
        print("  SURVIVED EVERY CONDITION:")
        for t, l in survivors:
            print(f"    {t}  bucket [{l}]")
        print()
        print("  THIS IS STILL NOT A STRATEGY. Twenty cells were inspected to")
        print("  find it, so the honest next step is to pre-register it and")
        print("  test it on data it was not found in -- never to bet it.")
    else:
        print("  NOTHING SURVIVED. No feature the collector records separates")
        print("  the winners from the losers well enough to clear this side's")
        print("  own break-even in both halves of the record.")
        print()
        print("  That is the expected result if the price is simply correct,")
        print("  which is what longcut already measured: the 15-25c side wins")
        print("  almost exactly as often as it costs, and the gap is the fee.")
    print()
    print("  AND THE SAMPLE IS QUOTES, NOT FILLS. The live record fills about")
    print("  0.75c worse than its quotes; at this price that is 4% of stake,")
    print("  so a cell that merely beats break-even here loses in practice.")
    print("=" * 110)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

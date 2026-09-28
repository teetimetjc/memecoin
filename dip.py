"""Buy the dip and SELL the recovery -- never held to settlement.

Every test in this project so far has held to settlement: you win if the
event goes your way. This is a different trade. Buy a contract that has
fallen, sell it if the price comes back, and the outcome of the market never
matters. Only the path does.

WHAT CHANGES IN THE ARITHMETIC, and it is not in your favour:

  You cross the spread TWICE -- buy at the ask, sell at the bid -- where
  holding to settlement crosses once. On these markets the spread is 2c at
  mid prices and proportionally enormous when cheap.

  You pay the FEE TWICE, once on each trade. Kalshi charges per
  transaction, so an early exit is a second charge that a held position
  never incurs.

  But you no longer need to be right about the coin. A 25c contract that
  recovers to 40c pays you whether it eventually settles yes or no, and you
  are out before the question is answered.

THE EXIT IS THE WHOLE PROBLEM. If the price does not come back you are left
holding it, and then you DO care about settlement -- at a price you already
know went against you. Both branches are priced here: sold on recovery, or
carried to settlement when the recovery never arrives. Reporting only the
winners would describe a strategy that cannot lose, which is a sure sign of
an unpriced branch.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
STAKE = 10.0
NPERM = 200


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fee(c, price):
    return 0.07 * c * price * (1 - price)


def cse(g, n):
    G = len(g)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in g.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load(sh, name):
    rows = sh.worksheet(name).get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                path[o] = (b, a)
        if len(path) < 4:
            continue
        out.append(dict(tk=r[0], ser=str(c("Series")),
                        ct=str(c("Close Time")), yes=(res == "yes"),
                        path=path))
    return out


def trade(m, side, entry_off, drop, exit_offs):
    """One dip trade, or None if it never set up.

    Entry: the side has fallen at least `drop` from its quote at T-14.
    Exit: the first later offset whose BID is above what we paid. Failing
    that, hold to settlement -- the branch that makes this honest."""
    first = m["path"].get(14)
    here = m["path"].get(entry_off)
    if not first or not here:
        return None

    def px(q, which):
        b, a = q
        if side == "YES":
            return a if which == "ask" else b
        return (1 - b) if which == "ask" else (1 - a)

    start_ask = px(first, "ask")
    buy = px(here, "ask")
    if not (0.05 < buy < 0.95) or start_ask is None:
        return None
    if buy > start_ask - drop:
        return None                      # did not fall far enough
    c = int(STAKE / buy)
    if c < 1:
        return None
    cost = c * buy + fee(c, buy)

    for o in exit_offs:
        if o >= entry_off or o not in m["path"]:
            continue
        sell = px(m["path"][o], "bid")
        if sell is None:
            continue
        if sell > buy:                   # a real recovery, net of nothing yet
            got = c * sell - fee(c, sell)
            return dict(v=got - cost, sold=True, buy=buy, sell=sell, n=c)
    # never recovered: carry it, and the event decides
    won = m["yes"] if side == "YES" else (not m["yes"])
    return dict(v=(c if won else 0) - cost, sold=False, buy=buy,
                sell=None, n=c)


def rep(rows, label):
    if len(rows) < 100:
        print(f"  {label:<40} n={len(rows)} too few")
        return None
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["v"])
    n = len(rows)
    m = sum(r["v"] for r in rows) / n
    se = cse(g, n)
    sold = sum(1 for r in rows if r["sold"])
    days = collections.defaultdict(float)
    for r in rows:
        days[r["ct"][:10]] += r["v"]
    by = sorted(days.values(), reverse=True)
    print(f"  {label:<40} n={n:<6} sold {100*sold/n:>4.0f}%  "
          f"${m:>+6.3f}/bet +/-{se or 0:>5.3f} t={m/se if se else 0:>+6.1f}  "
          f"days+ {100*sum(1 for v in by if v>0)/len(by):>3.0f}%  "
          f"no-best2 ${sum(by[2:]):>+9.0f}  no-worst2 ${sum(by[:-2]):>+9.0f}")
    return m


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    print("=" * 118)
    print(f"BUY THE DIP, SELL THE RECOVERY -- {tab}, {len(M)} markets")
    print("=" * 118)
    print("buy at the ask, sell at the bid, fee on BOTH trades; if it never")
    print("recovers the position is carried to settlement and priced there.\n")

    best = None
    for entry_off in (9, 6):
        for drop in (0.10, 0.20, 0.30):
            rows = []
            for m in M:
                for side in ("YES", "NO"):
                    t = trade(m, side, entry_off, drop,
                              [o for o in OFFSETS if o < entry_off])
                    if t:
                        t["ct"] = m["ct"]
                        rows.append(t)
            v = rep(rows, f"enter T-{entry_off}, fell >= {100*drop:.0f}pp")
            if v is not None and (best is None or v > best[0]):
                best = (v, rows, f"T-{entry_off} drop {100*drop:.0f}pp")

    if not best:
        print("\nno configuration produced enough trades")
        return 0

    print(f"\n{'='*118}")
    print(f"THE BEST CONFIGURATION: {best[2]}   ${best[0]:+.3f}/bet")
    print("=" * 118)
    rows = best[1]
    s = [r for r in rows if r["sold"]]
    h = [r for r in rows if not r["sold"]]
    if s:
        print(f"  sold on recovery   n={len(s):<6} "
              f"${sum(r['v'] for r in s)/len(s):>+6.3f}/bet   "
              f"avg buy {100*st.mean([r['buy'] for r in s]):>4.1f}c -> sell "
              f"{100*st.mean([r['sell'] for r in s]):>4.1f}c")
    if h:
        print(f"  never recovered    n={len(h):<6} "
              f"${sum(r['v'] for r in h)/len(h):>+6.3f}/bet   "
              f"avg buy {100*st.mean([r['buy'] for r in h]):>4.1f}c, "
              f"carried to settlement")
    print("\n  The second line is the one that decides it. A dip strategy is")
    print("  a bet that the unrecovered tail is small enough to pay for.")

    # the search was over six configurations; that needs a null
    g = collections.defaultdict(list)
    for i, r in enumerate(rows):
        g[r["ct"]].append(i)
    groups = collections.defaultdict(list)
    for ct, ix in g.items():
        groups[len(ix)].append(ix)
    vals = [r["v"] for r in rows]
    rnd = random.Random(17)
    nulls = []
    for _ in range(NPERM):
        sh_v = [0.0] * len(vals)
        for size, gl in groups.items():
            order = list(range(len(gl)))
            rnd.shuffle(order)
            for a_, b_ in enumerate(order):
                src, dst = gl[b_], gl[a_]
                for k in range(size):
                    sh_v[dst[k]] = vals[src[k]]
        nulls.append(sum(sh_v) / len(sh_v))
    nulls.sort()
    beat = sum(1 for x in nulls if x >= best[0])
    print(f"\n  shuffle of this configuration: median ${st.median(nulls):+.3f}"
          f"  90th ${nulls[int(.9*NPERM)]:+.3f}   p = "
          f"{(beat+1)/(NPERM+1):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

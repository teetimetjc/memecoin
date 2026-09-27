"""Search every 1-, 2- and 3-way combination for a profitable pocket.

The question this answers is the practical one: is there a coin, a time of
day, a liquidity condition and a price level which, TOGETHER, have paid --
the sort of rule you could put a notification on. Not "does the market have
an edge" but "is there a corner of it that has one".

FEATURES ARE ALL MARKET-DERIVED, because the 43,000-market history carries
no indicators -- RSI and MACD exist only in the v6 tab, five coins and two
weeks, already searched 562 ways. What history does carry is arguably
better: the actual book, its spread, its volume, its open interest and the
shape of the path, for eight series over ten weeks.

FOUR FILTERS, AND A CANDIDATE MUST PASS ALL OF THEM.

  1. Clustered t > 2. Six samples of one market are one market.

  2. Survives deleting its two best days AND its two worst. The first
     catches a rule carried by a lucky streak -- which killed ema=BEAR and
     the fallen favourite. The second catches the opposite shape, a rule
     that wins pennies daily and gives it all back occasionally; the maker
     test showed 80% of days green while losing $1.61 a bet, and a
     best-days-only check would have waved it through.

  3. A time holdout. The search runs on the oldest 70% of days; anything it
     likes is then scored once on the newest 30%, which it never saw.

  4. A shuffle null on the WHOLE search. Thousands of combinations
     guarantee winners on noise alone, so the same search runs again with
     outcomes permuted within close-times, and the real best must beat what
     the shuffle's best manages.

Read-only. Places nothing, notifies nothing.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
ENTRY = 3
MIN_N = 200
NPERM = 200
SPLIT = 0.70
STAKE = 10.0


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


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
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
        if ENTRY not in path:
            continue
        out.append(dict(close=str(c("Close Time")), ser=str(c("Series")),
                        yes=(res == "yes"), path=path,
                        vol=_f(c("Volume")) or 0.0, oi=_f(c("OpenInt")) or 0.0))
    return out


def build(M):
    """One observation per (market, side): the bet, and its feature values."""
    obs = []
    for m in M:
        b, a = m["path"][ENTRY]
        st_ = m["path"].get(14) or m["path"].get(12)
        start = None
        if st_:
            start = (st_[0] + st_[1]) / 2.0
        try:
            hr = (int(m["close"][11:13]) - 5) % 24
        except Exception:
            continue
        for side in ("YES", "NO"):
            price = a if side == "YES" else 1 - b
            if not (0.02 < price < 0.98):
                continue
            won = m["yes"] if side == "YES" else (not m["yes"])
            v = pnl(price, won)
            if v is None:
                continue
            mid = (a + b) / 2.0
            own = mid if side == "YES" else 1 - mid
            drift = None
            if start is not None:
                drift = (own - (start if side == "YES" else 1 - start))
            obs.append(dict(v=v, ct=m["close"], price=price, won=won,
                            ser=m["ser"], hr=hr, side=side,
                            spread=a - b, vol=m["vol"], oi=m["oi"],
                            drift=drift))
    return obs


def predicates(obs):
    """Named, binned conditions. Kept deliberately coarse: a fine grid is
    how a 1,188-rule search produced a winner a shuffle then beat."""
    def med(key):
        v = sorted(o[key] for o in obs if o[key] is not None)
        return v[len(v) // 2] if v else 0.0
    ms = med("spread")
    P_ = []
    for s in sorted({o["ser"] for o in obs}):
        P_.append((f"series={s}", lambda o, s=s: o["ser"] == s))
    for lo, hi in ((0, 6), (6, 12), (12, 18), (18, 24)):
        P_.append((f"hour {lo:02d}-{hi:02d}CT",
                   lambda o, lo=lo, hi=hi: lo <= o["hr"] < hi))
    for lo, hi in ((0.02, 0.20), (0.20, 0.40), (0.40, 0.60),
                   (0.60, 0.80), (0.80, 0.98)):
        P_.append((f"price {lo:.2f}-{hi:.2f}",
                   lambda o, lo=lo, hi=hi: lo <= o["price"] < hi))
    # VOLUME AND OPEN INTEREST ARE GONE, and this is not a tuning choice.
    # In M15H both are read at backfill time, AFTER settlement. Split by
    # outcome inside the cheap bands, the volume of winners over losers runs
    # 1.49, 1.43 and 1.31 -- when a longshot comes in, the market trades
    # heavily on the way there. So "volume high" is partly a description of
    # what happened, and a pocket built on it reported +$23/bet while
    # passing a clustered t-test, a best-and-worst-day check, a shuffle null
    # at p=0.005 and an out-of-sample holdout. Four filters, all cleared, on
    # a rule nobody could ever have followed. A holdout cannot catch a
    # feature that encodes the future, because the future is in the holdout
    # too.
    #
    # Only quantities knowable AT ENTRY remain: the quote, its spread, the
    # clock, the series and the shape of the path so far. The live collector
    # reads volume during the window and can have it back later; the history
    # cannot.
    P_ += [("spread tight", lambda o: o["spread"] <= ms),
           ("spread wide", lambda o: o["spread"] > ms),
           ("side=YES", lambda o: o["side"] == "YES"),
           ("side=NO", lambda o: o["side"] == "NO"),
           ("fell hard", lambda o: o["drift"] is not None and o["drift"] < -0.20),
           ("fell", lambda o: o["drift"] is not None and -0.20 <= o["drift"] < -0.05),
           ("flat", lambda o: o["drift"] is not None and abs(o["drift"]) <= 0.05),
           ("rose", lambda o: o["drift"] is not None and 0.05 < o["drift"] <= 0.20),
           ("rose hard", lambda o: o["drift"] is not None and o["drift"] > 0.20)]
    return P_


def masks(obs, preds):
    out = []
    for name, fn in preds:
        s = frozenset(i for i, o in enumerate(obs) if fn(o))
        if len(s) >= MIN_N:
            out.append((name, s))
    return out


def combos(mk, maxway=3):
    """Every 1-, 2- and 3-way intersection with enough rows."""
    out = [(n, s) for n, s in mk]
    for i in range(len(mk)):
        for j in range(i + 1, len(mk)):
            s = mk[i][1] & mk[j][1]
            if len(s) >= MIN_N:
                out.append((f"{mk[i][0]} & {mk[j][0]}", s))
    two = out[len(mk):]
    for name, s in two:
        for n2, s2 in mk:
            if n2 in name:
                continue
            s3 = s & s2
            if len(s3) >= MIN_N:
                out.append((f"{name} & {n2}", s3))
    return out


def score(obs, idx, vals):
    g = collections.defaultdict(list)
    for i in idx:
        g[obs[i]["ct"]].append(vals[i])
    n = len(idx)
    m = sum(vals[i] for i in idx) / n
    se = cse(g, n)
    by = sorted((sum(v) for v in g.values()), reverse=True)
    return m, (m / se if se else 0.0), sum(by[2:]), sum(by[:-2]), len(by), g


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    obs = build(M)
    vals = [o["v"] for o in obs]
    print("=" * 108)
    print(f"COMBINATION SEARCH on {tab}: {len(M)} markets -> {len(obs)} bets, "
          f"{len({o['ct'] for o in obs})} close-times")
    print("=" * 108)
    mk = masks(obs, predicates(obs))
    C = combos(mk)
    print(f"conditions {len(mk)}   combinations tested {len(C)}\n")

    keep = []
    for name, idx in C:
        m, t, minus_best2, minus_worst2, days, g = score(obs, idx, vals)
        if m > 0 and t > 2.0 and minus_best2 > 0 and minus_worst2 > 0:
            keep.append((m, t, name, idx, days))
    keep.sort(reverse=True)
    print(f"combinations positive AND t>2 AND robust to best-2 AND worst-2: "
          f"{len(keep)}")
    for m, t, name, idx, days in keep[:12]:
        print(f"   ${m:>+6.2f}/bet  t={t:>+5.1f}  n={len(idx):<6} days={days:<4} {name[:64]}")
    if not keep:
        print("   none")

    # ---- the shuffle: does the best survive the search being run on noise?
    best = keep[0][0] if keep else None
    byct = collections.defaultdict(list)
    for i, o in enumerate(obs):
        byct[o["ct"]].append(i)
    groups = collections.defaultdict(list)
    for ct, ix in byct.items():
        groups[len(ix)].append(ix)
    rnd = random.Random(31)
    print(f"\nrunning {NPERM} shuffles of the whole search ...")
    nulls = []
    for _ in range(NPERM):
        sh_v = [0.0] * len(obs)
        for size, gl in groups.items():
            order = list(range(len(gl)))
            rnd.shuffle(order)
            for a_, b_ in enumerate(order):
                src, dst = gl[b_], gl[a_]
                for k in range(size):
                    sh_v[dst[k]] = vals[src[k]]
        bm = -9e9
        for name, idx in C:
            mm = sum(sh_v[i] for i in idx) / len(idx)
            if mm > bm:
                bm = mm
        nulls.append(bm)
    nulls.sort()
    print(f"  shuffle's best combination: median ${st.median(nulls):+.2f}  "
          f"90th ${nulls[int(.9 * NPERM)]:+.2f}  max ${nulls[-1]:+.2f}")
    if best is not None:
        beat = sum(1 for x in nulls if x >= best)
        print(f"  real best ${best:+.2f} -> p = {(beat + 1) / (NPERM + 1):.3f}")
    else:
        print("  no real candidate to compare")

    # ---- holdout
    days_sorted = sorted({o["ct"][:10] for o in obs})
    cut = days_sorted[int(len(days_sorted) * SPLIT)]
    tr = frozenset(i for i, o in enumerate(obs) if o["ct"][:10] < cut)
    te = frozenset(i for i, o in enumerate(obs) if o["ct"][:10] >= cut)
    print(f"\nHOLDOUT: train before {cut} ({len(tr)} bets), "
          f"test after ({len(te)} bets)")
    cand = []
    for name, idx in C:
        a = idx & tr
        if len(a) < MIN_N:
            continue
        m, t, mb, mw, days, _ = score(obs, a, vals)
        if m > 0 and t > 2.0 and mb > 0 and mw > 0:
            cand.append((m, name, idx))
    cand.sort(reverse=True)
    print(f"  candidates found in train: {len(cand)}")
    for m, name, idx in cand[:8]:
        b = idx & te
        if len(b) < 40:
            print(f"   train ${m:>+6.2f}  test: too few   {name[:56]}")
            continue
        mm, tt, _, _, _, _ = score(obs, b, vals)
        print(f"   train ${m:>+6.2f} -> test ${mm:>+6.2f} (t={tt:>+5.1f}, "
              f"n={len(b)})  {'HELD' if mm > 0 else 'died'}  {name[:50]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

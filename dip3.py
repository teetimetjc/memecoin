"""What did the 40% of dip trades that reached target have in common?

dip2 found its best configuration -- T-12 entry, a 15-point fall, the
Bollinger-oversold filter, a 1.5x target -- and it still lost $1.137 a bet,
because the target was reached 40.2% of the time and break-even needs 45.5%.
The question this asks is whether anything KNOWABLE AT ENTRY separates the
trades that rebounded from the ones that did not. If something does, and it
lifts the hit rate by more than the 5.3 points missing, the trade becomes
viable; if nothing does, the 40% is luck and there is nothing to filter on.

TWO RULES CARRY OVER AND ARE NOT NEGOTIABLE HERE.

  EVERY ATTRIBUTE MUST BE KNOWABLE AT ENTRY. No Volume, no OpenInt -- both
  are read out of M15H after settlement, and that is exactly how a +$23/bet
  pocket passed a t-test, a best-day check, a shuffle AND a holdout before
  turning out to be lookahead. The underlying's own volume spike is fine:
  it is computed from coin candles that close at or before entry. Nothing
  about the Kalshi market after T-12 is touched.

  A SPLIT IS NOT AN EDGE. Sixty-odd attributes are compared, so the best
  one is expected to look good in pure noise. The winner is therefore
  scored on money with window-clustered errors, checked with the two BEST
  close-times deleted and again with the two WORST, and run against a
  shuffle that permutes the attribute across windows. The asymmetric day
  check matters both ways: deleting the best days catches a lucky streak,
  deleting the worst catches a rule collecting pennies in front of a
  steamroller.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P
import ind_backfill as IB
import dip2 as D

# dip2's winning configuration, fixed. Re-searching it here would just be
# the same 36-way search with a second layer of splits on top.
ENTRY, DROP, FILT, MULT = 12, 0.15, "bb", 1.5
NEED = 0.455          # hit rate dip2 measured as break-even for this config
NPERM = 300
MIN_N = 400


def trades(M, bars):
    """The winning configuration's trades, each carrying its entry state."""
    out = []
    for m in M:
        here = m["path"].get(ENTRY)
        if not here:
            continue
        minute = (m["cts"] - ENTRY * 60) // 60 - 1
        ind = IB.indicators(bars.get(m["ser"], {}), minute)
        if not ind or ind == "flat":
            continue
        for side in ("YES", "NO"):
            sgn = 1.0 if side == "YES" else -1.0
            start = D.side_px(m["path"][14], side, "ask")
            buy = D.side_px(here, side, "ask")
            bid = D.side_px(here, side, "bid")
            if start is None or buy is None or not (0.05 < buy < 0.90):
                continue
            if buy > start - DROP:
                continue
            x = ind["bb"]
            if x is None or x * sgn > -0.5:
                continue
            c = int(D.STAKE / buy)
            if c < 1:
                continue
            cost = c * buy + D.fee(c, buy)
            target = min(buy * MULT, 0.97)
            hit = None
            for o in (o for o in D.OFFSETS if o < ENTRY):
                if o not in m["path"]:
                    continue
                sell = D.side_px(m["path"][o], side, "bid")
                if sell is not None and sell >= target:
                    hit = sell
                    break
            if hit is not None:
                v = (c * hit - D.fee(c, hit)) - cost
            else:
                won = m["yes"] if side == "YES" else (not m["yes"])
                v = (c if won else 0) - cost
            # Oriented to the side bought: a raw RSI of 30 argues for a
            # rebound on a fallen YES and against one on a fallen NO, so
            # unoriented values cancel between the two and read as noise.
            out.append(dict(
                ct=m["ct"], v=v, sold=hit is not None, ser=m["ser"],
                side=side, buy=buy,
                spread=(None if bid is None else buy - bid),
                fall=start - buy,
                hour=int(((m["cts"] - ENTRY * 60) // 3600 - 5) % 24),
                rsi=(None if ind["rsi"] is None else (ind["rsi"] - 50.0) * sgn),
                bb=x * sgn,
                hist=(None if ind["hist"] is None else ind["hist"] * sgn),
                vdev=(None if ind["vdev"] is None else ind["vdev"] * sgn),
                spike=ind["spike"],
                ema=(ind["ema"] == ("BEAR" if side == "YES" else "BULL")),
            ))
    return out


def score(rows):
    """n, hit rate, $/bet, clustered t."""
    n = len(rows)
    if not n:
        return n, 0.0, 0.0, 0.0
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["v"])
    m = sum(r["v"] for r in rows) / n
    se = D.cse(g, n)
    return (n, sum(1 for r in rows if r["sold"]) / n, m,
            (m / se) if se else 0.0)


def q(rows, key, frac):
    vals = sorted(r[key] for r in rows if r[key] is not None)
    if len(vals) < 20:
        return None
    return vals[int(frac * (len(vals) - 1))]


def candidates(rows):
    """Every split worth trying, as (label, predicate)."""
    C = []
    for ser in sorted({r["ser"] for r in rows}):
        C.append((f"series = {ser}", lambda r, s=ser: r["ser"] == s))
    C.append(("side = YES (coin fell)", lambda r: r["side"] == "YES"))
    C.append(("side = NO  (coin rose)", lambda r: r["side"] == "NO"))
    C.append(("EMA disagrees with the fall", lambda r: r["ema"]))
    C.append(("EMA agrees with the fall", lambda r: not r["ema"]))
    # Daytime CT, which is when the user would actually be watching, plus
    # the halves either side of it so the split is not cherry-picked.
    for lo, hi in ((8, 20), (0, 8), (20, 24), (8, 14), (14, 20)):
        C.append((f"hour CT in [{lo},{hi})",
                  lambda r, a=lo, b=hi: a <= r["hour"] < b))
    for key in ("buy", "spread", "fall", "rsi", "bb", "hist", "vdev",
                "spike"):
        for frac, name in ((0.25, "bottom quartile"), (0.50, "bottom half"),
                           (0.50, "top half"), (0.75, "top quartile")):
            t = q(rows, key, frac)
            if t is None:
                continue
            if name.startswith("bottom"):
                C.append((f"{key} <= {t:.4g} ({name})",
                          lambda r, k=key, x=t: r[k] is not None and r[k] <= x))
            else:
                C.append((f"{key} >  {t:.4g} ({name})",
                          lambda r, k=key, x=t: r[k] is not None and r[k] > x))
    return C


def day_checks(rows):
    """Delete the two best close-times, then the two worst. Both required."""
    by = collections.defaultdict(float)
    for r in rows:
        by[r["ct"]] += r["v"]
    order = sorted(by, key=lambda k: by[k])
    out = []
    for label, drop in (("two best close-times deleted", set(order[-2:])),
                        ("two worst close-times deleted", set(order[:2]))):
        k = [r for r in rows if r["ct"] not in drop]
        n, h, m, t = score(k)
        out.append((label, n, m, t))
    return out


def shuffle_null(rows, pred, obs):
    """Permute the attribute across close-time windows, keeping clusters."""
    wins = collections.defaultdict(list)
    for r in rows:
        wins[r["ct"]].append(r)
    keys = list(wins)
    labels = [[pred(r) for r in wins[k]] for k in keys]
    rnd = random.Random(23)
    nulls = []
    for _ in range(NPERM):
        order = list(range(len(keys)))
        rnd.shuffle(order)
        sel = []
        for i, k in enumerate(keys):
            lab = labels[order[i]]
            for j, r in enumerate(wins[k]):
                if lab[j % len(lab)]:
                    sel.append(r["v"])
        nulls.append(sum(sel) / len(sel) if sel else 0.0)
    nulls.sort()
    beat = sum(1 for x in nulls if x >= obs)
    return st.median(nulls), nulls[int(0.9 * NPERM)], (beat + 1) / (NPERM + 1)


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = D.load(sh)
    byser = collections.defaultdict(list)
    for m in M:
        byser[m["ser"]].append(m)
    bars = {}
    for ser, items in byser.items():
        lo = min(x["cts"] for x in items) - (14 + IB.BARS + 8) * 60
        hi = max(x["cts"] for x in items)
        print(f"  fetching {ser} ...", flush=True)
        bars[ser] = IB.fetch_bars(D.PRODUCT[ser], lo, hi)

    rows = trades(M, bars)
    n, hitr, mean, t = score(rows)
    print("\n" + "=" * 104)
    print(f"dip2's best config, re-run:  T-{ENTRY}  fell {100*DROP:.0f}pp  "
          f"{FILT}-oversold  target {MULT}x")
    print(f"  n={n}   target reached {100*hitr:.1f}%   ${mean:+.3f}/bet   "
          f"break-even needs {100*NEED:.1f}%")
    print("=" * 104)
    if n < MIN_N:
        print("not enough trades to split")
        return 0

    C = candidates(rows)
    print(f"\nsplitting on {len(C)} attributes, all knowable at entry.")
    print("A split has to lift the hit rate above the break-even line, not")
    print("merely above 40.2%, to be worth anything.\n")
    print(f"  {'attribute':<40} {'n':>6} {'hit%':>6} {'$/bet':>8} {'t':>6}")
    print("  " + "-" * 70)
    res = []
    for label, pred in C:
        k = [r for r in rows if pred(r)]
        if len(k) < MIN_N:
            continue
        n_, h_, m_, t_ = score(k)
        res.append((m_, h_, n_, t_, label, pred))
    for m_, h_, n_, t_, label, _ in sorted(res, key=lambda x: -x[0]):
        flag = "  <= clears break-even" if h_ >= NEED else ""
        print(f"  {label:<40} {n_:>6} {100*h_:>5.1f}% {m_:>+8.3f} "
              f"{t_:>+6.1f}{flag}")

    if not res:
        print("  every split fell below the minimum sample size")
        return 0

    m_, h_, n_, t_, label, pred = max(res, key=lambda x: x[0])
    print("\n" + "=" * 104)
    print(f"BEST SPLIT: {label}")
    print(f"  n={n_}   target reached {100*h_:.1f}%   ${m_:+.3f}/bet   "
          f"clustered t={t_:+.1f}")
    print("=" * 104)
    if h_ < NEED:
        print(f"  Still {100*(NEED-h_):.1f} points short of break-even. The")
        print("  robustness checks below are printed anyway, but a rule that")
        print("  does not clear the line cannot be made to by surviving them.")
    k = [r for r in rows if pred(r)]
    for lab, nn, mm, tt in day_checks(k):
        print(f"  {lab:<32} n={nn:<6} ${mm:+.3f}/bet  t={tt:+.1f}")
    med, p90, p = shuffle_null(rows, pred, m_)
    print(f"  shuffle (attribute permuted across windows): median ${med:+.3f}"
          f"  90th ${p90:+.3f}   p = {p:.3f}")
    print(f"\n  {len(C)} attributes were tried. A p above ~{1.0/len(C):.3f} is")
    print("  what one would expect from the best of that many in noise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

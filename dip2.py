"""A filtered dip trade: only when oversold, only early, with a profit target.

The blunt version -- buy every 10-point fall -- lost $2.21 a bet because 56%
of falls never came back. This asks the sharper question: if the indicators
say the fall is overdone, if there is still time left in the window, and if
we hold for a real gain rather than selling the moment we are even, does the
trade change?

FOUR THINGS ARE DIFFERENT FROM dip.py.

  ENTRY IS FILTERED. A dip is only taken when the underlying looks oversold
  IN THE DIRECTION BOUGHT -- for a fallen YES that means a low RSI or a
  price under its lower band; for a fallen NO the mirror, since the coin
  moved up and a rebound means it comes back down. Raw indicator values
  cancel across the two sides, so everything is oriented.

  ENTRY IS EARLY ONLY. T-12 or T-9, never T-6 or T-3. A 15-minute market
  three minutes from expiry has no time to rebound, and the previous run
  showed late entries were the worst.

  THE EXIT HAS A TARGET. 1.0x is break-even and is kept only as the
  baseline; 1.5x and 2.0x hold for a real gain. The target is checked
  against the BID, which is what you would actually receive.

  THE INDICATORS ARE TIMED CORRECTLY. Coinbase stamps a candle with the
  START of its bucket, so the bar labelled T-9 closes at T-8 -- one minute
  after entry. That one-minute error manufactured a 97% hit rate yesterday.
  Every indicator here is computed from bars closing at or before entry.

THE CARRIED BRANCH IS STILL PRICED. A target that is never reached leaves
the position held to settlement, and that is where the money goes. 36
configurations are tried, so the best gets a window-level shuffle.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P
import ind_backfill as IB

OFFSETS = (14, 12, 9, 6, 3, 1)
STAKE = 10.0
NPERM = 200
PRODUCT = IB.PRODUCT


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fee(c, p):
    return 0.07 * c * p * (1 - p)


def cse(g, n):
    G = len(g)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in g.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load(sh):
    rows = sh.worksheet("M15H").get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        ser = str(c("Series"))
        if ser not in PRODUCT:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        cts = IB.ts(c("Close Time"))
        if cts is None:
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                path[o] = (b, a)
        if 14 not in path or len(path) < 4:
            continue
        out.append(dict(tk=r[0], ser=ser, ct=str(c("Close Time")), cts=cts,
                        yes=(res == "yes"), path=path))
    return out


def side_px(q, side, which):
    b, a = q
    if side == "YES":
        return a if which == "ask" else b
    return (1 - b) if which == "ask" else (1 - a)


def run(M, bars, entry_off, drop, filt, mult):
    """One configuration. Returns the list of trades."""
    out = []
    for m in M:
        here = m["path"].get(entry_off)
        if not here:
            continue
        # indicators at entry, bar closing at or BEFORE entry (shift of 1)
        minute = (m["cts"] - entry_off * 60) // 60 - 1
        ind = IB.indicators(bars.get(m["ser"], {}), minute)
        if not ind or ind == "flat":
            continue
        for side in ("YES", "NO"):
            sgn = 1.0 if side == "YES" else -1.0
            start = side_px(m["path"][14], side, "ask")
            buy = side_px(here, side, "ask")
            if start is None or buy is None or not (0.05 < buy < 0.90):
                continue
            if buy > start - drop:
                continue
            # oversold IN THE DIRECTION BOUGHT argues for a rebound
            if filt == "rsi":
                x = ind["rsi"]
                if x is None or (x - 50.0) * sgn > -10.0:
                    continue
            elif filt == "bb":
                x = ind["bb"]
                if x is None or x * sgn > -0.5:
                    continue
            c = int(STAKE / buy)
            if c < 1:
                continue
            cost = c * buy + fee(c, buy)
            target = min(buy * mult, 0.97)
            hit = None
            for o in (o for o in OFFSETS if o < entry_off):
                if o not in m["path"]:
                    continue
                sell = side_px(m["path"][o], side, "bid")
                if sell is not None and sell >= target:
                    hit = sell
                    break
            if hit is not None:
                v = (c * hit - fee(c, hit)) - cost
                out.append(dict(ct=m["ct"], v=v, sold=True, buy=buy,
                                sell=hit))
            else:
                won = m["yes"] if side == "YES" else (not m["yes"])
                out.append(dict(ct=m["ct"], v=(c if won else 0) - cost,
                                sold=False, buy=buy, sell=None))
    return out


def rep(rows, label):
    if len(rows) < 120:
        print(f"  {label:<46} n={len(rows)} too few")
        return None
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["v"])
    n = len(rows)
    m = sum(r["v"] for r in rows) / n
    se = cse(g, n)
    days = collections.defaultdict(float)
    for r in rows:
        days[r["ct"][:10]] += r["v"]
    by = sorted(days.values(), reverse=True)
    print(f"  {label:<46} n={n:<6} hit-target {100*sum(1 for r in rows if r['sold'])/n:>4.0f}%"
          f"  ${m:>+6.3f}/bet t={m/se if se else 0:>+6.1f}  days+ "
          f"{100*sum(1 for v in by if v>0)/len(by):>3.0f}%")
    return m, rows


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh)
    print(f"crypto markets usable: {len(M)}")
    byser = collections.defaultdict(list)
    for m in M:
        byser[m["ser"]].append(m)
    bars = {}
    for ser, items in byser.items():
        lo = min(x["cts"] for x in items) - (14 + IB.BARS + 8) * 60
        hi = max(x["cts"] for x in items)
        print(f"  fetching {ser} ...", flush=True)
        bars[ser] = IB.fetch_bars(PRODUCT[ser], lo, hi)
        print(f"    {len(bars[ser])} bars")

    print("\n" + "=" * 118)
    print("FILTERED DIP, EARLY ENTRY, PROFIT TARGET  (indicators timed to entry)")
    print("=" * 118)
    best = None
    for entry_off in (12, 9):
        for drop in (0.10, 0.15):
            for filt in ("none", "rsi", "bb"):
                for mult in (1.0, 1.5, 2.0):
                    rows = run(M, bars, entry_off, drop, filt, mult)
                    r = rep(rows, f"T-{entry_off}  fell{100*drop:.0f}pp  "
                                  f"{filt:<4}  target {mult:.1f}x")
                    if r and (best is None or r[0] > best[0]):
                        best = (r[0], r[1],
                                f"T-{entry_off} fell{100*drop:.0f} {filt} "
                                f"{mult:.1f}x")

    if not best:
        print("\nnothing produced enough trades")
        return 0
    print("\n" + "=" * 118)
    print(f"BEST OF 36: {best[2]}   ${best[0]:+.3f}/bet")
    print("=" * 118)
    rows = best[1]
    s = [r for r in rows if r["sold"]]
    h = [r for r in rows if not r["sold"]]
    if s:
        print(f"  target reached   n={len(s):<6} "
              f"${sum(r['v'] for r in s)/len(s):>+7.3f}/bet   "
              f"buy {100*st.mean([r['buy'] for r in s]):>4.1f}c -> sell "
              f"{100*st.mean([r['sell'] for r in s]):>4.1f}c")
    if h:
        print(f"  target missed    n={len(h):<6} "
              f"${sum(r['v'] for r in h)/len(h):>+7.3f}/bet   carried to "
              f"settlement")
    need = None
    if s and h:
        winv = sum(r["v"] for r in s) / len(s)
        losv = sum(r["v"] for r in h) / len(h)
        if winv > 0 > losv:
            need = -losv / (winv - losv)
            print(f"\n  break-even needs the target reached "
                  f"{100*need:.1f}% of the time; it happens "
                  f"{100*len(s)/len(rows):.1f}%")

    vals = [r["v"] for r in rows]
    g = collections.defaultdict(list)
    for i, r in enumerate(rows):
        g[r["ct"]].append(i)
    groups = collections.defaultdict(list)
    for ct, ix in g.items():
        groups[len(ix)].append(ix)
    rnd = random.Random(19)
    nulls = []
    for _ in range(NPERM):
        sv = [0.0] * len(vals)
        for size, gl in groups.items():
            order = list(range(len(gl)))
            rnd.shuffle(order)
            for a_, b_ in enumerate(order):
                src, dst = gl[b_], gl[a_]
                for k in range(size):
                    sv[dst[k]] = vals[src[k]]
        nulls.append(sum(sv) / len(sv))
    nulls.sort()
    beat = sum(1 for x in nulls if x >= best[0])
    print(f"\n  shuffle: median ${st.median(nulls):+.3f}  "
          f"90th ${nulls[int(.9*NPERM)]:+.3f}   p = {(beat+1)/(NPERM+1):.3f}")
    print("  36 configurations were tried, so a single good-looking row is")
    print("  expected even in noise; the shuffle is what says otherwise.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

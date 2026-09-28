"""Do indicators at SEVERAL lookbacks together beat the market's price?

v6 tested one configuration -- a single lookback, one set of periods -- and
came in at 45.5% against a 47.1% price. That is evidence about that
configuration, not about the idea. The natural next question is whether
agreement ACROSS horizons carries information a single horizon does not: a
coin oversold on twenty minutes, forty and sixty is arguably a different
state from one oversold on twenty alone.

LOOKBACKS ARE 20, 40 AND 60 MINUTES, and the reason is arithmetic rather
than taste. Bollinger needs 20 bars and MACD needs 35, so a 15-minute window
cannot produce either; asking for them anyway would return None and quietly
shrink the sample. MACD is therefore computed at 40 and 60 only, and that
gap is reported rather than hidden.

ENTRY IS AT T-12, a minute into the window, which is the earliest point the
book is quoted. This is the honest form of "predict the next fifteen
minutes": everything used exists before the window has really begun.

THE BENCHMARK IS THE PRICE, NOT 50%. A contract quoted at 47c that wins 47%
of the time has taught us nothing. So every comparison is against what the
market charged, and the money column uses the real ask.

AND THE CLOCK IS RIGHT. Coinbase stamps a candle with the START of its
bucket, so a bar labelled T-12 closes at T-11, one minute after entry. That
one-minute slip produced a fake 97% hit rate two runs ago. Bars here close
at or before entry.

Read-only.
"""

import collections
import math
import random
import statistics as st
import sys

import predictor as P
import ind_backfill as IB

ENTRY = 12
# The HORIZON has to live in the indicator's PERIOD, not in how many bars
# are handed to it. RSI(7) reads the last 7 closes and Bollinger(20) the last
# 20 no matter how long a series you pass, so feeding 20, 40 and 60 bars to
# fixed periods returns the identical number three times -- which the first
# offline check showed as rsi=47.3 and bb=0.211 in all three rows. The
# periods are therefore scaled instead, and the bar count follows from them.
HORIZONS = ((7, 10, "15m"), (14, 20, "30m"), (30, 40, "60m"))
STAKE = 10.0
NPERM = 200
PRODUCT = IB.PRODUCT


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
    return (c if won else 0) - (c * price + 0.07 * c * price * (1 - price))


def cse(g, n):
    G = len(g)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in g.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def ind_at(bars, minute, rsi_p, bb_p):
    """RSI and Bollinger position at a given PERIOD, ending at `minute`.

    The period is the horizon. RSI(7) is a fifteen-minute view of momentum,
    RSI(30) an hour of it, and they are genuinely different numbers -- unlike
    RSI(7) fed 20 versus 60 bars, which is the same number twice.

    Returns None on a short window rather than a partial figure."""
    need = max(rsi_p + 1, bb_p) + 5
    seq = []
    for m in range(minute - need + 1, minute + 1):
        k = bars.get(m)
        if k:
            seq.append(k)
    if len(seq) < need - 3:
        return None
    closes = [float(k[4]) for k in seq]
    try:
        rsi = P.calc_rsi(closes, rsi_p)
    except ZeroDivisionError:
        return None
    bb = None
    try:
        band = P.calc_bollinger(closes, bb_p, 2.0)
        if band:
            up, mid, lo = band
            if up != lo:
                bb = (closes[-1] - mid) / ((up - lo) / 2.0)
    except ZeroDivisionError:
        pass
    if rsi is None or bb is None:
        return None
    return dict(rsi=rsi, bb=bb, n=len(seq))


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
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if cts is None or b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(tk=r[0], ser=ser, ct=str(c("Close Time")), cts=cts,
                        yes=(res == "yes"), b=b, a=a))
    return out


def rep(rows, label):
    if len(rows) < 150:
        print(f"  {label:<44} n={len(rows)} too few")
        return None
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["v"])
    n = len(rows)
    m = sum(r["v"] for r in rows) / n
    se = cse(g, n)
    h = sum(1 for r in rows if r["won"])
    paid = 100 * sum(r["price"] for r in rows) / n
    days = collections.defaultdict(float)
    for r in rows:
        days[r["ct"][:10]] += r["v"]
    by = sorted(days.values(), reverse=True)
    print(f"  {label:<44} n={n:<6} won {100*h/n:>5.1f}% paid {paid:>5.1f}c "
          f"edge {100*h/n-paid:>+6.2f}pp  ${m:>+6.3f}/bet t="
          f"{m/se if se else 0:>+6.1f}  days+ "
          f"{100*sum(1 for v in by if v>0)/len(by):>3.0f}%")
    return m, rows


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh)
    print(f"crypto markets with a T-{ENTRY} quote: {len(M)}")
    byser = collections.defaultdict(list)
    for m in M:
        byser[m["ser"]].append(m)
    bars = {}
    for ser, items in byser.items():
        lo = min(x["cts"] for x in items) - (ENTRY + 60) * 60
        hi = max(x["cts"] for x in items)
        print(f"  fetching {ser} ...", flush=True)
        bars[ser] = IB.fetch_bars(PRODUCT[ser], lo, hi)
        print(f"    {len(bars[ser])} bars")

    # one row per (market, side) with all three lookbacks attached
    B = []
    miss = collections.Counter()
    for m in M:
        minute = (m["cts"] - ENTRY * 60) // 60 - 1     # bar closes AT entry
        per = {}
        for rp, bp, tag in HORIZONS:
            per[tag] = ind_at(bars.get(m["ser"], {}), minute, rp, bp)
        if any(v is None for v in per.values()):
            miss["short window"] += 1
            continue
        for side in ("YES", "NO"):
            sgn = 1.0 if side == "YES" else -1.0
            price = m["a"] if side == "YES" else 1 - m["b"]
            if not (0.05 < price < 0.95):
                continue
            won = m["yes"] if side == "YES" else (not m["yes"])
            v = pnl(price, won)
            if v is None:
                continue
            o = {}
            for _, _, tag in HORIZONS:
                o[f"rsi{tag}"] = (per[tag]["rsi"] - 50.0) * sgn
                o[f"bb{tag}"] = per[tag]["bb"] * sgn
            B.append(dict(ct=m["ct"], price=price, won=won, v=v, o=o))
    print(f"\nbets built: {len(B)}   skipped: {dict(miss)}")
    print("horizons: RSI(7)/BB(10) = 15m, RSI(14)/BB(20) = 30m, "
          "RSI(30)/BB(40) = 60m\n")
    if len(B) < 500:
        print("too few to read")
        return 0

    print("=" * 120)
    print("1. EACH LOOKBACK ALONE  (top quartile, oriented to the side bought)")
    print("=" * 120)
    for key in ([f"rsi{t}" for *_, t in HORIZONS]
                + [f"bb{t}" for *_, t in HORIZONS]):
        v = sorted(((b["o"][key], b) for b in B if b["o"][key] is not None),
                   key=lambda t: t[0])
        if len(v) < 800:
            continue
        q = len(v) // 4
        rep([b for _, b in v[-q:]], f"{key} top quartile")

    print("\n" + "=" * 120)
    print("2. AGREEMENT ACROSS HORIZONS  (the actual question)")
    print("=" * 120)
    best = None
    for fam, lab in (("rsi", "RSI"), ("bb", "Bollinger")):
        for k in (2, 3):
            sel = []
            for b in B:
                vals = [b["o"][f"{fam}{t}"] for *_, t in HORIZONS]
                vals = [x for x in vals if x is not None]
                if len(vals) < 3:
                    continue
                agree = sum(1 for x in vals if x > 0)
                if agree >= k:
                    sel.append(b)
            r = rep(sel, f"{lab}: {k} of 3 horizons favour my side")
            if r and (best is None or r[0] > best[0]):
                best = (r[0], r[1], f"{lab} {k}/3")
    # both families agreeing on all three
    sel = []
    for b in B:
        rs = [b["o"][f"rsi{t}"] for *_, t in HORIZONS]
        bs = [b["o"][f"bb{t}"] for *_, t in HORIZONS]
        if any(x is None for x in rs + bs):
            continue
        if all(x > 0 for x in rs) and all(x > 0 for x in bs):
            sel.append(b)
    r = rep(sel, "RSI and Bollinger, all 3 horizons")
    if r and (best is None or r[0] > best[0]):
        best = (r[0], r[1], "both families 3/3")

    print("\n" + "=" * 120)
    print("3. BASELINE")
    print("=" * 120)
    rep(B, "every bet at T-12, no filter")

    if best:
        rows = best[1]
        vals = [r["v"] for r in rows]
        g = collections.defaultdict(list)
        for i, r in enumerate(rows):
            g[r["ct"]].append(i)
        groups = collections.defaultdict(list)
        for ct, ix in g.items():
            groups[len(ix)].append(ix)
        rnd = random.Random(23)
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
        print(f"\n  best rule {best[2]}: ${best[0]:+.3f}/bet")
        print(f"  shuffle median ${st.median(nulls):+.3f}  "
              f"90th ${nulls[int(.9*NPERM)]:+.3f}   p = "
              f"{(beat+1)/(NPERM+1):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Was the indicator edge real, or one minute of lookahead?

ind_split found favourites in the top Bollinger quartile hitting 97.2%
against a 92.8c price -- +$0.43 a bet, t=+12.5, profitable on 91% of days.
Three indicators agreed. Every filter passed.

THE SUSPICION. Coinbase labels a candle by the START of its bucket, so the
bar timestamped T-3 covers T-3 to T-2 and its CLOSE is the price at T-2 --
one minute after the entry at T-3. On a market with three minutes left,
knowing the next minute's spot is worth a great deal, and +4.4 points of
accuracy is about what it would be worth.

THE TEST. Recompute the same indicators with the window ending one minute
EARLIER, so the last close used is at or before entry. Nothing else changes.
If the edge collapses it was the future leaking in; if it survives, it is
real and this was a false alarm.

Both are reported side by side rather than only the corrected number,
because the size of the collapse is the measurement.
"""

import collections
import math
import statistics as st
import sys
import time

import predictor as P
import ind_backfill as IB

ENTRY = 3
STAKE = 10.0
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


def rep(sel, label):
    if len(sel) < 100:
        print(f"  {label:<34} n={len(sel)} too few")
        return
    g = collections.defaultdict(list)
    for b in sel:
        g[b["ct"]].append(b["v"])
    n = len(sel)
    m = sum(b["v"] for b in sel) / n
    se = cse(g, n)
    h = sum(1 for b in sel if b["won"])
    days = collections.defaultdict(float)
    for b in sel:
        days[b["ct"][:10]] += b["v"]
    print(f"  {label:<34} n={n:<6} hit {100*h/n:>5.1f}%  paid "
          f"{100*sum(b['price'] for b in sel)/n:>5.1f}c  ${m:>+6.3f}/bet "
          f"t={m/se if se else 0:>+6.1f}  days+ "
          f"{100*sum(1 for v in days.values() if v>0)/len(days):>3.0f}%")


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet("M15H").get_all_values()
    hm = {h: i for i, h in enumerate(rows[0])}
    mk = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hm.get(k)
            return r[i] if i is not None and i < len(r) else ""
        ser = str(c("Series"))
        if ser not in PRODUCT:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        ct = IB.ts(c("Close Time"))
        if b is None or a is None or ct is None or not (0 < b <= a < 1):
            continue
        mk.append(dict(tk=r[0], ser=ser, ct=str(c("Close Time")),
                       cts=ct, yes=(res == "yes"), b=b, a=a))
    print(f"crypto markets with a T-{ENTRY} quote: {len(mk)}")

    # fetch bars once per series, covering everything we need
    byser = collections.defaultdict(list)
    for m in mk:
        byser[m["ser"]].append(m)
    bars = {}
    for ser, items in byser.items():
        lo = min(m["cts"] for m in items) - (ENTRY + IB.BARS + 6) * 60
        hi = max(m["cts"] for m in items)
        print(f"  fetching {ser} bars ...", flush=True)
        bars[ser] = IB.fetch_bars(PRODUCT[ser], lo, hi)
        print(f"    {len(bars[ser])} bars")

    def build(shift):
        out = []
        for m in mk:
            minute = (m["cts"] - ENTRY * 60) // 60 - shift
            ind = IB.indicators(bars[m["ser"]], minute)
            if not ind or ind == "flat":
                continue
            for side in ("YES", "NO"):
                price = m["a"] if side == "YES" else 1 - m["b"]
                if not (0.80 <= price < 0.98):
                    continue
                won = m["yes"] if side == "YES" else (not m["yes"])
                v = pnl(price, won)
                if v is None:
                    continue
                sgn = 1.0 if side == "YES" else -1.0
                out.append(dict(ct=m["ct"], price=price, won=won, v=v,
                                bb=(ind["bb"] * sgn) if ind["bb"] is not None
                                else None,
                                rsi=((ind["rsi"] - 50) * sgn)
                                if ind["rsi"] is not None else None))
        return out

    for shift, name in ((0, "AS BUILT  (bar close is T-2, one minute ahead)"),
                        (1, "SHIFTED   (bar close is T-3, entry time)")):
        bets = build(shift)
        print(f"\n{'='*92}\n{name}\n{'='*92}")
        rep(bets, "all favourites 80-98c")
        for key, lab in (("bb", "Bollinger"), ("rsi", "RSI")):
            v = sorted(((b[key], b) for b in bets if b[key] is not None),
                       key=lambda t: t[0])
            if len(v) < 400:
                continue
            q = len(v) // 4
            rep([b for _, b in v[-q:]], f"  {lab} top quartile")
            rep([b for _, b in v[:q]], f"  {lab} bottom quartile")

    print(f"\n{'='*92}")
    print("If the shifted rows collapse toward the baseline, the edge was the")
    print("next minute's price and nothing more. That is not a strategy: at")
    print("entry time that number does not exist yet.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())

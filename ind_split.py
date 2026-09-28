"""What did the winners have in common? Indicators, hits versus misses.

THE NAIVE VERSION OF THIS QUESTION ANSWERS ITSELF WRONGLY. Most winners are
favourites -- the 91% band -- so any indicator that correlates at all with
price will look predictive of winning. Ask "did winners have higher RSI" and
you mostly measure "were winners priced higher", which we already know.

So every comparison here runs INSIDE a price band. Within contracts that all
cost about the same, do the ones that won carry different indicator values
from the ones that lost? That is the only version of the question whose
answer could be acted on.

SIDE MATTERS TOO. The indicators describe the underlying coin, not the
contract. Rising momentum argues for YES and against NO, so a raw average
over both sides cancels out. Each indicator is therefore also reported
ORIENTED: signed so that positive means "this argued for the side I bought".

And the difference that counts is money, not means. A gap in average RSI
between winners and losers is interesting; a gap that survives being turned
into dollars per bet, with clustered errors and a day-level check, is the
only kind worth a notification.

Read-only.
"""

import collections
import math
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
ENTRY = 3
STAKE = 10.0
INDS = [("RSI7", "rsi"), ("MACD Hist", "hist"), ("BB Position", "bb"),
        ("VWAP Dev %", "vdev"), ("Vol Spike", "spike")]


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


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)

    rows = sh.worksheet("IND").get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    IND = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        IND[r[0]] = dict(rsi=_f(c("RSI7")), ema=str(c("EMA Signal")),
                         hist=_f(c("MACD Hist")), bb=_f(c("BB Position")),
                         vdev=_f(c("VWAP Dev %")), spike=_f(c("Vol Spike")))

    rows = sh.worksheet("M15H").get_all_values()
    hm = {h: i for i, h in enumerate(rows[0])}
    bets = []
    for r in rows[1:]:
        if not r or not r[0] or r[0] not in IND:
            continue
        def c(k):
            i = hm.get(k)
            return r[i] if i is not None and i < len(r) else ""
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        ind = IND[r[0]]
        for side in ("YES", "NO"):
            price = a if side == "YES" else 1 - b
            if not (0.02 < price < 0.98):
                continue
            won = (res == "yes") if side == "YES" else (res == "no")
            v = pnl(price, won)
            if v is None:
                continue
            sgn = 1.0 if side == "YES" else -1.0
            bets.append(dict(day=str(c("Close Time"))[:10],
                             ct=str(c("Close Time")), price=price, won=won,
                             v=v, side=side, ind=ind, sgn=sgn))
    print(f"bets with indicators attached: {len(bets)}   "
          f"markets: {len({b['ct'] for b in bets})} close-times\n")
    if not bets:
        print("no overlap between IND and M15H")
        return 1

    BANDS = ((0.02, 0.20), (0.20, 0.40), (0.40, 0.60), (0.60, 0.80),
             (0.80, 0.98))
    print("=" * 100)
    print("1. RAW indicator averages, hits vs misses, INSIDE each price band")
    print("=" * 100)
    for lo, thi in BANDS:
        sel = [b for b in bets if lo <= b["price"] < thi]
        if len(sel) < 200:
            continue
        w = [b for b in sel if b["won"]]
        l = [b for b in sel if not b["won"]]
        if len(w) < 50 or len(l) < 50:
            continue
        print(f"\n  price {lo:.2f}-{thi:.2f}   n={len(sel)}  "
              f"({len(w)} hit / {len(l)} miss)")
        for label, key in INDS:
            vw = [b["ind"][key] for b in w if b["ind"][key] is not None]
            vl = [b["ind"][key] for b in l if b["ind"][key] is not None]
            if len(vw) < 30 or len(vl) < 30:
                continue
            mw, ml = st.mean(vw), st.mean(vl)
            sd = st.pstdev(vw + vl) or 1.0
            print(f"     {label:<12} hit {mw:>9.3f}   miss {ml:>9.3f}   "
                  f"gap {mw-ml:>+9.3f}  ({(mw-ml)/sd:>+5.2f} sd)")
        ew = collections.Counter(b["ind"]["ema"] for b in w)
        el = collections.Counter(b["ind"]["ema"] for b in l)
        tw, tl = sum(ew.values()), sum(el.values())
        print(f"     {'EMA Signal':<12} " + "  ".join(
            f"{k}: hit {100*ew[k]/tw:.0f}% miss {100*el[k]/tl:.0f}%"
            for k in ("BULL", "BEAR", "FLAT") if ew[k] or el[k]))

    print("\n" + "=" * 100)
    print("2. ORIENTED to the side bought  (+ means it argued for my side)")
    print("=" * 100)
    print("   Raw averages cancel across YES and NO; these do not.")
    for lo, thi in BANDS:
        sel = [b for b in bets if lo <= b["price"] < thi]
        if len(sel) < 200:
            continue
        w = [b for b in sel if b["won"]]
        l = [b for b in sel if not b["won"]]
        if len(w) < 50 or len(l) < 50:
            continue
        print(f"\n  price {lo:.2f}-{thi:.2f}")
        for label, key in (("RSI7-50", "rsi"), ("MACD Hist", "hist"),
                           ("BB Position", "bb"), ("VWAP Dev %", "vdev")):
            def orient(b):
                x = b["ind"][key]
                if x is None:
                    return None
                if key == "rsi":
                    x = x - 50.0
                return x * b["sgn"]
            vw = [orient(b) for b in w]
            vw = [x for x in vw if x is not None]
            vl = [orient(b) for b in l]
            vl = [x for x in vl if x is not None]
            if len(vw) < 30 or len(vl) < 30:
                continue
            mw, ml = st.mean(vw), st.mean(vl)
            sd = st.pstdev(vw + vl) or 1.0
            print(f"     {label:<12} hit {mw:>9.3f}   miss {ml:>9.3f}   "
                  f"gap {mw-ml:>+9.3f}  ({(mw-ml)/sd:>+5.2f} sd)")

    print("\n" + "=" * 100)
    print("3. DOES IT PAY? favourites only (>80c), split by each indicator")
    print("=" * 100)
    fav = [b for b in bets if b["price"] >= 0.80]
    print(f"   baseline: all favourites")
    report(fav)
    for label, key in (("RSI oriented", "rsi"), ("MACD oriented", "hist"),
                       ("BB oriented", "bb"), ("VWAP oriented", "vdev"),
                       ("Vol Spike raw", "spike")):
        vals = []
        for b in fav:
            x = b["ind"][key]
            if x is None:
                continue
            if key == "rsi":
                x = x - 50.0
            if key != "spike":
                x = x * b["sgn"]
            vals.append((x, b))
        if len(vals) < 400:
            continue
        vals.sort(key=lambda t: t[0])
        q = len(vals) // 4
        print(f"\n   {label}")
        report([b for _, b in vals[:q]], "     bottom quartile")
        report([b for _, b in vals[-q:]], "     top quartile   ")
    return 0


def report(sel, label="   all            "):
    if len(sel) < 100:
        print(f"{label} n={len(sel)} too few")
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
        days[b["day"]] += b["v"]
    by = sorted(days.values(), reverse=True)
    print(f"{label} n={n:<6} hit {100*h/n:>5.1f}%  paid "
          f"{100*sum(b['price'] for b in sel)/n:>5.1f}c  ${m:>+6.3f}/bet "
          f"+/-{se or 0:>5.3f}  t={m/se if se else 0:>+5.1f}  "
          f"days+ {100*sum(1 for v in by if v>0)/len(by):>3.0f}%")


if __name__ == "__main__":
    sys.exit(main())

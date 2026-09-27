"""Sell cheap contracts as the MAKER, on 70 days of data.

Every strategy tested here has been a taker: cross the spread, pay the ask,
lose the spread. The one direction never tested at scale is the other side
of the trade we understand best -- longshot BUYERS lost $20,322 in three
days, and somebody collected that by resting quotes rather than crossing.

WHAT MAKING ACTUALLY CHANGES. Buying NO as a taker costs 1 minus the YES
bid. Resting instead, and being crossed into, fills at 1 minus the YES ASK
-- better by the whole spread. On a 5c longshot that is about two cents a
contract, which is the entire margin in question.

WHAT IT DOES NOT CHANGE. Kalshi charges the fee to both sides, so making
dodges nothing there; the maker study's verdict C came from exactly that --
0.50c of half-spread against a 1.75c fee at mid prices. The cheap end is
the only place the arithmetic is even close.

TWO FILL MODELS, AND THE GAP BETWEEN THEM IS THE ANSWER.

  OPTIMISTIC: every resting order fills at our price, no selection. This is
  an upper bound and cannot be achieved. If it loses, the idea is dead and
  nothing else needs checking.

  ADVERSE: an order fills only when the market later trades through it --
  the bid rises to touch our ask. That is what really happens, and it is
  also exactly when we are wrong: we sold YES cheap and the market went up.
  A maker strategy that works optimistically and dies here is a strategy
  whose profits are imaginary.

Read-only.
"""

import collections
import math
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
MIN_N = 120
STAKE = 10.0


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fee_for(c, price):
    return math.ceil(round(0.07 * c * price * (1 - price), 9) * 100) / 100.0


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
        if len(path) < 2:
            continue
        out.append(dict(close=str(c("Close Time")), ser=str(c("Series")),
                        yes=(res == "yes"), path=path))
    return out


def report(bets, label):
    if len(bets) < MIN_N:
        print(f"  {label:<34} n={len(bets):<6} too few")
        return
    g = collections.defaultdict(list)
    for v, ct in bets:
        g[ct].append(v)
    n = len(bets)
    m = sum(v for v, _ in bets) / n
    se = cse(g, n)
    by = sorted((sum(v) for v in g.values()), reverse=True)
    minus2 = sum(by[2:])
    share = 100 * sum(1 for x in by if x > 0) / len(by)
    t = m / se if se else 0.0
    flag = ""
    if m > 0:
        flag = "  <== POSITIVE" if (t > 2.0 and minus2 > 0) else "  (positive, fails a check)"
    print(f"  {label:<34} n={n:<6} ${m:>+6.2f} +/-{se:>4.2f} t={t:>+5.1f} "
          f"days+ {share:>3.0f}% total {sum(v for v, _ in bets):>+9.0f}{flag}")


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    print("=" * 100)
    print(f"MAKER TEST on {tab}: {len(M)} markets with two-sided quotes")
    print("=" * 100)
    print("We SELL yes (buy no) by resting, so our cost is 1 - yes_ask.")
    print("The taker doing the same trade pays 1 - yes_bid.\n")

    bands = ((0.01, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.35))
    for model in ("optimistic", "adverse"):
        print("=" * 100)
        print(f"{model.upper()} FILLS")
        print("=" * 100)
        for lo, hi in bands:
            bets = []
            for m in M:
                for i, off in enumerate(OFFSETS[:-1]):
                    q = m["path"].get(off)
                    if not q:
                        continue
                    b, a = q
                    if not (lo <= a < hi):
                        continue
                    later = [m["path"][o] for o in OFFSETS[i + 1:]
                             if o in m["path"]]
                    if model == "adverse":
                        # filled only if someone later bids up to our ask
                        if not any(lb >= a for lb, la in later):
                            continue
                    cost = 1.0 - a            # we buy NO at 1 - yes_ask
                    c = int(STAKE / cost)
                    if c < 1:
                        continue
                    f = fee_for(c, cost)
                    won = not m["yes"]        # NO wins when the market says no
                    bets.append((((c if won else 0) - (c * cost + f)),
                                 m["close"]))
                    break                     # one resting order per market
            report(bets, f"sell yes at {lo:.2f}-{hi:.2f}")

    # The taker doing the identical trade, for contrast.
    print("=" * 100)
    print("THE SAME TRADE AS A TAKER  (pays 1 - yes_bid instead)")
    print("=" * 100)
    for lo, hi in bands:
        bets = []
        for m in M:
            for off in OFFSETS:
                q = m["path"].get(off)
                if not q:
                    continue
                b, a = q
                if not (lo <= a < hi):
                    continue
                cost = 1.0 - b
                c = int(STAKE / cost)
                if c < 1:
                    continue
                f = fee_for(c, cost)
                won = not m["yes"]
                bets.append((((c if won else 0) - (c * cost + f)), m["close"]))
                break
        report(bets, f"sell yes at {lo:.2f}-{hi:.2f}")

    print("\n" + "=" * 100)
    print("If OPTIMISTIC loses, the idea is dead -- it is an upper bound that")
    print("assumes free fills and no selection. If optimistic wins and ADVERSE")
    print("loses, the profit was imaginary: the fills arrive precisely when the")
    print("market is moving against the quote.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

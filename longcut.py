"""Buy the ~19c side and hold to expiry. The trade KingTradezz posts. Read-only.

WHERE THIS CAME FROM. A Kalshi profile on the crypto leaderboard posted a
+$151,608 week over 71 trades. Adding up the slips he posted, three of them
-- all BTC 15-minute, all entered at 18.3-19.5c -- account for +$134,662,
which is 89% of the whole week. The other five posted trades made $41,401
and, since his posted trades total MORE than his stated return, the ~63
trades he did not post lost about $24,456 between them.

So the question is narrow and answerable: IS THE ~19c SIDE OF A FIFTEEN-
MINUTE CRYPTO MARKET UNDERPRICED? If it is, those three wins were skill and
the strategy is worth stealing. If it is fairly priced, three winners is
simply what fifteen attempts at one-in-five produces, and the posts are a
payout structure rather than a signal.

THE PRIOR IS THAT IT IS OVERPRICED, NOT UNDER. The favourite-longshot bias
is the single effect this whole project rests on, and it says the cheap side
costs too much. This project's own revert.py bought the 5c side and held it:
-52.59%. That was a more extreme price, so 19c genuinely is a different
question, but the direction of the prior is not in doubt and is stated here
BEFORE the numbers.

THE TEST IS THE SAME SHAPE AS EVERY OTHER ONE HERE.

  BREAK-EVEN IS THE GROUP'S OWN MEAN COST PER CONTRACT. A winning contract
  pays exactly $1, so buying at 19c needs 19% to win plus the fee. Comparing
  a 19c bet's win rate against a 92c bet's is comparing two different
  hurdles, which is why every rate below is printed against its own line.

  THE FEE IS CHARGED AT ITS REAL SHAPE, and at this price it is not small:
  0.07 * n * p * (1-p) is 1.08c on a 19c contract, about 5.7% of stake,
  against 0.37% on a 95c favourite. Rounded up at the order level as Kalshi
  does it.

  THE SAME WINDOWS ARE SCORED BOTH WAYS. For every market where one side is
  in the band, the OTHER side is the favourite at ~81c, and the favourite's
  return on those identical windows is reported beside it. One market, two
  prices, head to head -- which is the only comparison that cannot be
  confounded by which days or coins happened to be in the sample.

WHAT IT CANNOT SEE. Entry is the quoted ask at one instant, so it inherits
the standing caveat of every quote-based result here: the live record's
fills run about 0.75c worse than its quotes, and at 19c that is 4% of stake.
Depth is reported but not simulated -- a $5 bet needs 26 contracts at 19c
and the 95/5 study found a median of ONE contract resting at the extremes.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import os
import statistics as st

import predictor as P
import collect15 as C
import oddsmath as M
import tally as T

LO = float(os.environ.get("LONG_LO_CENTS") or 15.0)
HI = float(os.environ.get("LONG_HI_CENTS") or 25.0)
OFFSETS = tuple(int(x) for x in
                (os.environ.get("LONG_OFFSETS") or "14,12,9,6,3").split(",")
                if x.strip())
STAKE = float(os.environ.get("LONG_STAKE") or 5.0)
MIN_N = int(os.environ.get("LONG_MIN_N") or 150)
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def coin_of(ticker):
    tk = str(ticker or "").upper()
    if not tk.startswith("KX"):
        return "?"
    head = tk.split("-")[0][2:]
    return head[:-3] if head.endswith("15M") else head


def cell(group):
    """(n, won%, mean all-in cost per contract, net, ROI, wilson lo/hi)."""
    n = len(group)
    if not n:
        return None
    wins = sum(1 for g in group if g["won"])
    staked = sum(g["cost"] for g in group)
    ret = sum(g["ret"] for g in group)
    be = 100.0 * st.mean([g["cost"] / g["k"] for g in group])
    lo, hi = T.wilson(wins, n)
    return (n, 100.0 * wins / n, be, ret - staked,
            100.0 * (ret - staked) / staked if staked else 0.0,
            100.0 * lo, 100.0 * hi)


def show(label, c):
    if c is None:
        print(f"  {label:<22} -- none")
        return
    n, won, be, net, roi, wlo, whi = c
    verdict = "UNDER" if won > be else "over"
    print(f"  {label:<22} n {n:>5}   won {won:6.2f}%  "
          f"[{wlo:5.2f}-{whi:5.2f}]   needs {be:6.2f}%   "
          f"net ${net:+9.2f}  ROI {roi:+7.2f}%   {verdict}priced")


def main():
    print("=" * 108)
    print(f"THE {LO:.0f}-{HI:.0f}c SIDE, BOUGHT AND HELD TO EXPIRY")
    print(f"  The trade behind 89% of a leaderboard week. ${STAKE:.0f} a bet, "
          f"real fees, quotes from the collector.")
    print(f"  Prior, stated before the numbers: the favourite-longshot bias "
          f"says this side is OVERpriced.")
    print("=" * 108)

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(C.SHEET).get_all_values()
    if len(rows) < 2:
        print("\n  the collector sheet is empty.")
        return 1
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    graded = 0
    # keyed by offset -> list of records
    long_by = collections.defaultdict(list)
    fav_by = collections.defaultdict(list)
    by_coin = collections.defaultdict(list)
    depths = []

    for r in rows[1:]:
        tk = str(g(r, "Ticker")).strip()
        if not tk:
            continue
        res = str(g(r, "Result")).strip().lower()
        if res not in ("yes", "no"):
            continue
        graded += 1
        coin = coin_of(tk)
        if coin not in COINS:
            continue
        close = str(g(r, "Close Time"))[:10]

        for m in OFFSETS:
            yb, ya = f(g(r, f"bid{m}")), f(g(r, f"ask{m}"))
            if yb is None or ya is None or not (0 < yb <= ya < 1):
                continue
            yes_c = ya * 100.0            # cost to buy YES
            no_c = (1.0 - yb) * 100.0     # cost to buy NO (one book!)

            # Which side sits in the band? Never both: they sum to ~100.
            if LO <= yes_c <= HI:
                cheap, dear = "YES", "NO"
                cheap_c, dear_c = yes_c, no_c
                cheap_won, dear_won = (res == "yes"), (res == "no")
                depth = f(g(r, f"asksz{m}"))
            elif LO <= no_c <= HI:
                cheap, dear = "NO", "YES"
                cheap_c, dear_c = no_c, yes_c
                cheap_won, dear_won = (res == "no"), (res == "yes")
                depth = f(g(r, f"bidsz{m}"))
            else:
                continue

            def rec(price_c, won):
                k = int(STAKE / (price_c / 100.0))
                if k < 1:
                    return None
                fee = M.fee_cents(k, price_c / 100.0) / 100.0
                return dict(k=k, cost=k * price_c / 100.0 + fee,
                            ret=(k * 1.0 if won else 0.0), won=won,
                            price=price_c, close=close, coin=coin)

            a = rec(cheap_c, cheap_won)
            b = rec(dear_c, dear_won)
            if a is None or b is None:
                continue
            long_by[m].append(a)
            fav_by[m].append(b)
            if m == OFFSETS[len(OFFSETS) // 2]:
                by_coin[coin].append(a)
                if depth is not None:
                    depths.append(depth)

    print(f"\n  {graded} graded collector row(s)")
    tot = sum(len(v) for v in long_by.values())
    print(f"  {tot} (row, offset) pair(s) had a side inside the band")
    if tot < MIN_N:
        print(f"\n  BELOW the {MIN_N} minimum set before the run. No verdict.")
        return 0

    print(f"\n{'-' * 108}")
    print("HEAD TO HEAD ON THE SAME WINDOWS -- cheap side vs the favourite "
          "it faces")
    print("-" * 108)
    for m in OFFSETS:
        if not long_by[m]:
            continue
        print(f"  T-{m}:")
        show(f"    the {LO:.0f}-{HI:.0f}c side", cell(long_by[m]))
        show("    the favourite", cell(fav_by[m]))
        print()

    mid_off = OFFSETS[len(OFFSETS) // 2]
    print(f"{'-' * 108}")
    print(f"BY COIN, at T-{mid_off} -- he traded BTC only")
    print("-" * 108)
    for coin in sorted(by_coin, key=lambda k: -len(by_coin[k])):
        show(coin, cell(by_coin[coin]))

    if depths:
        depths.sort()
        need = STAKE / ((LO + HI) / 2 / 100.0)
        print(f"\n  depth on the cheap side at T-{mid_off}: median "
              f"{depths[len(depths)//2]:.0f} contract(s), "
              f"10th pct {depths[max(0,len(depths)//10)]:.0f}")
        print(f"  a ${STAKE:.0f} bet needs about {need:.0f} contracts; his "
              f"were ~100,000")

    # Halves, on the pooled middle offset.
    pool = sorted(long_by[mid_off], key=lambda r: r["close"])
    if len(pool) >= 2 * MIN_N // 3:
        half = len(pool) // 2
        print(f"\n{'-' * 108}")
        print("BOTH CHRONOLOGICAL HALVES")
        print("-" * 108)
        for label, part in (("first half", pool[:half]),
                            ("second half", pool[half:])):
            show(label, cell(part))

    print("\n" + "=" * 108)
    print("  HOW TO READ IT.")
    print()
    print("  'UNDERpriced' means the cheap side won MORE often than its own")
    print("  all-in cost requires. That is the only result that would make")
    print("  his three winners anything but the expected yield of repeated")
    print("  one-in-five attempts.")
    print()
    print("  THE HEAD-TO-HEAD IS THE POINT. Both rows are the same markets at")
    print("  the same instants, so they cannot differ because of which coins")
    print("  or days fell into the sample. One of the two sides is the better")
    print("  buy and the table says which.")
    print()
    print("  QUOTES, NOT FILLS. The live record fills about 0.75c worse than")
    print("  its quotes, which at 19c is 4% of stake -- so a cheap-side result")
    print("  near break-even here is NEGATIVE in practice.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

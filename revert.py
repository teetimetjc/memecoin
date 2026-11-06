"""When a window goes 95/5 early, is the 5c side worth 50c? Read-only.

THE IDEA. Three minutes into a fifteen-minute window the price has already
gone to 95/5. Buy the cheap side for about 50c of stake and rest a sell
order above it, so the position closes itself if the price ticks back.

IT IS TWO DIFFERENT BETS AND THEY HAVE DIFFERENT ANSWERS.

  THE OUTCOME BET -- hold to expiry. The favourite-longshot bias, the same
  effect this project's whole strategy rests on, says the longshot is
  OVERPRICED. Buying the 5c side is buying the side the bias says costs too
  much, so the prior is negative. This measures it anyway: what share of
  these cheap sides actually win, against what they cost.

  THE PATH BET -- sell on a tick back. This does not care who wins. It asks
  whether the cheap side's BID later reaches a multiple of what it cost. A
  side can be hopeless at expiry and still trade at twice its price an hour
  -- or here, nine minutes -- earlier.

Both are computed, separately, because a positive path result alongside a
negative outcome result is the whole case for the idea, and reporting a
blended number would hide exactly that.

THE PRICES ARE READ FROM THE RIGHT SIDE OF THE BOOK. Kalshi runs ONE book:
a resting YES bid at p IS a NO ask at 1-p. So buying the NO costs
(1 - yes_bid) and selling it later hits (1 - yes_ask), and getting this
backwards would make a spread look like a profit. The collector's asksz is
the size behind the YES ask and bidsz the size behind the YES bid, so the
depth quoted for each side is the one that side would actually sweep.

THE FEE IS NOT A ROUNDING ERROR HERE. At 5c the Kalshi fee is
0.07 * n * p * (1-p), rounded UP at the order level: ten contracts at 5c
costs 4c of fee on 50c of stake, about 8%. On a bet whose edge would have
to come from a few cents of movement, that is most of the question, so it
is charged on every entry below rather than mentioned in a footnote.

WHAT THIS CANNOT SEE. The collector samples six instants -- 14, 12, 9, 6, 3
and 1 minutes out. A price that spiked to twice its value BETWEEN two
samples is invisible here, so the path result is a LOWER BOUND on how often
a resting sell would have filled. It is also quotes, not fills: at 5c a
one-cent spread is a fifth of the price, and the standing finding of this
project is that quotes at the extremes do not survive contact with fills.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import os
import statistics as st

import predictor as P
import collect15 as C
import oddsmath as M

# PRE-REGISTERED, before any number is read.
EARLY = int(os.environ.get("REVERT_EARLY") or 12)      # 3 min into a window
EXTREME = float(os.environ.get("REVERT_EXTREME") or 0.95)
STAKE = float(os.environ.get("REVERT_STAKE") or 0.50)
TARGETS = (2.0, 3.0)
LATER = (9, 6, 3, 1)
MIN_N = int(os.environ.get("REVERT_MIN_N") or 100)


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    print("=" * 100)
    print(f"95/5 EARLY REVERSION: buy the cheap side at T-{EARLY}, "
          f"${STAKE:.2f} of stake")
    print(f"  Pre-registered: extreme at >= {EXTREME:.2f}, sell targets "
          f"{TARGETS}, fee charged on entry.")
    print("=" * 100)

    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(C.SHEET).get_all_values()
    if len(rows) < 2:
        print("\n  the collector sheet is empty.")
        return 1
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    total = graded = extreme = 0
    recs = []
    for r in rows[1:]:
        if not r or not g(r, "Ticker"):
            continue
        total += 1
        res = str(g(r, "Result")).strip().lower()
        if res not in ("yes", "no"):
            continue
        graded += 1
        yb = f(g(r, f"bid{EARLY}"))
        ya = f(g(r, f"ask{EARLY}"))
        if yb is None or ya is None or not (0 < yb <= ya < 1):
            continue

        # Which side is cheap, and what does it cost to BUY it?
        if ya >= EXTREME:
            side = "NO"
            cost = 1.0 - yb               # NO ask = 1 - YES bid
            depth = f(g(r, f"bidsz{EARLY}"))   # sweeps the resting YES bids
            won = (res == "no")
        elif ya <= 1.0 - EXTREME:
            side = "YES"
            cost = ya
            depth = f(g(r, f"asksz{EARLY}"))
            won = (res == "yes")
        else:
            continue
        if not (0.0 < cost < 0.5):
            continue
        extreme += 1

        n = int(STAKE / cost) if cost else 0
        if n <= 0:
            continue
        fee = M.fee_cents(n, cost) / 100.0
        spent = n * cost + fee

        # THE PATH: does the cheap side's BID reach a multiple of cost?
        best = 0.0
        hit = {t: None for t in TARGETS}
        for m in LATER:
            lb = f(g(r, f"bid{m}"))
            la = f(g(r, f"ask{m}"))
            if lb is None or la is None:
                continue
            sell = (1.0 - la) if side == "NO" else lb
            if sell is None or sell <= 0:
                continue
            best = max(best, sell)
            for t in TARGETS:
                if hit[t] is None and sell >= t * cost:
                    hit[t] = m

        recs.append(dict(side=side, cost=cost, n=n, spent=spent, won=won,
                         best=best, hit=hit, depth=depth,
                         close=str(g(r, "Close Time"))[:10]))

    print(f"\n  {total} collector row(s), {graded} graded")
    print(f"  {extreme} reached {EXTREME:.0%}/{1-EXTREME:.0%} at T-{EARLY}"
          + (f"  ({100.0 * extreme / graded:.1f}% of graded windows)"
             if graded else ""))
    if len(recs) < MIN_N:
        print(f"\n  ONLY {len(recs)} USABLE. Below the {MIN_N} minimum set "
              f"before the run; no verdict is offered.")
        return 0

    # ------------------------------------------------------------- outcome
    wins = sum(1 for r in recs if r["won"])
    spent = sum(r["spent"] for r in recs)
    ret = sum(r["n"] for r in recs if r["won"])   # $1 per winning contract
    costs = [r["cost"] for r in recs]
    print("\n" + "-" * 100)
    print("THE OUTCOME BET -- buy and hold to expiry")
    print("-" * 100)
    print(f"  n {len(recs)}   mean cost {100*st.mean(costs):.2f}c   "
          f"the cheap side won {100.0*wins/len(recs):.2f}% of the time")
    print(f"  spent ${spent:,.2f}   returned ${ret:,.2f}   "
          f"net ${ret - spent:+,.2f}   ROI {100.0*(ret-spent)/spent:+.2f}%")
    be = 100.0 * st.mean([r["spent"] / r["n"] for r in recs])
    print(f"  break-even needs {be:.2f}% (the mean all-in cost per contract)")
    print(f"  -> the longshot is "
          + ("UNDERpriced here" if 100.0*wins/len(recs) > be
             else "OVERpriced here, which is the favourite-longshot bias"))

    # ---------------------------------------------------------------- path
    print("\n" + "-" * 100)
    print("THE PATH BET -- rest a sell above the entry")
    print("-" * 100)
    for t in TARGETS:
        k = sum(1 for r in recs if r["hit"][t] is not None)
        print(f"  reached {t:.0f}x the entry at some later sample: "
              f"{k} of {len(recs)}  ({100.0*k/len(recs):.1f}%)")
    # What the auto-sale would actually have returned.
    net = 0.0
    for r in recs:
        if r["hit"][2.0] is not None:
            net += r["n"] * (2.0 * r["cost"]) - r["spent"]
        else:
            net += (r["n"] if r["won"] else 0.0) - r["spent"]
    print(f"\n  sell at 2x when reached, else hold to expiry:")
    print(f"    net ${net:+,.2f} on ${spent:,.2f} staked   "
          f"ROI {100.0*net/spent:+.2f}%")

    # --------------------------------------------------------------- depth
    deps = [r["depth"] for r in recs if r["depth"] is not None]
    if deps:
        deps.sort()
        need = st.mean([r["n"] for r in recs])
        print(f"\n  depth on the cheap side at entry: median "
              f"{deps[len(deps)//2]:.0f} contracts, "
              f"10th pct {deps[max(0,len(deps)//10)]:.0f}")
        print(f"  a ${STAKE:.2f} order needs about {need:.0f} contracts")

    # --------------------------------------------------------------- halves
    recs.sort(key=lambda r: r["close"])
    mid = len(recs) // 2
    for label, part in (("first half", recs[:mid]), ("second half", recs[mid:])):
        sp = sum(r["spent"] for r in part)
        nt = 0.0
        for r in part:
            if r["hit"][2.0] is not None:
                nt += r["n"] * (2.0 * r["cost"]) - r["spent"]
            else:
                nt += (r["n"] if r["won"] else 0.0) - r["spent"]
        print(f"  {label:<12} n {len(part):>5}   "
              f"ROI {100.0*nt/sp if sp else 0:+.2f}%")

    print("\n" + "=" * 100)
    print("  THE PATH NUMBER IS A LOWER BOUND. The collector samples six")
    print("  instants, so a price that doubled between two of them is")
    print("  invisible here and a resting sell would have caught it.")
    print()
    print("  AND IT IS QUOTES, NOT FILLS. At 5c a one-cent spread is a fifth")
    print("  of the price, and every quote-based edge this project has found")
    print("  has shrunk or vanished on contact with real fills. Treat a")
    print("  positive result as a reason to paper-test, never to bet.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

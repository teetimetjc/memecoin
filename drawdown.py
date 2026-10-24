"""The real record replayed at every flat stake: peak, trough, worst run.

THE QUESTION. "Predict the most I'd have lost and won if I'd used the same
stake the whole time." flat5.py answered half of it -- the TOTAL at a flat $5
-- and the total is the least useful number here. A run that ends +$30 after
being -$40 along the way needed a bankroll that could survive -$40, and the
total never mentions it.

So this reports the PATH, at several stakes:

    final net          where it ended
    peak               the best it ever stood
    trough             the worst it ever stood
    max drawdown       the largest peak-to-trough fall -- the money the
                       bankroll actually had to absorb
    worst window       the biggest single-window loss, since legs in one
                       window resolve together and are one bet in practice
    longest losing run of consecutive losing windows

NOTHING IS SIMULATED. Every bet is a real bet that really happened:

  THE FILL PRICE IS THE ONE PAID, volume-weighted across the order's pieces,
  from Kalshi's fills endpoint -- not the quote, not the limit. The sheet's
  Cost column is contracts times the LIMIT and would overstate every spend,
  so it is not used.

  THE OUTCOME IS KALSHI'S OWN SETTLEMENT. Never re-derived from a price.

  ONLY THE STAKE CHANGES, and it changes the contract count the way live.py
  does: floor(stake / ceil(fill + slip buffer)). Integer arithmetic, kept,
  because at a 94c limit $5 and $6 both buy 5 contracts and that rounding is
  real money.

  THE FEE IS RECOMPUTED at the new count: 0.07*n*p*(1-p), the formula
  verified against 162 of 163 fills.

THE ASSUMPTION, and it is the whole weakness: that the same price was
available at the larger size. fills.py measured slippage flat from 2 to 7
contracts and found no size penalty, so modest steps are defensible. A $25
stake buying 27 contracts is NOT covered by that evidence and this file says
so rather than quietly extrapolating.

WHAT IT STILL CANNOT MODEL: the stake ladder would have fired differently,
the day-stop arithmetic would have moved, and a bigger loss early changes
what was affordable later. This is "the same bets at one size", not "what
the bot would have done if configured this way from the start".

Read-only.
"""
import collections
import math
import statistics as st

import tally as T

RESTART = "2026-10-14 00:00"
STAKES = (3.0, 5.0, 6.0, 7.0, 10.0, 15.0)
SLIP_BUFFER_CENTS = 3
BANKROLL = 106.0          # what the account started this run with


def fee(n, px):
    return 0.07 * n * px * (1.0 - px)


def main():
    bets = T.rows_since(RESTART)
    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break

    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    for _tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            x["_share"] = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0

    fills = T.actual_costs()
    usable, skipped = [], 0
    for b in sorted(bets, key=lambda x: x["ts"]):
        if str(b.get("side") or "").upper() not in ("UP", "YES"):
            continue
        s = setts.get(b["ticker"])
        f = fills.get(b["oid"])
        if s is None or not (f and f.get("qty") and sum(f["qty"])):
            skipped += 1
            continue
        qsum = sum(f["qty"])
        px = sum(p * n for p, n in zip(f["px"], f["qty"])) / qsum
        if not (0 < px < 1):
            skipped += 1
            continue
        usable.append(dict(ts=b["ts"], px=px,
                           won=(s["rev"] * b.get("_share", 1.0)) > 0))

    print("=" * 108)
    print("THE REAL RECORD REPLAYED AT EVERY FLAT STAKE")
    print("  same bets, same fill prices, same outcomes -- only the size "
          "changes")
    print("=" * 108)
    print(f"  {len(usable)} settled YES bets rebuilt"
          + (f", {skipped} skipped for want of a settlement or a verified fill"
             if skipped else ""))
    if len(usable) < 30:
        print("  too few to replay")
        return 0
    print(f"  from {usable[0]['ts']} to {usable[-1]['ts']}\n")

    print(f"  {'stake':>6} {'bets':>5} {'final':>9} {'peak':>9} "
          f"{'trough':>9} {'max DD':>9} {'worst win':>10} {'lose run':>9} "
          f"{'% of $' + str(int(BANKROLL)):>10}")
    print("  " + "-" * 104)

    for stake in STAKES:
        # Group into windows: legs closing together resolve together, so a
        # window is the unit of risk even though each leg is its own bet.
        wins_by_window = collections.defaultdict(float)
        equity, peak, trough, maxdd = 0.0, 0.0, 0.0, 0.0
        n_bets = 0
        for r in usable:
            limit_c = min(99.0, math.ceil(r["px"] * 100 + SLIP_BUFFER_CENTS))
            k = int(stake / (limit_c / 100.0))
            if k < 1:
                continue
            n_bets += 1
            cost = k * r["px"] + fee(k, r["px"])
            pnl = (k * 1.0 if r["won"] else 0.0) - cost
            equity += pnl
            wins_by_window[r["ts"]] += pnl
            peak = max(peak, equity)
            trough = min(trough, equity)
            maxdd = max(maxdd, peak - equity)

        # Worst single window and the longest run of losing windows.
        wk = sorted(wins_by_window)
        worst_window = min(wins_by_window.values()) if wins_by_window else 0.0
        run = best_run = 0
        for t in wk:
            if wins_by_window[t] < 0:
                run += 1
                best_run = max(best_run, run)
            else:
                run = 0
        print(f"  ${stake:>5.0f} {n_bets:>5} {equity:>+8.2f} {peak:>+8.2f} "
              f"{trough:>+8.2f} {maxdd:>8.2f} {worst_window:>+9.2f} "
              f"{best_run:>9} {100 * maxdd / BANKROLL:>9.1f}%")

    print()
    print("=" * 108)
    print("  HOW TO READ IT")
    print()
    print("  MAX DD is the money the bankroll had to absorb, and it is the")
    print("  number that decides whether a stake is survivable -- not the")
    print("  final column. A run ending positive after a drawdown deeper")
    print("  than the bankroll never actually happens: the account is empty")
    print("  before the recovery arrives.")
    print()
    print("  Everything scales linearly, so the ranking never changes and")
    print("  no stake here is 'better'. That is the point: the final column")
    print("  is the only thing that differs between the rows in substance,")
    print("  and it is a risk number rather than a return number.")
    print()
    print("  THE WORST WINDOW column is the one-day version of the same")
    print("  thing. Legs in a window resolve together about 96% of the")
    print("  time, so that figure is a single bad roll, not a bad month.")
    print()
    print("  AND THE LIMIT OF THE EVIDENCE: fills.py measured slippage flat")
    print("  from 2 to 7 contracts, so $3-$7 rests on measurement. The $10")
    print("  and $15 rows assume the same prices at 10 and 16 contracts,")
    print("  which is an extrapolation -- the book was never tested there at")
    print("  this offset, and a thin top-of-book would make those rows")
    print("  optimistic in a way this file cannot detect.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

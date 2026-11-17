"""Every series on Kalshi, ranked for a $40 account. Places nothing.

WHY THIS EXISTS. Seven months of work on this account has traded exactly one
kind of product: fifteen-minute crypto, plus WTI and GOLD on the same clock.
allmkt.py widened that to every FIFTEEN-MINUTE series -- which is still the
same trap, because the problem was never which coin. It was the clock.

THE FEE IS CHARGED ONCE PER MARKET: 0.07 x p x (1-p) per contract. Hold a
fifteen-minute market and you pay it for fifteen minutes of exposure; hold a
market that settles in a month and you pay the same fee once for a month. On
a 91c contract that is about 0.57c, which is 6% of the 9c on offer -- per
cycle. Trading the 15-minute clock around the clock means paying that 96
times a day. Our entire approach maximised fee drag, and then we went looking
for an edge inside what was left.

The fifteen-minute clock is also where every other bot is, for the same
reason it is where we were: it is the product that looks most like a slot
machine. A market that settles in three weeks cannot be scalped and is
therefore contested by far fewer automated participants.

SO THIS CENSUSES THE WHOLE EXCHANGE and ranks series by what actually
matters for a small account:

  LIFETIME        longer is better, because the fee is paid once over it
  FEE PER DAY     the thing being minimised, computed rather than asserted
  VOLUME          an edge you cannot fill is worth nothing; $40 needs very
                  little, but it needs more than zero
  SPREAD          what entry costs before anything else happens
  SETTLED COUNT   whether there is enough history to backtest at all

IT RANKS, IT DOES NOT RECOMMEND. A short lifetime and a wide spread rule a
series out; nothing here rules one IN. That takes a measured edge against
something knowable, which is the next piece of work and not this one.

READ-ONLY. No order, no row, no setting changed.
"""
import collections
import os
import statistics as st
import time

import backfill as B

BUDGET_S = int(os.environ.get("KCENSUS_BUDGET_S") or 1500)
SAMPLE = int(os.environ.get("KCENSUS_SAMPLE") or 40)
# A market this short is the trap we are trying to leave.
SHORT_H = 1.0


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    print("=" * 112)
    print("EVERY KALSHI SERIES, RANKED FOR A SMALL ACCOUNT")
    print("=" * 112)
    print("  The fee is 0.07 x p x (1-p) per contract, charged ONCE per")
    print("  market. A 15-minute market pays it 96 times a day; a monthly")
    print("  market pays it once. That ratio is the point of this census.")
    print("  READ-ONLY: no order, no row, no setting changed.\n")

    d, err = B.get("/trade-api/v2/series")
    ser = sorted({str(s.get("ticker") or "")
                  for s in ((d or {}).get("series") or []) if s.get("ticker")})
    if not ser:
        raise SystemExit(f"  no series returned ({err}); nothing to census")
    print(f"  {len(ser):,} series on the exchange\n")

    end = time.time() + BUDGET_S
    rows, checked, skipped = [], 0, 0
    for tk in ser:
        if time.time() > end:
            print(f"  [budget reached after {checked} series; "
                  f"{len(ser) - checked} not inspected]")
            break
        try:
            mk = B.settled_markets(tk, SAMPLE)
        except Exception:
            skipped += 1
            continue
        checked += 1
        if not mk:
            continue
        lives, vols, spreads, prices = [], [], [], []
        for m in mk:
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is not None and c is not None and c > o:
                lives.append((c - o) / 3600.0)
            v = f(m.get("volume"))
            if v is not None:
                vols.append(v)
            bid, ask = f(m.get("yes_bid")), f(m.get("yes_ask"))
            if bid is not None and ask is not None and ask > bid:
                spreads.append(ask - bid)
                prices.append((ask + bid) / 2.0)
        if not lives:
            continue
        life_h = st.median(lives)
        # THE NUMBER THIS CENSUS EXISTS FOR. Fee per contract at the typical
        # price, divided by how many days the position is held: what the
        # exchange charges per day of exposure.
        p = (st.median(prices) / 100.0) if prices else 0.5
        p = min(max(p, 0.01), 0.99)
        fee_c = 100.0 * 0.07 * p * (1.0 - p)
        days = max(life_h / 24.0, 1.0 / 96.0)
        rows.append(dict(
            tk=tk, n=len(mk), life_h=life_h,
            fee_c=fee_c, fee_per_day=fee_c / days,
            vol=(st.median(vols) if vols else 0.0),
            spread=(st.median(spreads) if spreads else None),
            price=100.0 * p))

    print(f"  {checked:,} series inspected, {len(rows)} with usable "
          f"markets, {skipped} errored\n")
    if not rows:
        raise SystemExit("  nothing usable; no ranking produced")

    # ------------------------------------------------------ the whole picture
    short = [r for r in rows if r["life_h"] < SHORT_H]
    longr = [r for r in rows if r["life_h"] >= SHORT_H]
    print(f"{'-' * 112}")
    print("THE SHAPE OF THE EXCHANGE")
    print("-" * 112)
    print(f"  series living under {SHORT_H:.0f}h (the trap we were in): "
          f"{len(short)}")
    print(f"  series living {SHORT_H:.0f}h or longer: {len(longr)}")
    if short:
        print(f"  median fee/day on the short ones: "
              f"{st.median([r['fee_per_day'] for r in short]):,.1f}c")
    if longr:
        print(f"  median fee/day on the longer ones: "
              f"{st.median([r['fee_per_day'] for r in longr]):,.3f}c")

    # ------------------------------------------- what a $40 account can use
    # Filters, each with a reason and each able to exclude everything:
    #   lifetime >= 1h   leaves the clock that maximised fee drag
    #   volume  >= 100   $40 needs almost nothing, but not nothing
    #   spread  <= 4c    a wider spread costs more than any edge we have
    #                    ever measured on this account
    cands = [r for r in longr
             if r["vol"] >= 100 and r["spread"] is not None
             and r["spread"] <= 4.0 and r["n"] >= 20]
    print(f"\n{'-' * 112}")
    print("SERIES WORTH TESTING -- long enough, liquid enough, tight enough")
    print("-" * 112)
    print(f"  {'series':<26}{'settled':>8}{'life h':>9}{'fee/day c':>11}"
          f"{'med vol':>10}{'spread c':>10}{'med px':>8}")
    if not cands:
        print("  NONE PASSED. That is a finding: on this sample no series")
        print("  outside the short clock is both liquid and tight enough,")
        print("  and no amount of strategy work fixes a spread.")
    for r in sorted(cands, key=lambda r: r["fee_per_day"])[:30]:
        print(f"  {r['tk']:<26}{r['n']:>8}{r['life_h']:>9.1f}"
              f"{r['fee_per_day']:>11.3f}{r['vol']:>10,.0f}"
              f"{r['spread']:>10.1f}{r['price']:>8.1f}")

    print(f"\n{'-' * 112}")
    print("FOR CONTRAST -- what we have actually been trading")
    print("-" * 112)
    print(f"  {'series':<26}{'settled':>8}{'life h':>9}{'fee/day c':>11}"
          f"{'med vol':>10}{'spread c':>10}{'med px':>8}")
    ours = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
            "KXWTI15M")
    for r in rows:
        if r["tk"] in ours:
            print(f"  {r['tk']:<26}{r['n']:>8}{r['life_h']:>9.1f}"
                  f"{r['fee_per_day']:>11.1f}{r['vol']:>10,.0f}"
                  f"{(r['spread'] if r['spread'] is not None else -1):>10.1f}"
                  f"{r['price']:>8.1f}")

    print(f"\n{'=' * 112}")
    print("  WHAT THIS DOES AND DOES NOT SAY. It rules series OUT on cost")
    print("  and liquidity, which is cheap and reliable. It rules nothing")
    print("  IN: a tight, long-dated, liquid series is merely a place where")
    print("  an edge would survive if one existed. Finding one still needs")
    print("  something knowable to price against -- a published forecast, a")
    print("  consensus number, a rule the market has to follow -- measured")
    print("  with the same holdout and permutation discipline as everything")
    print("  else here. This census only says where to look.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

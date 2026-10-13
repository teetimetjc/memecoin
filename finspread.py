"""Is the Financials spread actually earnable, or just wide while nobody looks?

The category survey put Financials at a 7c median spread against a 3.39c
round-trip fee, with 1,354 quotes -- four times the activity of any other
wide-spread category. That is the only row on the exchange where quoting
both sides clears the fee AND somebody is trading. This asks whether it
survives contact with detail.

THE THREE WAYS A WIDE SPREAD TURNS OUT TO BE NOTHING, each measured here:

  IT IS WIDE ONLY WHEN DEAD. A 12c spread at 4am and 1c when the market is
  busy averages to 7c and is earnable in neither state. Spread is therefore
  reported BY HOUR, not pooled.

  IT IS ONE SERIES. A category median hides the case where one illiquid
  series carries the whole number and the rest are tight. Reported per
  series.

  IT NEVER ROUND TRIPS. Earning a spread needs BOTH sides filled. A market
  that drifts one way all day fills one leg and leaves inventory. Counted as
  the share of markets where the price visited both a posted bid and a
  posted ask.

WHAT THIS STILL CANNOT SEE, and it is the thing most likely to kill the
idea: queue position and depth. The candle says a quote existed at a price,
not that a resting order would have been hit, nor how much size sat in front
of it. Every number here is an optimistic bound.

Read-only.
"""
import collections, statistics as st, sys, time
import backfill as B

PER_SERIES = 10
MIN_SETTLED = 20
HOURS_BACK = 120
PERIOD = 60
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2200
# What a quoter would post inside the touch, each side.
STEP = 0.01


def fee_rt(p):
    return 2 * 0.07 * p * (1 - p)


def main():
    print("=" * 100)
    print("IS THE FINANCIALS SPREAD EARNABLE?")
    print("=" * 100)
    d, _ = B.get("/trade-api/v2/series")
    ser = [s for s in ((d or {}).get("series") or [])
           if str(s.get("category") or "").lower().startswith("financ")]
    print(f"  {len(ser)} Financials series\n")

    started = time.time()
    perseries = []
    byhour = collections.defaultdict(list)
    roundtrips = [0, 0]
    for s in ser:
        if time.time() - started > BUDGET:
            break
        tk = str(s.get("ticker") or "")
        if not tk:
            continue
        try:
            mk = B.settled_markets(tk, 60)
        except Exception:
            continue
        if len(mk) < MIN_SETTLED:
            continue
        sp, px, nq = [], [], 0
        for m in mk[:PER_SERIES]:
            if time.time() - started > BUDGET:
                break
            end = B.ts(m.get("close_time"))
            if end is None:
                continue
            dd, _e = B.get(
                f"/trade-api/v2/series/{tk}/markets/{m.get('ticker')}/candlesticks",
                start_ts=int(end) - HOURS_BACK * 3600, end_ts=int(end),
                period_interval=PERIOD)
            lows, highs = [], []
            for c in (dd or {}).get("candlesticks") or []:
                va, vb = c.get("yes_ask"), c.get("yes_bid")
                a = B._f(va.get("close_dollars")) if isinstance(va, dict) else None
                b = B._f(vb.get("close_dollars")) if isinstance(vb, dict) else None
                t = B._f(c.get("end_period_ts"))
                if a is None or b is None or t is None or not (0 < b <= a < 1):
                    continue
                sp.append(100 * (a - b))
                px.append(100 * (a + b) / 2)
                byhour[time.gmtime(t).tm_hour].append(100 * (a - b))
                nq += 1
                # Would a quote posted one cent inside each side be reached?
                lows.append(a)       # the ask came down to here
                highs.append(b)      # the bid came up to here
            if lows and highs:
                roundtrips[1] += 1
                # both legs touched: someone sold into our bid AND bought our ask
                if min(lows) <= st.median(px) / 100 + STEP and \
                   max(highs) >= st.median(px) / 100 - STEP:
                    roundtrips[0] += 1
            time.sleep(B.PAUSE)
        if nq >= 40:
            perseries.append((tk, len(mk), nq, st.median(sp), st.median(px)))

    print(f"  {'series':<28} {'settled':>8} {'quotes':>7} {'spread':>8} "
          f"{'price':>7} {'fee r/t':>8} {'sp-fee':>8}")
    for tk, n, nq, sp, px in sorted(perseries, key=lambda x: -x[3]):
        f = 100 * fee_rt(px / 100)
        print(f"  {tk:<28} {n:>8} {nq:>7} {sp:>7.1f}c {px:>6.1f}c "
              f"{f:>7.2f}c {sp-f:>+7.1f}c")

    print(f"\n  SPREAD BY HOUR (UTC) -- wide only when dead is the failure mode:")
    print(f"  {'hour':>5} {'quotes':>8} {'median spread':>15}")
    for h in sorted(byhour):
        v = byhour[h]
        if len(v) < 15:
            continue
        print(f"  {h:>3}:00 {len(v):>8} {st.median(v):>14.1f}c")

    print(f"\n  ROUND TRIP: both sides reachable in "
          f"{roundtrips[0]} of {roundtrips[1]} markets"
          + (f" ({100*roundtrips[0]/roundtrips[1]:.0f}%)"
             if roundtrips[1] else ""))
    print("\n" + "=" * 100)
    print("  EVERY NUMBER ABOVE IS AN OPTIMISTIC BOUND. A candle says a quote")
    print("  existed at a price, not that a resting order would have been")
    print("  filled there, and says nothing about how much size was queued in")
    print("  front of it. Depth and queue position are what actually decide a")
    print("  market maker's income, and neither is visible from here.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

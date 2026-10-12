"""How often does a genuinely cheap offer appear, and could it be taken?

On 2026-10-11 an ETH order aimed at 90.1c came back filled at 54.0c, because
someone had a sell order resting there and our order swept it. That single
bet is nineteen percent of the live profit. The question this answers is
whether such offers are common enough to go looking for, or whether that was
a once-a-month gift.

WHAT IS MEASURED. Kalshi's candlesticks carry the per-minute LOW of the yes
ask, not only its close. So for each minute of each market, the gap between
the ask's close and its low is how far the offer side dipped WITHIN that
minute. A large gap means someone briefly offered far below the going rate.

TWO THINGS THAT WOULD MAKE A LARGE GAP MEAN NOTHING, both handled:

  A GENUINE PRICE MOVE. If the market really did fall from 90c to 54c and
  stayed there, the dip is not a mispricing, it is the news. So a dip only
  counts when the ask RECOVERS -- the next minute's close must come back to
  within RECOVER_C of the pre-dip close. A price that fell and stayed fell
  is excluded.

  AN EMPTY BOOK. A missing or crossed quote is not a price. Both sides must
  be present and in order, with a spread under 25c, or the minute is
  skipped. This is the same guard slowbook has carried since September, and
  the one path.py was written without -- which made it report winners
  crashing to single digits.

WHAT IT CANNOT TELL US, and this is the important caveat. A candle says a
cheap offer existed somewhere inside a sixty-second window. It does not say
for how long, or whether anything was still there a second later. These
offers are taken in milliseconds by bots that do nothing else; a ten-second
poll from a shared runner is not in that race. So a high count here is a
reason to investigate, never on its own a reason to build.

Read-only. Writes nothing.
"""

import collections
import math
import random
import sys
import time

import backfill as B
import score as S


OFFSETS = tuple(range(1, 16))
# A dip counts at these depths below the minute's closing ask.
DEPTHS = (5, 10, 20, 30)
# How close the ask must come back for the dip to count as a dislocation
# rather than a real move.
RECOVER_C = 3.0
PER_SERIES = 9000
SEED = 20261012
MAJORS = list(S.MAJORS)


def candles(series, ticker, close):
    """Per-minute (offset -> (ask_close, ask_low, bid_close)) for one market."""
    end = B.ts(close)
    if end is None:
        return {}
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(end) - 17 * 60, end_ts=int(end), period_interval=1)
    if not d:
        return {}
    out = {}
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or t >= end:
            continue

        def side(k, f):
            v = c.get(k)
            return B._f(v.get(f)) if isinstance(v, dict) else None

        ac = side("yes_ask", "close_dollars")
        al = side("yes_ask", "low_dollars")
        bc = side("yes_bid", "close_dollars")
        if ac is None or bc is None:
            continue
        if not (0 < bc <= ac < 1) or (ac - bc) > 0.25:
            continue
        if al is None or not (0 < al <= ac):
            al = ac
        off = int(round((end - t) / 60.0))
        if off in OFFSETS:
            out[off] = (ac, al, bc)
    return out


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
    print("=" * 94)
    print("HOW OFTEN DOES A CHEAP OFFER APPEAR?")
    print("  per-minute dip of the yes ask below its own close, counted only "
          "when the price recovers")
    print("=" * 94)

    mk = []
    for s in MAJORS:
        got = B.settled_markets(s, PER_SERIES)
        print(f"  {s:<12} {len(got)} settled markets")
        mk.extend((s, m) for m in got)
    random.Random(SEED).shuffle(mk)
    print(f"  {len(mk)} available; sampling within a {budget}s budget\n")

    minutes = 0
    markets = 0
    hits = {d: 0 for d in DEPTHS}
    deep = []
    started = time.time()
    for series, m in mk:
        if time.time() - started > budget:
            break
        q = candles(series, str(m.get("ticker")), m.get("close_time"))
        if not q:
            continue
        markets += 1
        offs = sorted(q)
        for i, off in enumerate(offs):
            ac, al, bc = q[off]
            minutes += 1
            dip = 100.0 * (ac - al)
            if dip < DEPTHS[0]:
                continue
            # The recovery test: the NEXT minute in time is the one with the
            # smaller offset, because offsets count backwards from the close.
            nxt = None
            for o2 in offs:
                if o2 == off - 1:
                    nxt = q[o2]
                    break
            if nxt is None:
                continue
            if 100.0 * abs(nxt[0] - ac) > RECOVER_C:
                continue                 # it moved and stayed moved
            for d in DEPTHS:
                if dip >= d:
                    hits[d] += 1
            if dip >= 20:
                deep.append((series, str(m.get("ticker")), off, ac, al))
        time.sleep(B.PAUSE)

    el = time.time() - started
    print(f"  {markets} markets, {minutes:,} quoted minutes, in {el:.0f}s\n")
    if not minutes:
        print("  no usable minutes -- raise the budget")
        return 0

    print(f"  {'dip of at least':>17} {'minutes':>9} {'share':>9} "
          f"{'per market':>12} {'per day*':>10}")
    # Markets run four an hour on five majors: 480 market-windows a day.
    per_day_markets = 480.0
    for d in DEPTHS:
        share = hits[d] / minutes
        per_mkt = hits[d] / markets if markets else 0
        print(f"  {d:>14}c   {hits[d]:>9,} {100*share:>8.3f}% "
              f"{per_mkt:>12.4f} {per_mkt*per_day_markets:>10.1f}")
    print("\n  * per day assumes 480 market-windows a day across the five "
          "majors")

    if deep:
        print(f"\n  a few of the {len(deep)} dips of 20c or more:")
        for s, tk, off, ac, al in deep[:8]:
            print(f"    {s:<10} T-{off:<3} ask {100*ac:.0f}c, "
                  f"dipped to {100*al:.0f}c")

    print("\n" + "=" * 94)
    big = hits[20]
    per_day = (big / markets * per_day_markets) if markets else 0
    print(f"  dips of 20c or more: about {per_day:.1f} a day across the five "
          "majors")
    print()
    if per_day < 1:
        print("  TOO RARE TO BUILD FOR. Less than one a day, each lasting")
        print("  seconds, against bots that do nothing else. A test would")
        print("  spend weeks to confirm a number this small.")
    elif per_day < 10:
        print("  PRESENT BUT THIN. Enough to be real, not enough to chase")
        print("  from a ten-second poll on a shared runner. The honest next")
        print("  step is measuring how LONG they last, not building a taker.")
    else:
        print("  COMMON ENOUGH TO INVESTIGATE. The next question is duration:")
        print("  a candle says the offer existed inside a minute, not that it")
        print("  survived a second. That needs the live book, not history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

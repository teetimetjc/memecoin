"""The entry-time curve at one-minute resolution. Is it smooth or jagged?

The collector samples six offsets -- 14, 12, 9, 6, 3, 1 -- and scored across
those, the live rule looked best at T-6 and worst at T-1, with the sign
flipping somewhere between six and three minutes out. Kalshi's candlesticks
are PER MINUTE, so every offset in between can be reconstructed.

THIS IS NOT A SEARCH FOR THE BEST MINUTE, and building it as one would be a
mistake. The T-9 versus T-6 gap was 0.16 points per dollar; the gap between
adjacent minutes will be smaller than that and smaller than the noise.
Picking the winner of fourteen columns measured that finely is choosing the
luckiest one, which is exactly how T-9 and then T-6 were arrived at.

WHAT IT IS FOR IS THE SHAPE.

  A SMOOTH ARC -- rising from T-14, a plateau somewhere in the middle,
  falling away through T-3 to T-1 -- is a mechanism: the price of a
  favourite rises faster than its probability as the close approaches, so
  late entries pay for certainty already in the price. A shape like that is
  hard to produce by chance across fourteen columns.

  A JAGGED MESS -- T-8 good, T-7 bad, T-6 good -- means the peaks are noise,
  T-6 means nothing, and the entry-time question should be dropped rather
  than refined.

Either answer is worth the compute. The jagged one is arguably worth more,
because it closes the question instead of leaving it open.

AND NOTE WHAT A PLATEAU WOULD IMPLY. If the good region runs from about 12
to 6, its centre is near T-9 -- where the live rule already sits. The middle
of a plateau is the robust place to stand, because it survives being wrong
about exactly where the peak is. The edge of one does not.

EVERY OFFSET SCORES A DIFFERENT SET OF MARKETS. The rule takes whatever is
in the 90-96c band at that instant, and band membership changes as a window
runs: early, few markets have a clear favourite; late, some have passed 96c
and left. Bet counts are printed for exactly this reason -- a column with
more bets is not the same column with better timing.

Writes nothing. Reads the exchange and prints a table.
"""

import collections
import math
import random
import sys
import time

import backfill as B
import score as S


BAND = (0.90, 0.96)
OFFSETS = tuple(range(1, 15))          # T-1 .. T-14, every minute
STAKE = 4.0
MAJORS = list(S.MAJORS)
PER_SERIES = 9000
# Sampling is deterministic so two runs of the same budget see the same
# markets and can be compared. Shuffled rather than taken in cursor order,
# because cursor order is reverse-chronological and a truncated run would
# otherwise score only the most recent days.
SEED = 20261011


def fee(n, p):
    if n <= 0 or not (0 < p < 1):
        return 0.0
    return math.ceil(0.07 * n * p * (1 - p) * 100) / 100.0


def quotes(series, ticker, close):
    """Per-minute yes ask for one market, keyed by minutes before close."""
    end = B.ts(close)
    if end is None:
        return {}
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(end) - 16 * 60, end_ts=int(end), period_interval=1)
    if not d:
        return {}
    out = {}
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or t >= end:
            continue

        def side(k):
            v = c.get(k)
            return B._f(v.get("close_dollars")) if isinstance(v, dict) else None

        bid, ask = side("yes_bid"), side("yes_ask")
        # BOTH SIDES OR NEITHER, with a sane spread. An empty book is not a
        # price, and reading one as a price is what made path.py report
        # winners crashing to single digits before it was fixed.
        if bid is None or ask is None:
            continue
        if not (0 < bid <= ask < 1) or (ask - bid) > 0.25:
            continue
        off = int(round((end - t) / 60.0))
        if off in OFFSETS and off not in out:
            out[off] = ask
    return out


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
    print("=" * 96)
    print(f"THE ENTRY-TIME CURVE, ONE MINUTE AT A TIME   "
          f"{100*BAND[0]:.0f}-{100*BAND[1]:.0f}c YES, ${STAKE:.0f} a bet, held")
    print("  Looking for a SHAPE, not a best minute. Adjacent minutes cannot "
          "be told apart at this noise level.")
    print("=" * 96)

    mk = []
    for s in MAJORS:
        got = B.settled_markets(s, PER_SERIES)
        print(f"  {s:<12} {len(got)} settled markets")
        mk.extend((s, m) for m in got)
    random.Random(SEED).shuffle(mk)
    print(f"  {len(mk)} markets available; sampling within a "
          f"{budget}s budget\n")

    acc = {o: dict(n=0, won=0, cost=0.0, pnl=0.0, px=0.0) for o in OFFSETS}
    started, seen, failed = time.time(), 0, 0
    for series, m in mk:
        if time.time() - started > budget:
            break
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        q = quotes(series, str(m.get("ticker")), m.get("close_time"))
        seen += 1
        if not q:
            failed += 1
            continue
        for off, px in q.items():
            if not (BAND[0] <= px < BAND[1]):
                continue
            n = int(STAKE // px)
            if n <= 0:
                continue
            cost = n * px + fee(n, px)
            a = acc[off]
            a["n"] += 1
            a["px"] += px
            a["cost"] += cost
            if res == "yes":
                a["won"] += 1
                a["pnl"] += n - cost
            else:
                a["pnl"] -= cost
        time.sleep(B.PAUSE)

    print(f"  {seen} markets read ({failed} with no usable candles) in "
          f"{time.time()-started:.0f}s\n")
    print(f"  {'entry':>6} {'bets':>7} {'won':>7} {'needed':>8} "
          f"{'per $':>8} {'$/bet':>8}   shape")
    rows = []
    for off in sorted(OFFSETS, reverse=True):
        a = acc[off]
        if a["n"] < 30:
            print(f"  T-{off:<4} {a['n']:>7}   too few to score")
            continue
        per = 100 * a["pnl"] / a["cost"]
        need = 100 * a["px"] / a["n"]
        # A bar either side of zero, so the arc is visible without plotting.
        w = int(round(abs(per) * 6))
        bar = ("-" * min(w, 28)).rjust(28) + "|" if per < 0 else \
              "|" + ("+" * min(w, 28))
        print(f"  T-{off:<4} {a['n']:>7} {100*a['won']/a['n']:>6.1f}% "
              f"{need:>7.1f}% {per:>+7.2f}% ${a['pnl']/a['n']:>+7.3f}   {bar}")
        rows.append((off, per, a["n"]))

    print("\n" + "=" * 96)
    if len(rows) < 6:
        print("  Not enough columns scored to judge the shape. Raise the "
              "budget and run again.")
        return 0
    # SMOOTHNESS, stated as a number rather than left to the eye: how often
    # the curve changes direction. A clean arc turns once. Noise turns often.
    seq = [p for _, p, _ in sorted(rows)]
    turns = sum(1 for i in range(1, len(seq) - 1)
                if (seq[i] - seq[i - 1]) * (seq[i + 1] - seq[i]) < 0)
    best = max(rows, key=lambda r: r[1])
    pos = [o for o, p, _ in rows if p > 0]
    print(f"  direction changes across the curve: {turns} of "
          f"{max(0, len(seq)-2)} possible")
    print(f"  positive offsets: "
          + (", ".join(f"T-{o}" for o in sorted(pos, reverse=True)) or "none"))
    print(f"  highest column: T-{best[0]} at {best[1]:+.2f}% "
          f"({best[2]} bets) -- NOT a recommendation, see the header")
    print()
    if turns <= 3:
        print("  SMOOTH. The curve turns few times, which is what a mechanism")
        print("  looks like: favourites get expensive faster than they get")
        print("  likelier as the close approaches. The T-6 result is more")
        print("  believable, and the MIDDLE of the positive region is the")
        print("  robust place to sit -- not its highest single column.")
    else:
        print("  JAGGED. The curve changes direction repeatedly, so adjacent")
        print("  minutes disagree by more than the effect being measured.")
        print("  That makes the T-6 result noise rather than a finding, and")
        print("  the entry-time question should be dropped, not refined.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Cent by cent across 85-97c: is 88-89c an isolated spike or part of a shape?

THE QUESTION THIS ANSWERS, AND THE ONE IT DOES NOT. bandsweep found 88-89c
at +2.04% net ROI, positive in both chronological halves, clearing every
test that had been pre-registered for it -- and 89-90c, the cent BETWEEN it
and the live band, flipping sign (+2.92% / -0.49%). A market that misprices
88-89c, prices 89-90c correctly and misprices 90-96c again is not a market,
it is a coincidence. One of those rows is noise.

So this stops asking "is this band positive" and asks the structural
question instead: READ CENT BY CENT, does the edge vary smoothly?

  A real mispricing of cheap favourites is a GRADIENT. Whatever makes 88c
  favourites underpriced should make 89c favourites slightly less so and 90c
  less again -- adjacent prices are adjacent bets on nearly the same event,
  priced by the same participants seconds apart. The curve can slope, bend,
  or sit flat, but it should not alternate.

  NOISE ALTERNATES. If the cent-by-cent series goes +2, -0.5, +1.4, -1.1,
  +1.3 with every neighbour disagreeing, the individual cells are sampling
  error around a single underlying number and 88-89c was the luckiest draw.

THE ESTIMATOR, so the shape is not an artefact of the measurement: each cent
reports net ROI against ITS OWN mean cost per contract, since a winning
contract pays exactly $1 and the hurdle moves with the price. 85-97c is
swept so the live band's own cents appear on the same axis -- if 90-96c also
alternates cent by cent while being reliably positive in aggregate, then
cent-level alternation is just what this data looks like at this sample size
and proves nothing against 88-89c either. That control matters more than the
candidate rows.

ALSO REPORTED: the standard error on each cent's win rate, and the pooled
88-90c cell. The error bars are the honest way to see whether neighbouring
cents actually disagree or merely appear to -- two cells 3 points apart with
4-point error bars are the same cell.

WHAT NO AMOUNT OF THIS CAN SETTLE. It is all QUOTES. Whether contracts are
available at 88c at T-9, and what they fill at, is not in this data and is
the question that killed T-3 (+2.3-3.2% on quotes, -6.38% live, the gap
being 1.61c of slippage). A clean shape here earns a depth sample, not an
order.

AND IT IS THE SAME DATA bandsweep already used -- roughly 09-13 to 10-26,
the limit of the candle history. A structural test on the data that produced
the hypothesis is weaker than a fresh window, and the fresh window does not
exist yet; it accrues at about a day per day. Re-running this in a few weeks
is the genuinely out-of-sample test.

Read-only.
"""
import math
import statistics as st
import sys
import time

import backfill as B

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRY_OFFSET = 9
CENTS = list(range(85, 97))          # 85-86, 86-87, ... 96-97
POOLED = ((0.88, 0.90), (0.90, 0.96))
MIN_N = 60                           # per single cent
PER_SERIES = 4000
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 3000


def ask_at(cs, close_ts, off):
    want = close_ts - off * 60
    best = None
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None or t > want:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars")) if isinstance(v, dict)
                    else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        if best is None or t > best[0]:
            best = (t, a)
    return best[1] if best else None


def collect(series, budget_end):
    out = []
    try:
        mk = B.settled_markets(series, PER_SERIES)
    except Exception as e:
        print(f"  {series}: could not list markets ({str(e)[:60]})")
        return out
    print(f"  {series}: {len(mk)} settled markets", flush=True)
    for m in mk:
        if time.time() > budget_end:
            print(f"  {series}: budget reached at {len(out)} usable",
                  flush=True)
            break
        if str(m.get("result") or "").lower() not in ("yes", "no"):
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        cs = B.candles(series, m.get("ticker"), m.get("close_time"))
        time.sleep(B.PAUSE)
        a = ask_at(cs, close, ENTRY_OFFSET)
        if a is not None:
            out.append((a, str(m.get("result")).lower() == "yes", close))
    return out


def cell(rows, lo, hi, minimum):
    """(n, won%, cost%, roi%, se_points) or None when too thin."""
    g = [(p, won) for p, won, _t in rows if lo <= p < hi]
    if len(g) < minimum:
        return None
    n = len(g)
    w = 100.0 * sum(1 for _p, won in g if won) / n
    p = 100.0 * st.mean([p for p, _ in g])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
    cost = p + fee
    # Standard error of the win rate, in percentage points. The ROI error is
    # this divided by the cost, which at these prices is close to the same
    # number -- so it is quoted on the win rate where it is unambiguous.
    se = math.sqrt(max(w * (100.0 - w), 0.0) / n)
    return n, w, cost, 100.0 * (w - cost) / cost, se


def main():
    print("=" * 104)
    print("CENT BY CENT, 85-97c at T-9: does the edge vary smoothly, or "
          "alternate?")
    print("=" * 104)
    end = time.time() + BUDGET
    rows = []
    for s in SERIES:
        if time.time() > end:
            print(f"  [budget reached before {s}]")
            break
        rows += collect(s, end)
    print()

    if len(rows) < 500:
        print(f"  only {len(rows)} readable quotes -- too thin. Nothing "
              "reported.")
        return 1

    rows.sort(key=lambda r: r[2])
    mid = len(rows) // 2
    print(f"  {len(rows)} markets with a readable T-{ENTRY_OFFSET} quote, "
          f"{time.strftime('%Y-%m-%d', time.gmtime(rows[0][2]))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(rows[-1][2]))}")
    print()

    print("-" * 104)
    print(f"{'cent':<10} {'n':>6} {'won %':>7} {'cost %':>7} {'net ROI':>9} "
          f"{'+/- se':>8}   {'older':>8} {'newer':>8}   shape")
    print("-" * 104)
    prev = None
    flips = 0
    measured = 0
    for c in CENTS:
        lo, hi = c / 100.0, (c + 1) / 100.0
        got = cell(rows, lo, hi, MIN_N)
        label = f"{c}-{c+1}c"
        if got is None:
            have = sum(1 for p, _w, _t in rows if lo <= p < hi)
            print(f"{label:<10} {have:>6}   (under {MIN_N}, not measured)")
            continue
        n, w, cost, roi, se = got
        o = cell(rows[:mid], lo, hi, max(25, MIN_N // 2))
        nw = cell(rows[mid:], lo, hi, max(25, MIN_N // 2))
        od = f"{o[3]:+.2f}%" if o else "   --"
        nd = f"{nw[3]:+.2f}%" if nw else "   --"
        mark = ""
        if prev is not None:
            measured += 1
            if (roi > 0) != (prev > 0):
                mark = "<- sign flips vs neighbour"
                flips += 1
        prev = roi
        print(f"{label:<10} {n:>6} {w:>7.2f} {cost:>7.2f} {roi:>+8.2f}% "
              f"{se:>7.2f} {od:>9} {nd:>8}   {mark}")
    print()

    print("-" * 104)
    print("POOLED, for reference")
    print("-" * 104)
    for lo, hi in POOLED:
        got = cell(rows, lo, hi, MIN_N)
        if got is None:
            continue
        n, w, cost, roi, se = got
        print(f"{100*lo:.0f}-{100*hi:.0f}c   n={n:<6} won {w:.2f}%  "
              f"cost {cost:.2f}%  net ROI {roi:+.2f}%  +/- {se:.2f} points")
    print()

    print("=" * 104)
    print("  HOW TO READ IT.")
    print()
    print(f"  {flips} of {measured} adjacent pairs disagree on sign.")
    print()
    print("  A GRADIENT -- few flips, neighbours close together -- means the")
    print("  cent-level numbers are measuring something real, and 88-89c can")
    print("  be read as part of it.")
    print()
    print("  ALTERNATION -- many flips, including INSIDE the live 90-96c band")
    print("  which is reliably positive in aggregate -- means single cents are")
    print("  sampling error at this sample size. In that case 89-90c's flip is")
    print("  not evidence against 88-89c, and 88-89c's pass is not evidence")
    print("  for it: neither cell can carry a verdict, and the pooled 88-90c")
    print("  row is the only reading worth anything.")
    print()
    print("  CHECK THE ERROR BARS BEFORE BELIEVING ANY GAP. Two cents three")
    print("  points apart with four-point standard errors are one cent.")
    print()
    print("  AND IT REMAINS A QUOTE RESULT on the same window that produced")
    print("  the hypothesis. Whatever shape appears, the tradeable question")
    print("  is depth and fills at 88c, which is not in this data at all.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Should the 90-96c band reach wider? Measured at the EDGES, on quotes.

WHY THE LIVE RECORD CANNOT ANSWER THIS. The bot has never placed a bet
outside 90-96c at T-9, so there are zero live bets at 88c and zero at 97c.
No quantity of further $5 bets INSIDE the band says anything about the
outside of it. The only record that contains those prices is the historical
quote record, so that is what this reads.

THE FOUR TESTS, pre-committed before the numbers were seen, so they cannot
be loosened afterwards. A new band earns a SHADOW slot -- not money -- only
if all four hold:

  1. n >= MIN_N markets in the band. The bands already in use sit at
     roughly 100-250 live bets, so this is the same bar, not a softer one.
  2. Net ROI positive AFTER the fee, where break-even is the band's own
     mean cost per contract, not its midpoint. A winning contract pays
     exactly $1, so the mean cost IS the hurdle.
  3. Positive in BOTH chronological halves. Every other claim in this
     project has had to clear that bar; a sign that flips between halves
     is noise wearing a result's clothes.
  4. Within reach of the 90-96c control run through THIS SAME CODE.
     "Positive" is not the test -- a band that returns a tenth of what the
     live band returns is not worth the extra variance.

THE ARITHMETIC ALREADY ARGUES AGAINST THE TOP END, and it is stated here in
advance rather than discovered later. At 96.5c a win pays 3.6% and the fee
costs about 0.25%, so break-even sits near 96.74% -- a margin of about a
quarter of a cent. Measured fill slippage in the live record is larger than
that margin. The top bands can be correctly priced and still untradeable,
and this file will say so even if their gap looks positive.

THE CONTROL IS NOT DECORATION. 90-96c is measured by the same function, on
the same markets, in the same run. If the control comes back somewhere other
than mildly positive then the READ is broken -- stale candles, a bad offset,
a thin pull -- and every other row in the table is as broken as it is. A
sweep whose control fails reports nothing, because a plausible-looking
number from a broken read is worse than no number.

ENTRY IS THE YES ASK AT T-9, from per-minute candles, matching what the live
bot pays. The fee is the verified 0.07*n*p*(1-p), charged on entry.

AND IT IS STILL ONLY A PRICE. A gap here says the market misprices that
band. It does not say six contracts sit at that ask at T-9, which is a depth
question this file does not touch. Passing all four tests earns a shadow
run, never an order.

Read-only.
"""
import statistics as st
import sys
import time

import backfill as B

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRY_OFFSET = 9                 # minutes before close, matching the bot
CONTROL = (0.90, 0.96)
BANDS = ((0.88, 0.89), (0.89, 0.90), (0.90, 0.96),
         (0.96, 0.97), (0.97, 0.98))
MIN_N = 150
HALF_MIN_N = 50
PER_SERIES = 4000
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 3000

# The control must land inside this range or the whole run is void. The live
# 90-96c band has measured between roughly +0.5 and +2 ROI points across
# every cut of this project; a control outside this window means the read is
# wrong, not that the market changed.
CONTROL_MIN, CONTROL_MAX = -0.5, 5.0


def ask_at(cs, close_ts, off):
    """The yes ask `off` minutes before close, from per-minute candles."""
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
    """[(entry_price, won, close_ts)] for one series."""
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
    """(n, won%, cost%, gap, net ROI%) or None when too thin.

    Break-even is the mean COST -- price plus fee -- because a winning
    contract pays exactly one dollar. Comparing against the midpoint of the
    band would flatter every row.
    """
    g = [(p, won) for p, won, _t in rows if lo <= p < hi]
    if len(g) < minimum:
        return None
    n = len(g)
    w = 100.0 * sum(1 for _p, won in g if won) / n
    p = 100.0 * st.mean([p for p, _ in g])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
    cost = p + fee
    return n, w, cost, w - cost, 100.0 * (w - cost) / cost


def main():
    print("=" * 112)
    print("BAND EDGES: is 90-96c leaving money at 88-90c, or at 96-98c?")
    print(f"  entry = the yes ask at T-{ENTRY_OFFSET}, fee included, "
          "'*' marks the band the bot actually trades")
    print("=" * 112)
    end = time.time() + BUDGET
    rows = []
    for s in SERIES:
        if time.time() > end:
            print(f"  [budget reached before {s}]")
            break
        rows += collect(s, end)
    print()

    if len(rows) < 500:
        print(f"  only {len(rows)} markets with a readable T-{ENTRY_OFFSET} "
              "quote -- too thin to judge an edge band. Nothing reported.")
        return 1

    rows.sort(key=lambda r: r[2])
    mid = len(rows) // 2
    print(f"  {len(rows)} markets with a readable quote, "
          f"{time.strftime('%Y-%m-%d', time.gmtime(rows[0][2]))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(rows[-1][2]))}")
    print()

    # THE CONTROL GATE, checked before anything else is printed as a result.
    ctl = cell(rows, CONTROL[0], CONTROL[1], MIN_N)
    print("-" * 112)
    print("THE CONTROL -- 90-96c, the live band, measured by this same code")
    print("-" * 112)
    if ctl is None:
        print(f"  the live band has under {MIN_N} markets in this pull. The "
              "read is too thin to trust, so")
        print("  NOTHING ELSE IS REPORTED. A table whose control cannot be "
              "computed is decoration.")
        return 1
    print(f"  n={ctl[0]}  won {ctl[1]:.2f}%  cost {ctl[2]:.2f}%  "
          f"gap {ctl[3]:+.2f}  net ROI {ctl[4]:+.2f}%")
    if not (CONTROL_MIN <= ctl[4] <= CONTROL_MAX):
        print(f"  CONTROL FAILED. Expected the live band between "
              f"{CONTROL_MIN:+.1f}% and {CONTROL_MAX:+.1f}% and got "
              f"{ctl[4]:+.2f}%.")
        print("  That is a broken READ, not a market change: every other row "
              "would be wrong the same")
        print("  way, so NOTHING ELSE IS REPORTED. Fix the pull and rerun.")
        return 1
    print("  control PASSES -- the read reproduces the known live band, so "
          "the rows below mean something.")
    print()

    print("-" * 112)
    print("THE EDGES")
    print("-" * 112)
    print(f"    {'band':<13} {'n':>6} {'won %':>7} {'cost %':>7} {'gap':>7} "
          f"{'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    verdicts = {}
    for lo, hi in BANDS:
        label = f"{lo:.2f}-{hi:.2f}" + (" *" if (lo, hi) == CONTROL else "")
        c = cell(rows, lo, hi, MIN_N)
        if c is None:
            have = sum(1 for p, _w, _t in rows if lo <= p < hi)
            print(f"    {label:<13} {have:>6}   (under {MIN_N} -- "
                  f"TEST 1 FAILS, no verdict)")
            verdicts[label] = "too thin"
            continue
        n, w, cost, gap, roi = c
        o = cell(rows[:mid], lo, hi, HALF_MIN_N)
        nw = cell(rows[mid:], lo, hi, HALF_MIN_N)
        if o is None or nw is None:
            od, nd, v = "  --", "  --", "halves too thin -- TEST 3 unmet"
        else:
            od, nd = f"{o[4]:+.2f}%", f"{nw[4]:+.2f}%"
            if o[4] > 0 and nw[4] > 0:
                v = ("holds in both" if roi > 0.5 * ctl[4]
                     else "holds, but well under the control -- TEST 4 unmet")
            elif o[4] <= 0 and nw[4] <= 0:
                v = "negative in both -- TEST 2 FAILS"
            else:
                v = "FLIPS -- noise, TEST 3 FAILS"
        verdicts[label] = v
        print(f"    {label:<13} {n:>6} {w:>7.2f} {cost:>7.2f} {gap:>+7.2f} "
              f"{roi:>+8.2f}%   {od:>8} {nd:>8}  {v}")
    print()

    print("=" * 112)
    print("  THE TOP END, on arithmetic rather than on this table. At 96.5c a")
    print("  win pays about 3.6% and the fee costs about 0.25%, so break-even")
    print("  sits near 96.74% -- a margin of roughly a quarter of a cent.")
    print("  Measured slippage in the live record is LARGER than that margin,")
    print("  so 96-98c can be correctly priced here and still untradeable.")
    print("  A positive row up there is not a reason to trade it.")
    print()
    print("  AND WHAT A PASS EARNS. A band clearing all four tests earns a")
    print("  SHADOW slot -- logged, no money -- for about a hundred bets.")
    print("  These are quotes: a gap says the market misprices the band, not")
    print("  that six contracts sit at that ask at T-9. That is depth, and")
    print("  this file does not measure it.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

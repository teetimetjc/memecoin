"""Is there a better band at T-3 than the one the bot trades? Read-only.

THE QUESTION IS ABOUT DOLLARS, NOT ROI, and those come apart here. The live
T-3 band is 0.90-0.94. A narrower band almost always shows a higher net ROI,
because it keeps the best-calibrated slice -- and fires so rarely that it
earns less money. A band at +5.0% that appears twice a week is beaten by one
at +2.5% that appears four times a day.

So every row carries FREQUENCY, and the last column is expected dollars per
day at the live $3 stake. That is the only column that answers what was
asked. It is also the column most easily over-read, so:

  IT ASSUMES EVERY SIGNAL IS BET, and the live session is capped at one bet
  per window and yields to the frozen session. leftover.py measured that
  yielding withholds about 6.4% of T-3 signals, so the dollar figures are
  roughly 6% optimistic before any other consideration.

  IT ASSUMES THE QUOTE IS THE FILL. Every number here is the ask from
  per-minute candles. The T-3 book is thinner than the T-9 book and no
  depth check has been run at this offset.

  IT SCALES LINEARLY WITH STAKE and so says nothing new about sizing. Stake
  scales an edge; it cannot create one.

WHY THE SHAPE MATTERS MORE THAN THE WINNER. This searches a grid, and a grid
hands back its best cell whether or not anything is there. Twenty-odd cells
means several clear a both-halves test by chance -- gold's +2.67%/-1.42% was
found exactly this way.

What a grid CAN say honestly is whether the edge has a shape. If net ROI
rises smoothly as the price rises, that is a structure, and structures
survive. If one cell spikes while its neighbours sit at zero, that is a
coincidence with a good hiding place. So the bands are printed in price
order rather than sorted by ROI, and the neighbours of any winner are there
to be read next to it.

THE LIVE BAND IS MARKED AND DOUBLES AS THE CONTROL, checked against
leftover.py's +3.20% over 6,000 markets. A run that cannot reproduce the
known cell cannot be trusted to rank the unknown ones.

BREAK-EVEN IS EACH BAND'S OWN MEAN COST, price plus fee, because a winning
contract pays exactly $1.00.

Read-only. Places nothing.
"""
import math
import statistics as st
import sys
import time

import backfill as B

OFFSET = 3
STAKE = 3.0
SLIP_BUFFER_CENTS = 3            # matches live.py, for the contract count

# Printed in PRICE ORDER, never sorted by result. Narrow slices first so the
# shape is readable, then the wider spans the bot could actually trade.
SLICES = ((0.88, 0.90), (0.90, 0.92), (0.92, 0.94), (0.94, 0.96),
          (0.96, 0.98), (0.98, 0.995))
SPANS = ((0.90, 0.94), (0.90, 0.96), (0.92, 0.96), (0.88, 0.94),
         (0.90, 0.98), (0.92, 0.98))
LIVE_BAND = (0.90, 0.94)
CONTROL_REF = +3.20
CONTROL_TOLERANCE = 2.0

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
PER_SERIES = 2500
MIN_CELL = 120
MIN_HALF = 50
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 16000


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


def measure(group, minimum):
    """(n, conv%, needed%, net ROI%, $ per bet) or None when too thin.

    The dollar figure is rebuilt the way live.py sizes an order rather than
    as stake * roi: contracts = floor(stake / limit) with the limit at
    ceil(price + slip buffer), which is integer arithmetic. At a 94c limit
    $3 buys 3 contracts, and the rounding is a real part of the return.
    """
    if len(group) < minimum:
        return None
    n = len(group)
    conv = 100.0 * sum(1 for _p, won in group if won) / n
    price = 100.0 * st.mean([p for p, _ in group])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in group])
    needed = price + fee
    roi = 100.0 * (conv - needed) / needed

    per_bet = []
    for px, won in group:
        limit_c = min(99.0, math.ceil(px * 100 + SLIP_BUFFER_CENTS))
        k = int(STAKE / (limit_c / 100.0))
        if k < 1:
            continue
        cost = k * px + 0.07 * k * px * (1.0 - px)
        per_bet.append((k * 1.0 if won else 0.0) - cost)
    dollars = st.mean(per_bet) if per_bet else 0.0
    return n, conv, needed, roi, dollars


def cell(rows, lo, hi, mid, days):
    g = [(p, won) for p, won, _t in rows if lo <= p < hi]
    whole = measure(g, MIN_CELL)
    if whole is None:
        return None, len(g)
    o = measure([(p, won) for p, won, t in rows
                 if lo <= p < hi and t < mid], MIN_HALF)
    nw = measure([(p, won) for p, won, t in rows
                  if lo <= p < hi and t >= mid], MIN_HALF)
    if o is None or nw is None:
        verd, oa, nb = "too thin to split", None, None
    else:
        oa, nb = o[3], nw[3]
        if oa > 0 and nb > 0:
            verd = "HOLDS IN BOTH"
        elif oa <= 0 and nb <= 0:
            verd = "negative in both"
        else:
            verd = "FLIPS -- noise"
    per_day = len(g) / days if days > 0 else 0.0
    return (whole, oa, nb, verd, per_day), len(g)


def show(lo, hi, res, raw, mark):
    band = f"{lo:.2f}-{hi:.2f}{mark}"
    if res is None:
        print(f"  {band:<16} {raw:>5}   too few (needs {MIN_CELL})")
        return None
    whole, a, b, verd, per_day = res
    n, conv, needed, roi, dollars = whole
    od = f"{a:+.2f}%" if a is not None else "   --"
    nd = f"{b:+.2f}%" if b is not None else "   --"
    print(f"  {band:<16} {n:>5} {conv:>8.2f}% {needed:>8.2f}% {roi:>+8.2f}% "
          f"{per_day:>7.2f} {dollars * per_day:>+8.2f}  {od:>8} {nd:>8}  "
          f"{verd}")
    return (a, b, verd, roi, dollars * per_day, n)


def main():
    print("=" * 124)
    print(f"EVERY BAND AT T-{OFFSET}: is there a better one than the "
          f"{LIVE_BAND[0]:.2f}-{LIVE_BAND[1]:.2f} the bot trades?")
    print(f"  '$/day' is expected dollars per day at the live ${STAKE:.0f} "
          "stake, assuming every signal is bet")
    print("  printed in PRICE ORDER, never sorted by result -- the shape is "
          "the evidence, not the winner")
    print("=" * 124)
    started = time.time()
    end = started + BUDGET

    rows = []
    for i, ser in enumerate(SERIES):
        left = end - time.time()
        if left <= 30:
            print(f"  [budget gone; unmeasured: {', '.join(SERIES[i:])}]")
            break
        share = time.time() + max(30.0, left / (len(SERIES) - i))
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception as e:
            print(f"  {ser:<12} could not list ({str(e)[:45]})")
            continue
        got, cut = 0, False
        for m in mk:
            if time.time() > min(share, end):
                cut = True
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            a = ask_at(cs, close, OFFSET)
            if a is not None:
                rows.append((a, res == "yes", close))
                got += 1
        print(f"  {ser:<12} {len(mk):>5} settled, {got:>5} readable"
              + ("   [BUDGET CUT IT SHORT]" if cut else ""))

    print(f"\n  {len(rows)} markets with a readable T-{OFFSET} quote")
    if len(rows) < 500:
        print("  TOO FEW. Nothing claimed.")
        return 0

    ts = sorted(r[2] for r in rows)
    mid = ts[len(ts) // 2]
    # Calendar span the sample covers, for the frequency column. The five
    # series run concurrently, so this is days of wall clock, not of data.
    days = max(1.0, (ts[-1] - ts[0]) / 86400.0)
    print(f"  spanning {days:.1f} days, so the $/day column is per calendar "
          f"day across all {len(SERIES)} series\n")

    hdr = (f"  {'band':<16} {'n':>5} {'converts':>9} {'needs':>9} "
           f"{'net ROI':>9} {'bets/d':>7} {'$/day':>8}  {'older':>8} "
           f"{'newer':>8}  verdict")

    results = {}
    print("=" * 124)
    print("NARROW SLICES -- for the shape of the curve")
    print("=" * 124)
    print(hdr)
    n_cells = 0
    for lo, hi in SLICES:
        n_cells += 1
        res, raw = cell(rows, lo, hi, mid, days)
        g = show(lo, hi, res, raw, "")
        if g:
            results[(lo, hi)] = g
    print()

    print("=" * 124)
    print("TRADEABLE SPANS -- bands the bot could actually be set to")
    print("=" * 124)
    print(hdr)
    for lo, hi in SPANS:
        n_cells += 1
        mark = " *" if (lo, hi) == LIVE_BAND else ""
        res, raw = cell(rows, lo, hi, mid, days)
        g = show(lo, hi, res, raw, mark)
        if g:
            results[(lo, hi)] = g
    print()

    # ---- control --------------------------------------------------------
    print("=" * 124)
    print("CONTROL -- the live band against its known value")
    print("=" * 124)
    live = results.get(LIVE_BAND)
    trustworthy = False
    if live is None:
        print(f"  The live band did not reach {MIN_CELL} in-band markets. "
              "Nothing above can be ranked.")
    else:
        roi = live[3]
        drift = roi - CONTROL_REF
        crossed = (roi > 0) != (CONTROL_REF > 0)
        print(f"  reference {CONTROL_REF:+.2f}% (leftover.py, 6,000 markets)"
              f"   this run {roi:+.2f}%   drift {drift:+.2f}")
        if abs(drift) <= CONTROL_TOLERANCE and not crossed:
            trustworthy = True
            print(f"  PASSED within {CONTROL_TOLERANCE:.1f} points.")
        else:
            print(f"  FAILED -- "
                  f"{'crossed zero' if crossed else 'drifted too far'}. "
                  "Everything above is diagnosis only.")
    print()

    print("=" * 124)
    print("  IS THERE A BETTER BAND?")
    print()
    print(f"  {n_cells} cells searched, so about {n_cells * 0.25:.0f} would "
          "pass a both-halves test by chance alone.")
    print()
    if not trustworthy:
        print("  Unanswerable from this run: the control did not reproduce.")
        print("=" * 124)
        return 0

    live_roi, live_cash = live[3], live[4]
    print(f"  THE INCUMBENT   {LIVE_BAND[0]:.2f}-{LIVE_BAND[1]:.2f}   "
          f"{live_roi:+.2f}% net ROI   ${live_cash:+.2f}/day")
    print()
    better = [(k, v) for k, v in results.items()
              if k != LIVE_BAND and v[2] == "HOLDS IN BOTH"
              and v[4] > live_cash]
    if not better:
        print("  NOTHING BEATS IT on dollars per day while holding in both")
        print("  halves. The band the bot already trades is the best of the")
        print("  ones searched, which is the most reassuring possible result")
        print("  for a grid search: it means the earlier choice was not luck.")
    else:
        print("  Holding in both halves AND earning more per day:")
        for (lo, hi), v in sorted(better, key=lambda x: -x[1][4]):
            print(f"    {lo:.2f}-{hi:.2f}   {v[3]:+.2f}% net ROI   "
                  f"${v[4]:+.2f}/day   (older {v[0]:+.2f}%, newer "
                  f"{v[1]:+.2f}%, n={v[5]})")
        print()
        print("  BEFORE ACTING ON ANY OF THESE, read its neighbours in the")
        print("  slice table above. A band that beats the incumbent while the")
        print("  slices either side of it sit at zero is a coincidence that")
        print("  found a hiding place. One that wins because the whole curve")
        print("  rises through it is a structure, and only that is worth")
        print("  changing a live session for.")
        print()
        print("  AND THE DOLLAR COLUMN IS OPTIMISTIC: it bets every signal,")
        print("  while the live session takes one per window and yields ~6.4%")
        print("  of its signals to the frozen session. It is also a quote, not")
        print("  a fill, with no depth check at this offset.")
    print("=" * 124)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

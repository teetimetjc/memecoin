"""How often does a 70-90c favourite at T-3 actually convert? Read-only.

THE QUESTION. Everything measured at T-3 so far sits at 90c and above: the
live band is 0.90-0.94, and lateentry's grid started at 0.90. The 70-80c
range three minutes out has never been looked at, and it is a different
shape of bet rather than a cheaper version of the same one.

WHAT "CONVERT" HAS TO MEAN, and it is the whole answer. A winning contract
pays exactly $1.00, so the bar is the price itself plus the fee:

    at 75c   you need about 75.5% to break even
    at 85c   you need about 85.4%
    at 92c   you need about 92.5%

A 75c favourite converting 78% of the time is a better bet than a 92c
favourite converting 93%. A 75c favourite converting 74% of the time is a
losing bet that still "wins three times out of four", which is exactly how
this band could look attractive while bleeding money. So every row below
prints the conversion rate NEXT TO the rate required, and the gap between
them is the only number that matters.

THE VARIANCE IS THE REAL DIFFERENCE, and it does not show up in an ROI
column. At 92c one bet in fourteen loses; at 75c one in four does. Identical
expected value feels nothing alike over a few dozen bets, and the project's
own history says so -- the frozen session spent a whole $6 era underwater
while its edge was unchanged. A positive result here would mean a longer
wait before the record could distinguish it from noise, not a faster one.

AND IT IS A DIFFERENT POPULATION, not a discount. A market still priced at
75c with three minutes left is one the market thinks is genuinely unsettled.
At 92c the crowd has largely made up its mind. There is no reason the
calibration error -- if any -- should be the same in both, in either
direction, which is why this is worth measuring rather than assuming either
way.

HONEST ABOUT THE SEARCH. This is exploratory: eight buckets across two
offsets, sixteen cells, so one or two will pass a both-halves test by chance
even if the whole range is worthless. The cell count is printed with the
results. The live 0.90-0.94 cell is included as a CONTROL, because it has a
known value from leftover.py (+3.20% over 6,000 markets) and a run that
cannot reproduce it cannot be trusted on anything new.

Read-only. Places nothing.
"""
import statistics as st
import sys
import time

import backfill as B

OFFSETS = (3, 9)                 # T-3 is the question; T-9 for comparison
BUCKETS = ((0.70, 0.75), (0.75, 0.80), (0.80, 0.85), (0.85, 0.90))
CONTROL_BAND = (0.90, 0.94)      # the live T-3 band, known value
CONTROL_REF = +3.20              # leftover.py, 6,000 markets
CONTROL_TOLERANCE = 2.0

SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
PER_SERIES = 2500
MIN_CELL = 120
MIN_HALF = 50
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 16000


def asks(cs, close_ts):
    """{offset: yes_ask} in dollars, both offsets from one candle set."""
    out = {}
    for off in OFFSETS:
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
            # Both sides present and ordered, or the candle is not a book.
            if a is None or b is None or not (0 < b <= a < 1):
                continue
            if best is None or t > best[0]:
                best = (t, a)
        if best:
            out[off] = best[1]
    return out


def measure(group, minimum):
    """(n, converted%, needed%, gap, net ROI%) or None when too thin.

    `needed` is the group's own mean cost per contract -- the price plus the
    entry fee -- because that is what a contract paying $1.00 has to beat.
    Comparing a conversion rate to anything else (50%, or the whole
    sample's mean) is the mistake this column exists to prevent.
    """
    if len(group) < minimum:
        return None
    n = len(group)
    conv = 100.0 * sum(1 for _p, won in group if won) / n
    price = 100.0 * st.mean([p for p, _ in group])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in group])
    needed = price + fee
    return n, conv, needed, conv - needed, 100.0 * (conv - needed) / needed


def cell(rows, off, lo, hi, mid):
    g = [(q[off], won) for q, won, _t in rows if off in q and lo <= q[off] < hi]
    whole = measure(g, MIN_CELL)
    if whole is None:
        return None, len(g)
    o = measure([(q[off], won) for q, won, t in rows
                 if off in q and lo <= q[off] < hi and t < mid], MIN_HALF)
    nw = measure([(q[off], won) for q, won, t in rows
                  if off in q and lo <= q[off] < hi and t >= mid], MIN_HALF)
    if o is None or nw is None:
        return (whole, None, None, "too thin to split"), len(g)
    if o[4] > 0 and nw[4] > 0:
        verd = "HOLDS IN BOTH"
    elif o[4] <= 0 and nw[4] <= 0:
        verd = "negative in both"
    else:
        verd = "FLIPS -- noise"
    return (whole, o[4], nw[4], verd), len(g)


def show(label, res, raw):
    if res is None:
        print(f"  {label:<14} {raw:>5} in band   too few (needs {MIN_CELL})")
        return None
    whole, a, b, verd = res
    n, conv, needed, gap, roi = whole
    od = f"{a:+.2f}%" if a is not None else "   --"
    nd = f"{b:+.2f}%" if b is not None else "   --"
    print(f"  {label:<14} {n:>5} {conv:>9.2f}% {needed:>9.2f}% {gap:>+7.2f} "
          f"{roi:>+8.2f}%  {od:>8} {nd:>8}  {verd}")
    return (a, b, verd, roi, n, conv, needed)


def main():
    print("=" * 116)
    print("THE 70-90c RANGE AT T-3: how many convert, against how many NEED "
          "to?")
    print("  'needed' is the price plus the fee -- what a contract paying "
          "$1.00 has to beat.")
    print("  A 75c favourite converting 74% wins three times in four and "
          "loses money.")
    print("=" * 116)
    started = time.time()
    end = started + BUDGET

    rows = []
    print("-" * 116)
    for i, ser in enumerate(SERIES):
        left = end - time.time()
        if left <= 30:
            print(f"  [budget gone; unmeasured: {', '.join(SERIES[i:])}]")
            break
        share = time.time() + max(30.0, left / (len(SERIES) - i))
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception as e:
            print(f"  {ser:<12} could not list markets ({str(e)[:45]})")
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
            q = asks(cs, close)
            if q:
                rows.append((q, res == "yes", close))
                got += 1
        print(f"  {ser:<12} {len(mk):>5} settled, {got:>5} readable"
              + ("   [BUDGET CUT IT SHORT]" if cut else ""))
    print(f"\n  {len(rows)} markets with a readable quote\n")
    if len(rows) < 500:
        print("  TOO FEW to split anything in half. Nothing claimed.")
        return 0

    mid = sorted(r[2] for r in rows)[len(rows) // 2]

    hdr = (f"  {'bucket':<14} {'n':>5} {'converted':>10} {'needed':>10} "
           f"{'gap':>7} {'net ROI':>9}  {'older':>8} {'newer':>8}  verdict")

    print("=" * 116)
    print("CONTROL -- the live T-3 band, whose value is already known")
    print("=" * 116)
    print(hdr)
    cres, craw = cell(rows, 3, CONTROL_BAND[0], CONTROL_BAND[1], mid)
    got = show("0.90-0.94 T-3", cres, craw)
    trustworthy = False
    print()
    if got is None:
        print(f"  NOT CHECKABLE: fewer than {MIN_CELL} in-band markets. "
              "Nothing below counts.")
    else:
        roi = got[3]
        drift = roi - CONTROL_REF
        crossed = (roi > 0) != (CONTROL_REF > 0)
        print(f"  reference {CONTROL_REF:+.2f}% (leftover.py, 6,000 markets)"
              f"   this run {roi:+.2f}%   drift {drift:+.2f}")
        if abs(drift) <= CONTROL_TOLERANCE and not crossed:
            trustworthy = True
            print(f"  PASSED within {CONTROL_TOLERANCE:.1f} points.")
        else:
            print(f"  FAILED -- {'crossed zero' if crossed else 'drifted too far'}"
                  ". The rows below are diagnosis only.")
    print()

    n_cells, hits = 0, []
    for off in OFFSETS:
        print("=" * 116)
        print(f"T-{off}" + ("   <- THE QUESTION" if off == 3 else
                            "   (for comparison: the frozen entry)"))
        if not trustworthy:
            print("  *** CONTROL FAILED -- diagnosis only ***")
        print("=" * 116)
        print(hdr)
        for lo, hi in BUCKETS:
            n_cells += 1
            res, raw = cell(rows, off, lo, hi, mid)
            g = show(f"{lo:.2f}-{hi:.2f}", res, raw)
            if g and g[0] is not None and g[0] > 0 and g[1] > 0:
                hits.append((off, f"{lo:.2f}-{hi:.2f}", g[3], g[0], g[1],
                             g[4], g[5], g[6]))
        print()

    print("=" * 116)
    print("  WHAT IT SAYS")
    print()
    print(f"  {n_cells} cells searched. {len(hits)} positive in both halves.")
    print(f"  If the whole range were worthless, roughly "
          f"{n_cells * 0.25:.0f} would pass by chance, so a single")
    print("  survivor here is weak evidence and two are not much better.")
    print()
    if not trustworthy:
        print("  Unanswerable from this run: the control did not reproduce.")
    elif hits:
        print("  Positive in both halves:")
        for off, band, roi, a, b, n, conv, needed in sorted(
                hits, key=lambda x: -min(x[3], x[4])):
            print(f"    T-{off} {band}   converted {conv:.2f}% against "
                  f"{needed:.2f}% needed   net {roi:+.2f}%  (n={n})")
        print()
        print("  AND THE VARIANCE IS THE CATCH. At 75c one bet in four")
        print("  loses, against one in fourteen at 92c. The same expected")
        print("  value takes far longer to become visible, so a survivor")
        print("  here needs MORE bets to confirm than the 90c band did, not")
        print("  fewer. The frozen session sat underwater for a whole $6 era")
        print("  with its edge unchanged, and that was at 1-in-14.")
    else:
        print("  Nothing in 70-90c survives at either offset. The favourite")
        print("  mispricing, if it is real, lives at the top of the book --")
        print("  which is where the live rules already are.")
    print()
    print("  Still a quote result: the ask from candles, no depth check.")
    print("=" * 116)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

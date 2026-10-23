"""The T-3 rule on the non-crypto series. ONE pre-registered cell each.

THE CROSS NOBODY HAS RUN. Two findings stand up in this project, and they
have never been tested together:

  THE T-3 RULE -- the 90-94c band three minutes before close. Measured on
  the five coins over 6,000 markets: +3.20% net ROI, +2.80% and +3.66% in
  the two chronological halves, 295 in-band markets. The best-supported
  number here.

  WTI -- the frozen 90-96c rule at T-9 on oil: +3.56%, holding in both
  halves. The only non-crypto series that has ever cleared the bar.

Everything non-crypto was tested at T-9 only. Everything at T-3 was tested
on crypto only. If the T-3 edge is structural -- favourites underpriced in
the last minutes, as the book thins and fewer people are watching -- it
should not care what the underlying is. That is the same reasoning that made
WTI worth trying, and it was right that time.

WHY THIS FILE IS PRE-REGISTERED, AND WHAT THAT MEANS. The tempting version
of this test sweeps offsets by bands by series, which here would be 160
cells. At that width several will look excellent by chance, and the project
has already been burned by exactly that: gold's +2.67% in one half against
-1.42% in the other, found by searching, believed briefly, and discarded.

So the hypothesis is fixed in the source before the run:

  ONE offset   -- T-3, because that is the rule being tested.
  ONE band     -- 0.90-0.94, because that is the band the rule trades.
  FOUR series  -- WTI, Brent, copper, gold.

Four cells. Not forty. The crypto pool is scored as a CONTROL against the
figure leftover.py already produced for it, so a run that cannot reproduce a
known number cannot be read as evidence about an unknown one.

THE EXPLORATORY BLOCK IS PRINTED SEPARATELY AND LABELLED, because refusing
to look at the other bands would be its own kind of dishonesty -- the
numbers exist either way. What matters is that they are reported as
searched rather than predicted, with the cell count stated, so nothing there
can be quoted later as though it had been the hypothesis.

WHAT A SURVIVOR EARNS. A depth sample, then a forward test at the smallest
size that can be measured. Not an order. Every number here is the ASK from
per-minute candles -- a quote, not a fill -- and the T-3 book is thinner
than the T-9 book on every series measured so far.

Read-only. Places nothing.
"""
import statistics as st
import sys
import time

import backfill as B

# ---- THE PRE-REGISTERED HYPOTHESIS. Fixed before the run. ---------------
OFFSET = 3
BAND = (0.90, 0.94)
CANDIDATES = ("KXWTI15M", "KXBRENT15M", "KXCOPPER15M", "KXGOLD15M")
CONTROL = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
N_PREREGISTERED = len(CANDIDATES)

# WHAT THE CONTROL MUST REPRODUCE: leftover.py's figure for the five coins
# pooled, same offset, same band, measured 2026-10-23 over 6,000 markets.
# 295 in-band markets at +3.20%, halves +2.80% and +3.66%.
CONTROL_REF = +3.20
CONTROL_TOLERANCE = 2.0

# Scoring floors. A cell below these gets no verdict rather than a number:
# the first sweep printed copper at +8.00% on 34 markets, and that figure
# was the most persuasive thing on the page and the least supported.
MIN_CELL = 120
MIN_HALF = 50

# The exploratory grid, reported separately and never as the hypothesis.
EXPLORE_BANDS = ((0.90, 0.92), (0.92, 0.94), (0.94, 0.96), (0.90, 0.96))
EXPLORE_OFFSETS = (1, 2, 3, 5, 9)

PER_SERIES = 2500
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 16000


def asks(cs, close_ts, offsets):
    """{offset: yes_ask} in dollars, from one candle set per market.

    Both sides of the book must be present and ordered before a candle is
    trusted: an ask with no bid is not a price anything could have been
    bought at. Reading every offset from the same candle set keeps the
    comparison within-market.
    """
    out = {}
    for off in offsets:
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
        if best:
            out[off] = best[1]
    return out


def score(group, minimum):
    """(n, won%, priced%, net ROI%) or None when too thin to speak.

    Break-even is the group's OWN mean cost per contract: a winning contract
    pays exactly $1.00, so a 92c favourite needs better than about 92.5%
    once the entry fee is counted.
    """
    if len(group) < minimum:
        return None
    n = len(group)
    w = 100.0 * sum(1 for _p, won in group if won) / n
    p = 100.0 * st.mean([p for p, _ in group])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in group])
    cost = p + fee
    return n, w, p, 100.0 * (w - cost) / cost


def cell(rows, off, lo, hi, mid):
    """One (series, offset, band) cell, whole and split in halves."""
    g = [(q[off], won) for q, won, _t in rows if off in q and lo <= q[off] < hi]
    whole = score(g, MIN_CELL)
    if whole is None:
        return None, len(g)
    o = score([(q[off], won) for q, won, t in rows
               if off in q and lo <= q[off] < hi and t < mid], MIN_HALF)
    nw = score([(q[off], won) for q, won, t in rows
                if off in q and lo <= q[off] < hi and t >= mid], MIN_HALF)
    if o is None or nw is None:
        return (whole, None, None, "too thin to split"), len(g)
    if o[3] > 0 and nw[3] > 0:
        verd = "HOLDS IN BOTH"
    elif o[3] <= 0 and nw[3] <= 0:
        verd = "negative in both"
    else:
        verd = "FLIPS -- noise"
    return (whole, o[3], nw[3], verd), len(g)


def collect(series, offsets, budget_end):
    """[({offset: ask}, won, close_ts)] for one series."""
    out = []
    try:
        mk = B.settled_markets(series, PER_SERIES)
    except Exception as e:
        print(f"  {series:<14} could not list markets ({str(e)[:45]})")
        return out
    cut = False
    for m in mk:
        if time.time() > budget_end:
            cut = True
            break
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        cs = B.candles(series, m.get("ticker"), m.get("close_time"))
        time.sleep(B.PAUSE)
        q = asks(cs, close, offsets)
        if OFFSET in q:
            out.append((q, res == "yes", close))
    print(f"  {series:<14} {len(mk):>5} settled, {len(out):>5} readable at "
          f"T-{OFFSET}" + ("   [BUDGET CUT IT SHORT]" if cut else ""))
    return out


def row(label, res, n_raw, tag=""):
    if res is None:
        print(f"  {label:<16} {n_raw:>5} in band   too few "
              f"(needs {MIN_CELL}){tag}")
        return None
    whole, a, b, verd = res
    n, w, p, roi = whole
    od = f"{a:+.2f}%" if a is not None else "   --"
    nd = f"{b:+.2f}%" if b is not None else "   --"
    print(f"  {label:<16} {n:>5} {w:>7.2f}% {p:>8.2f}% {roi:>+8.2f}%   "
          f"{od:>8} {nd:>8}  {verd}{tag}")
    return (a, b, verd, roi, n)


def main():
    offsets = tuple(sorted(set(EXPLORE_OFFSETS) | {OFFSET}))
    print("=" * 112)
    print(f"THE T-{OFFSET} RULE ON THE NON-CRYPTO SERIES")
    print(f"  PRE-REGISTERED: band {BAND[0]:.2f}-{BAND[1]:.2f} at "
          f"T-{OFFSET} on {', '.join(CANDIDATES)}")
    print(f"  {N_PREREGISTERED} cells, fixed in the source before the run. "
          "The exploratory grid is reported separately.")
    print("=" * 112)
    started = time.time()
    end = started + BUDGET

    data = {}
    print("-" * 112)
    print("COLLECTING -- control first, so a budget overrun cannot leave the "
          "run uncheckable")
    print("-" * 112)
    order = list(CONTROL) + list(CANDIDATES)
    for i, s in enumerate(order):
        left = end - time.time()
        if left <= 30:
            print(f"  [budget gone; unmeasured: {', '.join(order[i:])}]")
            break
        share = time.time() + max(30.0, left / (len(order) - i))
        data[s] = collect(s, offsets, min(share, end))
    print()

    pooled = [r for s in CONTROL for r in data.get(s, [])]
    allrows = pooled + [r for s in CANDIDATES for r in data.get(s, [])]
    if not allrows:
        print("  Nothing collected.")
        return 1
    # One chronological midpoint across the whole run, so every cell is
    # split at the same date rather than at its own median -- otherwise the
    # halves are not comparable between series.
    mid = sorted(r[2] for r in allrows)[len(allrows) // 2]

    print("=" * 112)
    print("CONTROL -- the five coins pooled, the cell leftover.py already "
          "measured")
    print("=" * 112)
    print(f"  {'cohort':<16} {'n':>5} {'won':>8} {'priced':>9} "
          f"{'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    cres, craw = cell(pooled, OFFSET, BAND[0], BAND[1], mid)
    got = row("crypto pooled", cres, craw)
    print()
    trustworthy = False
    if got is None:
        print(f"  NOT CHECKABLE: the control cell did not reach {MIN_CELL} "
              "in-band markets.")
        print("  Nothing below can be read as evidence.")
    else:
        _a, _b, _v, roi, _n = got
        drift = roi - CONTROL_REF
        print(f"  reference {CONTROL_REF:+.2f}% (leftover.py, 6,000 markets)"
              f"   this run {roi:+.2f}%   drift {drift:+.2f}")
        # Distance AND direction, because the first sweep drifted only ~2
        # points while crossing zero, which is the difference between "do
        # not trade this" and "this might pay".
        crossed = (roi > 0) != (CONTROL_REF > 0)
        if abs(drift) <= CONTROL_TOLERANCE and not crossed:
            trustworthy = True
            print(f"  PASSED within {CONTROL_TOLERANCE:.1f} points. The "
                  "candidate rows are readable.")
        else:
            why = "crossed zero" if crossed else "drifted too far"
            print(f"  FAILED -- {why}. The same rule and code give a "
                  "materially different answer than")
            print("  leftover.py did, so this run cannot judge a new series. "
                  "Nothing below counts.")
    print()

    print("=" * 112)
    print(f"THE {N_PREREGISTERED} PRE-REGISTERED CELLS")
    if not trustworthy:
        print("  *** CONTROL FAILED -- printed for diagnosis only ***")
    print("=" * 112)
    print(f"  {'series':<16} {'n':>5} {'won':>8} {'priced':>9} "
          f"{'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    survivors = []
    for s in CANDIDATES:
        if s not in data:
            print(f"  {s:<16}   not collected")
            continue
        res, raw = cell(data[s], OFFSET, BAND[0], BAND[1], mid)
        g = row(s, res, raw)
        if g and g[0] is not None and g[0] > 0 and g[1] > 0:
            survivors.append((s, g[3], g[0], g[1], g[4]))
    print()

    print("-" * 112)
    print("EXPLORATORY -- SEARCHED, NOT PREDICTED. Do not quote these as the "
          "hypothesis.")
    print("-" * 112)
    n_cells = 0
    explore_hits = []
    for s in CANDIDATES:
        if s not in data:
            continue
        for off in EXPLORE_OFFSETS:
            for lo, hi in EXPLORE_BANDS:
                if off == OFFSET and (lo, hi) == BAND:
                    continue       # that one is the hypothesis, above
                n_cells += 1
                res, _raw = cell(data[s], off, lo, hi, mid)
                if res and res[1] is not None and res[1] > 0 and res[2] > 0:
                    explore_hits.append((s, off, f"{lo:.2f}-{hi:.2f}",
                                         res[0][3], res[1], res[2], res[0][0]))
    print(f"  {n_cells} additional cells searched. "
          f"{len(explore_hits)} positive in both halves.")
    if explore_hits:
        print(f"  {'series':<14} {'off':>4} {'band':<12} {'n':>5} "
              f"{'net ROI':>9} {'older':>9} {'newer':>9}")
        for s, off, band, roi, a, b, n in sorted(
                explore_hits, key=lambda x: -min(x[4], x[5])):
            print(f"  {s:<14} T-{off:<2} {band:<12} {n:>5} {roi:>+8.2f}% "
                  f"{a:>+8.2f}% {b:>+8.2f}%")
        print()
        print(f"  At {n_cells} cells, roughly {n_cells * 0.25:.0f} would be")
        print("  expected to pass a both-halves test by chance alone if every")
        print("  series were worthless. Treat any row here as a reason to")
        print("  pre-register a NEW test, never as a result.")
    print()

    print("=" * 112)
    print("  THE ANSWER TO THE QUESTION ASKED")
    print()
    if not trustworthy:
        print("  Unanswerable from this run: the control did not reproduce.")
    elif survivors:
        print("  The T-3 rule holds in both halves on:")
        for s, roi, a, b, n in sorted(survivors, key=lambda x: -min(x[2], x[3])):
            print(f"    {s:<14} {roi:+.2f}%  (older {a:+.2f}%, newer "
                  f"{b:+.2f}%, n={n})")
        print()
        print("  Each is a CANDIDATE. Still a quote result: the ask from")
        print("  candles, and the T-3 book is thinner than the T-9 book on")
        print("  every series measured so far. Next step is a depth sample,")
        print("  then a forward test at the smallest measurable size -- the")
        print("  way WTI was added, not an immediate order.")
    else:
        print("  The T-3 rule does NOT survive on any of the four, which is")
        print("  itself worth knowing: it suggests the edge is specific to")
        print("  the crypto series rather than structural to the last three")
        print("  minutes of any fifteen-minute market. The live T-3 session")
        print("  on crypto is unaffected either way.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

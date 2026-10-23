"""Every 15-minute series on Kalshi, through the one code path. Read-only.

THE QUESTION, ASKED PROPERLY. Seven series have ever been measured: the five
coins the bot trades, plus GOLD and WTI as candidates. That list was not
chosen by measurement -- it is the crypto bot's original universe plus the
two commodities that looked liquid enough to be worth a look. Copper, natural
gas, and any index or FX series on the same fifteen-minute clock have never
been tested at all. "Not a quality market" was never the finding; nobody ran
them.

So this discovers the whole set from the exchange and runs all of it through
the SAME function, rather than through a list somebody typed.

WHY ONE CODE PATH MATTERS MORE THAN THE SAMPLE SIZE. If a candidate is
scored by new code and the baseline by old code, a difference between them
measures the two code paths at least as much as the two markets. Everything
here -- the five live coins included -- goes through collect() and cell()
below. The live series' numbers are therefore reproducible against the
earlier goldoil run, and if they come out different, THIS file is wrong.

THE BAR, unchanged from everything else in this project:

  POSITIVE IN BOTH CHRONOLOGICAL HALVES. This is what kills candidates, and
  it should. GOLD scored +0.80% overall -- respectable, and it is the second
  deepest book on the exchange -- but +2.67% in the older half against
  -1.42% in the newer. A sign that flips between halves is noise wearing the
  costume of an edge. Sweeping twenty-odd series times four bands means
  several cells WILL look good by chance, which is the entire reason the
  split is not optional.

  THE FEE IS CHARGED, at the verified 0.07*n*p*(1-p) on entry, so net ROI is
  what a bet would have returned and not a gross gap.

  BREAK-EVEN IS THE GROUP'S OWN MEAN COST, because a winning contract pays
  exactly $1.00. A 93c favourite needs better than about 93.5% to pay.

WHAT THIS DELIBERATELY DOES NOT DO.

  IT DOES NOT CHECK DEPTH. Every number here is a QUOTE result: the ask at
  T-9 from per-minute candles. Volume says a book exists; it does not say
  five contracts sit at the ask at the moment the bot would hit it. DOGE
  showed 8 contracts resting against WTI's 269 in the same hour -- a quote
  edge you cannot fill is worth nothing. A series that survives here earns a
  depth sample from depthchk, not an order.

  IT DOES NOT RANK BY LIQUIDITY. Volume is printed because an empty book
  makes a backtest meaningless, not as a reason to trade something. Gold is
  the standing proof: deepest candidate on the board, excluded on the
  evidence. Choosing a market on liquidity rather than on edge is how this
  project would end up trading something that merely looks tradeable.

  IT SETTLES NOTHING ON ITS OWN. A survivor here is a candidate for a
  forward test at the smallest size that can be measured, the way WTI was.

THE BUDGET IS SPLIT PER SERIES, not spent first-come. Each series gets an
equal share of what remains, so one slow or enormous series cannot consume
the run and leave the rest unmeasured -- which would quietly turn "no edge
found" into "never looked".

Read-only. Places nothing.
"""
import statistics as st
import sys
import time

import backfill as B

# What counts as a fifteen-minute market. The live series sit at 0.25h; the
# window is generous enough to catch a series that opens a minute early or
# closes late, and tight enough to exclude the hourlies.
MIN_LIFE_H, MAX_LIFE_H = 0.15, 0.45

# A series needs enough settled markets to split in half and still have
# something in band. Below this there is nothing to say, and saying it anyway
# is how a thin cell becomes a finding.
MIN_SETTLED = 120

ENTRY_OFFSET = 9                 # minutes before close, matching the live bot
BANDS = ((0.90, 0.92), (0.92, 0.94), (0.94, 0.96), (0.90, 0.96))
LIVE_BAND = (0.90, 0.96)
PER_SERIES = 900
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 3000

LIVE = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
TESTED = LIVE + ("KXGOLD15M", "KXWTI15M")


def discover(budget_end):
    """Every series on the exchange whose markets live about 15 minutes.

    Cheap on purpose: one /markets call per series, 60 markets, no candles.
    The expensive part is the quote history, and that is only spent on
    series that clear the lifetime and sample-size filters here.
    """
    d, _e = B.get("/trade-api/v2/series")
    ser = [str(s.get("ticker") or "") for s in ((d or {}).get("series") or [])]
    ser = sorted({s for s in ser if s})
    print(f"  {len(ser):,} series on the exchange; screening for a "
          "fifteen-minute clock")
    out, checked, thin = [], 0, 0
    for tk in ser:
        if time.time() > budget_end:
            print(f"  [screen budget reached after {checked} series]")
            break
        try:
            mk = B.settled_markets(tk, 60)
        except Exception:
            continue
        checked += 1
        lives = []
        for m in mk:
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is not None and c is not None:
                lives.append((c - o) / 3600.0)
        if not lives:
            continue
        life = st.median(lives)
        if not (MIN_LIFE_H <= life <= MAX_LIFE_H):
            continue
        # 60 is the screening cap, so a series that fills it has at least 60
        # and is worth the deeper pull; one that does not is genuinely small.
        if len(mk) < 60:
            thin += 1
            print(f"    {tk:<16} 15-min clock but only {len(mk)} settled "
                  "markets in the screen -- too small to split")
            continue
        out.append(tk)
    print(f"  {checked:,} series inspected, {len(out)} on a fifteen-minute "
          f"clock, {thin} of those too small\n")
    return out


def ask_at(cs, close_ts, off):
    """The yes ask `off` minutes before close, from per-minute candles.

    Both sides are required to be present and ordered before a candle is
    trusted. A candle with an ask and no bid is a book with one side, and
    its 'ask' is not a price anything could have been bought at.
    """
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
    """[(entry_price, won, close_ts)] for one series. The only data path."""
    out = []
    try:
        mk = B.settled_markets(series, PER_SERIES)
    except Exception as e:
        print(f"  {series}: could not list markets ({str(e)[:50]})")
        return out
    ran_out = False
    for m in mk:
        if time.time() > budget_end:
            ran_out = True
            break
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        close = B.ts(m.get("close_time"))
        if close is None:
            continue
        cs = B.candles(series, m.get("ticker"), m.get("close_time"))
        time.sleep(B.PAUSE)
        a = ask_at(cs, close, ENTRY_OFFSET)
        if a is not None:
            out.append((a, res == "yes", close))
    # Printed, not swallowed: a truncated series is a smaller sample than it
    # looks, and that has to be visible next to its numbers.
    print(f"  {series:<16} {len(mk):>5} settled, {len(out):>5} with a "
          f"readable T-{ENTRY_OFFSET} quote"
          + ("   [BUDGET CUT IT SHORT]" if ran_out else ""))
    return out


def cell(rows, lo, hi, minimum=30):
    """(n, won%, priced%, gap, net ROI%) or None when too thin to speak."""
    g = [(p, won) for p, won, _t in rows if lo <= p < hi]
    if len(g) < minimum:
        return None
    n = len(g)
    w = 100.0 * sum(1 for _p, won in g if won) / n
    p = 100.0 * st.mean([p for p, _ in g])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
    cost = p + fee
    return n, w, p, w - p, 100.0 * (w - cost) / cost


def verdict_for(rows, lo, hi):
    """The both-halves verdict for one cell, and the two half ROIs."""
    rows = sorted(rows, key=lambda r: r[2])
    mid = len(rows) // 2
    o = cell(rows[:mid], lo, hi, minimum=15)
    nw = cell(rows[mid:], lo, hi, minimum=15)
    if o is None or nw is None:
        return None, None, "too thin to split"
    if o[4] > 0 and nw[4] > 0:
        return o[4], nw[4], "HOLDS IN BOTH"
    if o[4] <= 0 and nw[4] <= 0:
        return o[4], nw[4], "negative in both"
    return o[4], nw[4], "FLIPS -- noise"


def report(label, rows):
    if not rows:
        print(f"  {label:<16} no usable markets")
        return None
    srt = sorted(rows, key=lambda r: r[2])
    print(f"  {label}   ({len(rows)} markets with a readable "
          f"T-{ENTRY_OFFSET} quote)")
    print(f"    {'band':<13} {'n':>5} {'won %':>7} {'priced %':>9} "
          f"{'gap':>7} {'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    live_row = None
    for lo, hi in BANDS:
        c = cell(srt, lo, hi)
        band = f"{lo:.2f}-{hi:.2f}" + (" *" if (lo, hi) == LIVE_BAND else "")
        if c is None:
            k = sum(1 for p, _w, _t in srt if lo <= p < hi)
            print(f"    {band:<13} {k:>5}   (too few)")
            continue
        n, w, p, gap, roi = c
        a, b, verd = verdict_for(srt, lo, hi)
        od = f"{a:+.2f}%" if a is not None else "  --"
        nd = f"{b:+.2f}%" if b is not None else "  --"
        print(f"    {band:<13} {n:>5} {w:>7.2f} {p:>9.2f} {gap:>+7.2f} "
              f"{roi:>+8.2f}%   {od:>8} {nd:>8}  {verd}")
        if (lo, hi) == LIVE_BAND:
            live_row = (n, roi, a, b, verd)
    print()
    return live_row


def main():
    print("=" * 112)
    print("EVERY FIFTEEN-MINUTE SERIES ON KALSHI, through one code path")
    print(f"  entry = the yes ask at T-{ENTRY_OFFSET}, fee included; "
          "'*' marks the band the bot actually trades")
    print("  a cell counts ONLY if its net ROI is positive in both "
          "chronological halves")
    print("=" * 112)
    started = time.time()
    end = started + BUDGET

    # A fifth of the budget on discovery at most. Finding the series is
    # worthless without the quotes to score them.
    found = discover(min(end, started + BUDGET * 0.2))
    if not found:
        print("  No fifteen-minute series found. That is almost certainly a")
        print("  read failure rather than the truth -- the five the bot")
        print("  trades should always appear here.")
        return 1

    # The live five must come out of discovery, and if they do not, the
    # filter is wrong and every other row is suspect.
    missing = [s for s in LIVE if s not in found]
    if missing:
        print(f"  WARNING: the live series {', '.join(missing)} did not "
              "survive discovery.")
        print("  The lifetime filter or the markets read is wrong, so treat "
              "everything below as unverified.\n")

    fresh = [s for s in found if s not in TESTED]
    print(f"  {len(found)} fifteen-minute series: {len(fresh)} never measured "
          f"before, {len(found) - len(fresh)} already known")
    if fresh:
        print(f"  NEW: {', '.join(fresh)}")
    print()

    # NEVER-MEASURED FIRST, and an equal slice of the clock each, so the
    # answer to the question actually asked survives a budget overrun.
    order = fresh + [s for s in found if s in TESTED]
    data = {}
    print("-" * 112)
    print("COLLECTING")
    print("-" * 112)
    for i, s in enumerate(order):
        left = end - time.time()
        if left <= 30:
            print(f"  [budget gone; {len(order) - i} series unmeasured: "
                  f"{', '.join(order[i:])}]")
            break
        share = time.time() + max(30.0, left / (len(order) - i))
        data[s] = collect(s, min(share, end))
    print()

    summary = []
    for title, group in (("NEVER MEASURED BEFORE", fresh),
                         ("ALREADY KNOWN -- the same code, as a control",
                          [s for s in order if s in TESTED])):
        got = [s for s in group if data.get(s)]
        if not got:
            continue
        print("-" * 112)
        print(title)
        print("-" * 112)
        for s in got:
            r = report(s, data[s])
            if r:
                summary.append((s, s in TESTED) + r)

    print("=" * 112)
    print("THE LIVE BAND (0.90-0.96) ON EVERY SERIES, ranked by its WEAKER "
          "half")
    print("=" * 112)
    if not summary:
        print("  No series had enough in-band markets to score.")
    else:
        def weaker(x):
            a, b = x[4], x[5]
            return min(a, b) if (a is not None and b is not None) else -99.0
        print(f"  {'series':<16} {'n':>5} {'net ROI':>9} {'older':>9} "
              f"{'newer':>9}  verdict")
        for s, known, n, roi, a, b, verd in sorted(summary, key=weaker,
                                                   reverse=True):
            od = f"{a:+.2f}%" if a is not None else "   --"
            nd = f"{b:+.2f}%" if b is not None else "   --"
            tag = "" if known else "   <- NEW"
            print(f"  {s:<16} {n:>5} {roi:>+8.2f}% {od:>9} {nd:>9}  "
                  f"{verd}{tag}")
    print()
    print("  HOW TO READ IT, AND WHAT IT CANNOT TELL YOU.")
    print()
    print("  Compare any candidate against the known series in the control")
    print("  block, not against zero. Same rule, same code, same period. If")
    print("  the live five do not reproduce roughly what goldoil found for")
    print("  them, this file is wrong and nothing else here counts.")
    print()
    print("  A 'HOLDS IN BOTH' row is a candidate and nothing more. Every")
    print("  number above is a QUOTE: the ask at T-9 from candles. It says")
    print("  nothing about whether contracts could be bought there. DOGE had")
    print("  8 resting at the ask where WTI had 269, in the same hour. The")
    print("  next step for a survivor is a depth sample, then a forward test")
    print("  at the smallest size that can be measured -- the way WTI was.")
    print()
    print("  And with this many cells, some will look good by accident. The")
    print("  both-halves split is the defence, which is why the ranking is")
    print("  by the WEAKER half rather than by the average.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

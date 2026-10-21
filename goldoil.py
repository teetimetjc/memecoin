"""Does the favourite rule calibrate on GOLD and WTI the way it does on crypto?

WHY THIS IS A REAL QUESTION AND NOT A FORMALITY. shortmkt found thirteen
more fifteen-minute series, and two of them are liquid enough to matter:
KXGOLD15M at a 426,000 median volume -- second only to BTC, 4.8x the ETH
book the bot already trades -- and KXWTI15M at 112,000, ahead of both XRP
and SOL. Same clock, same structure, deeper books.

WHAT MUST NOT BE ASSUMED is that the 90-96c rule carries over. It was
calibrated on crypto. Gold and oil are different underlyings with different
volatility structure and, more to the point, different JUMP behaviour: oil
moves on inventory reports and OPEC headlines, gold on rate expectations,
and both trade against a futures market with its own session breaks. A
favourite that is correctly priced in one regime can be systematically
wrong in another, in either direction. That is the thing being measured.

THE CRYPTO SERIES ARE RUN THROUGH THE SAME CODE as a baseline. Without
them a number here means nothing: a +1.2 gap is encouraging or
disappointing only next to what the live markets produce under identical
treatment. Running them through separate code paths and comparing would
measure the code as much as the markets.

ENTRY IS THE ASK AT T-9, matching the live bot, read from per-minute
candles. The fee is the verified 0.07*n*p*(1-p), charged on entry, so the
net ROI column is what a bet would actually have returned.

CHRONOLOGICALLY SPLIT, same bar as everything else: a band counts only if
its gap holds in BOTH halves. Two new series times four bands is eight more
cells added to a project that has already searched a lot of them, so the
split is not optional here.

AND IT PROVES NOTHING ABOUT TRADEABILITY. These are quotes. Volume says a
book exists; it does not say six contracts sit at the ask at T-9. That
needs the same depth sampling latecollect does for the late-entry idea.

Read-only.
"""
import statistics as st
import sys
import time

import backfill as B

CANDIDATES = ("KXGOLD15M", "KXWTI15M")
BASELINE = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRY_OFFSET = 9                 # minutes before close, matching the bot
BANDS = ((0.90, 0.92), (0.92, 0.94), (0.94, 0.96), (0.90, 0.96))
LIVE_BAND = (0.90, 0.96)
PER_SERIES = 1500
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700


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
        print(f"  {series}: could not list markets ({str(e)[:50]})")
        return out
    print(f"  {series}: {len(mk)} settled markets")
    for m in mk:
        if time.time() > budget_end:
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
    return out


def cell(rows, lo, hi, minimum=30):
    """(n, won%, priced%, gap, net ROI%) or None when too thin."""
    g = [(p, won) for p, won, _t in rows if lo <= p < hi]
    if len(g) < minimum:
        return None
    n = len(g)
    w = 100.0 * sum(1 for _p, won in g if won) / n
    p = 100.0 * st.mean([p for p, _ in g])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in g])
    cost = p + fee
    return n, w, p, w - p, 100.0 * (w - cost) / cost


def report(label, rows):
    if not rows:
        print(f"  {label:<14} no usable markets")
        return
    rows = sorted(rows, key=lambda r: r[2])
    mid = len(rows) // 2
    print(f"  {label}  ({len(rows)} markets with a readable T-{ENTRY_OFFSET} "
          "quote)")
    print(f"    {'band':<13} {'n':>5} {'won %':>7} {'priced %':>9} "
          f"{'gap':>7} {'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    for lo, hi in BANDS:
        c = cell(rows, lo, hi)
        band = f"{lo:.2f}-{hi:.2f}" + (" *" if (lo, hi) == LIVE_BAND else "")
        if c is None:
            g = [1 for p, _w, _t in rows if lo <= p < hi]
            print(f"    {band:<13} {len(g):>5}   (too few)")
            continue
        n, w, p, gap, roi = c
        o = cell(rows[:mid], lo, hi, minimum=15)
        nw = cell(rows[mid:], lo, hi, minimum=15)
        if o is None or nw is None:
            od, nd, verdict = "  --", "  --", "too thin to split"
        else:
            od, nd = f"{o[4]:+.2f}%", f"{nw[4]:+.2f}%"
            if o[4] > 0 and nw[4] > 0:
                verdict = "holds in both"
            elif o[4] <= 0 and nw[4] <= 0:
                verdict = "negative in both"
            else:
                verdict = "FLIPS -- noise"
        print(f"    {band:<13} {n:>5} {w:>7.2f} {p:>9.2f} {gap:>+7.2f} "
              f"{roi:>+8.2f}%   {od:>8} {nd:>8}  {verdict}")
    print()


def main():
    print("=" * 108)
    print("GOLD AND OIL: does the 90-96c favourite rule calibrate the way it "
          "does on crypto?")
    print(f"  entry = the yes ask at T-{ENTRY_OFFSET}, fee included, "
          "'*' marks the band the bot actually trades")
    print("=" * 108)
    end = time.time() + BUDGET
    data = {}
    # CANDIDATES FIRST, so a budget overrun costs the baseline rather than
    # the thing being asked about. The baseline is already known live.
    for s in CANDIDATES + BASELINE:
        if time.time() > end:
            print(f"  [budget reached before {s}]")
            break
        data[s] = collect(s, end)
    print()

    print("-" * 108)
    print("THE CANDIDATES")
    print("-" * 108)
    for s in CANDIDATES:
        if s in data:
            report(s, data[s])

    print("-" * 108)
    print("THE BASELINE -- the five series the bot already trades, same code")
    print("-" * 108)
    pooled = []
    for s in BASELINE:
        if s in data:
            report(s, data[s])
            pooled += data[s]
    if pooled:
        report("ALL FIVE POOLED", pooled)

    print("=" * 108)
    print("  HOW TO READ IT. Compare each candidate's starred row against the")
    print("  pooled baseline's starred row -- same rule, same code, same")
    print("  period. A candidate only matters if its gap is positive in BOTH")
    print("  halves AND comparable to what the live markets give.")
    print()
    print("  AND THIS IS STILL ONLY THE PRICE. Volume says a book exists; it")
    print("  does not say six contracts sit at the ask at T-9. A gap that")
    print("  survives here earns a depth check, not an order.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

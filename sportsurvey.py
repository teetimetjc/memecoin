"""Which Kalshi sports are worth backtesting the favourite trade on?

A SURVEY, NOT A VERDICT. It screens every sports series with enough settled
history to say three things: are these markets quoted at all, do they reach
the 90-96c band, and when they do, does the favourite hold more often than
its price implies. A series that fails any of those is not worth the work of
a real backtest; one that passes has earned a proper spec.

THE ENTRY RULE IS FIRST-TOUCH, and it is chosen because it is the only rule
that means the same thing across sports. The crypto trade buys at T-9 because
a fifteen-minute market has a clock; a soccer market lives for a fortnight
and a baseball market for a day, so "T-9" transfers to nothing. First-touch
-- buy the first minute the yes ask lands in the band, hold to settlement --
needs no sport-specific knowledge and has no lookahead.

It is also arbitrary, and a real spec would almost certainly not use it. Read
the output as "is there anything here", never as "this is the strategy".

ONLY THE LAST FOUR HOURS BEFORE CLOSE ARE READ. That is where the game
actually happens; the days before it are pre-match positioning, which is a
different trade and would swamp the sample with quiet minutes.

WHAT A PASS LOOKS LIKE. Win rate above the average price paid, by more than
the fee, on enough markets to mean anything. What it will probably look like,
on everything measured in this project so far, is win rate almost exactly
equal to price -- which is what a calibrated market produces and is the
honest expectation going in.

Read-only.
"""
import collections, sys, time
import backfill as B

LO, HI = 0.90, 0.96
WINDOW_H = 4
PER_SERIES = 14
MIN_SETTLED = 60
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2400
SPORT_WORDS = ("sport", "football", "basketball", "baseball", "hockey",
               "soccer", "tennis", "golf", "racing", "nfl", "nba", "mlb",
               "nhl", "ufc", "boxing", "cricket", "rugby")


def first_touch(series, ticker, close):
    """(entry_price, minutes_in_band) on the first visit to the band."""
    end = B.ts(close)
    if end is None:
        return None, 0
    d, err = B.get(f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                   start_ts=int(end) - WINDOW_H * 3600, end_ts=int(end),
                   period_interval=1)
    cs = (d or {}).get("candlesticks") or []
    rows = []
    for c in cs:
        t = B._f(c.get("end_period_ts"))
        v = c.get("yes_ask")
        a = B._f(v.get("close_dollars")) if isinstance(v, dict) else None
        vb = c.get("yes_bid")
        b = B._f(vb.get("close_dollars")) if isinstance(vb, dict) else None
        if t is None or a is None or b is None:
            continue
        if not (0 < b <= a < 1):
            continue
        rows.append((t, a))
    rows.sort()
    entry = None
    inband = 0
    for _t, a in rows:
        if LO <= a < HI:
            inband += 1
            if entry is None:
                entry = a
    return entry, inband


def main():
    print("=" * 102)
    print("WHICH KALSHI SPORTS ARE WORTH A BACKTEST?")
    print(f"  first touch of {100*LO:.0f}-{100*HI:.0f}c in the last "
          f"{WINDOW_H}h before close, held to settlement")
    print("=" * 102)
    d, err = B.get("/trade-api/v2/series")
    allser = (d or {}).get("series") or []
    sporty = [s for s in allser
              if any(w in (str(s.get("category") or "") + " "
                           + str(s.get("title") or "")).lower()
                     for w in SPORT_WORDS)]
    print(f"  {len(allser):,} series, {len(sporty):,} look like sports\n")

    started = time.time()
    out = []
    checked = 0
    for s in sporty:
        if time.time() - started > BUDGET:
            print(f"  [budget reached after {checked} series]")
            break
        tk = str(s.get("ticker") or "")
        if not tk:
            continue
        try:
            mk = B.settled_markets(tk, 200)
        except Exception:
            continue
        if len(mk) < MIN_SETTLED:
            continue
        checked += 1
        bets = []
        band_minutes = 0
        looked = 0
        for m in mk[:PER_SERIES]:
            if time.time() - started > BUDGET:
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            looked += 1
            e, mins = first_touch(tk, str(m.get("ticker")), m.get("close_time"))
            band_minutes += mins
            if e is not None:
                bets.append((e, res == "yes"))
            time.sleep(B.PAUSE)
        if looked:
            out.append(dict(tk=tk, settled=len(mk), looked=looked,
                            bets=bets, mins=band_minutes,
                            title=str(s.get("title") or "")[:28]))

    print(f"  {'series':<26} {'settled':>8} {'seen':>5} {'hit band':>9} "
          f"{'avg entry':>10} {'won':>7} {'gap':>7}")
    tot = []
    for r in sorted(out, key=lambda x: -len(x["bets"])):
        n = len(r["bets"])
        if not n:
            print(f"  {r['tk']:<26} {r['settled']:>8,} {r['looked']:>5} "
                  f"{0:>9} {'-':>10} {'-':>7} {'-':>7}")
            continue
        px = 100.0 * sum(p for p, _ in r["bets"]) / n
        w = 100.0 * sum(1 for _, y in r["bets"] if y) / n
        print(f"  {r['tk']:<26} {r['settled']:>8,} {r['looked']:>5} "
              f"{n:>9} {px:>9.1f}c {w:>6.1f}% {w-px:>+6.1f}")
        tot.extend(r["bets"])

    print("\n" + "=" * 102)
    if tot:
        px = 100.0 * sum(p for p, _ in tot) / len(tot)
        w = 100.0 * sum(1 for _, y in tot if y) / len(tot)
        print(f"  ALL SPORTS POOLED: {len(tot)} bets, avg entry {px:.1f}c, "
              f"won {w:.1f}%, gap {w-px:+.1f} points")
        print()
        print("  A POSITIVE GAP IS NOT AN EDGE YET. These are a dozen markets")
        print("  per series on an arbitrary first-touch rule, and every series")
        print("  shown was examined, so the best-looking row is the best of")
        print("  many and will have flattered itself. What the table is for is")
        print("  deciding WHERE to spend a real backtest -- a series needs")
        print("  volume, quoted prices, and a gap that survives a proper spec.")
    else:
        print("  Nothing reached the band. Either the window is wrong for these")
        print("  sports or the quotes are not there.")
    print("=" * 102)
    return 0


if __name__ == "__main__":
    sys.exit(main())

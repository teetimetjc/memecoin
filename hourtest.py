"""Are some hours genuinely worse, or is that 10 bets per cell talking?

THE LIVE RECORD SAYS THREE HOURS CARRY THE DAMAGE -- 03:00, 07:00 and 08:00
CDT, together -$37.74 over 33 bets, while most other hours are clean. But
301 bets split 24 ways is about 12 bets an hour, and at a 4% loss rate half
the hours should show zero losses by luck alone. That is what the live table
looks like, so it cannot distinguish a real effect from a thin slice.

There is a MECHANISM available, which is why this is worth testing rather
than dismissing: 03:00, 07:00 and 08:00 CDT are 08:00, 12:00 and 13:00 UTC,
the London open and the London-New York overlap, when crypto volatility
genuinely peaks.

So: the same question, asked of the backtest history where each hour has
hundreds of observations instead of ten, and split chronologically so a
pattern has to survive into a period it was not chosen on.

CDT THROUGHOUT, because that is the clock the operator lives on and the one
the live table used. Central is UTC-5 in daylight time; the markets here are
all from an October that is entirely CDT, so a fixed offset is right for
this data and would need revisiting after the November change.

Read-only.
"""
import collections
import statistics as st
import sys
import time

import backfill as B

BAND = (0.90, 0.96)
ENTRY_OFFSET = 9
PER_SERIES = 1600
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
CDT_OFFSET = 5 * 3600


def entry_ask(cs, close_ts):
    """The yes ask at T-9, from the candle covering that minute."""
    want = close_ts - ENTRY_OFFSET * 60
    best = None
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None or t > want:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars"))
                    if isinstance(v, dict) else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        if best is None or t > best[0]:
            best = (t, a)
    return best[1] if best else None


def main():
    print("=" * 92)
    print("IS ANY HOUR GENUINELY WORSE? Backtest history, CDT, split in half")
    print("=" * 92)
    rows = []
    started = time.time()
    for ser in SERIES:
        if time.time() - started > BUDGET:
            break
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception:
            continue
        print(f"  {ser}: {len(mk)} settled markets")
        for m in mk:
            if time.time() - started > BUDGET:
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            a = entry_ask(cs, close)
            if a is None or not (BAND[0] <= a <= BAND[1]):
                continue
            hour = time.gmtime(close - CDT_OFFSET).tm_hour
            rows.append(dict(ts=close, hour=hour, ask=a, won=(res == "yes")))

    print(f"\n  {len(rows)} in-band observations\n")
    if len(rows) < 400:
        print("  TOO FEW TO SPLIT BY HOUR.")
        return 0
    rows.sort(key=lambda r: r["ts"])
    cut = len(rows) // 2
    halves = {"TRAIN": rows[:cut], "TEST": rows[cut:]}

    def gap(g):
        if not g:
            return None, 0
        w = 100.0 * sum(1 for r in g if r["won"]) / len(g)
        p = 100.0 * st.mean([r["ask"] for r in g])
        return w - p, len(g)

    print("  %-8s %-18s %-18s %s" % ("hour CDT", "TRAIN gap (n)",
                                     "TEST gap (n)", "verdict"))
    flagged = []
    for h in range(24):
        gt, nt = gap([r for r in halves["TRAIN"] if r["hour"] == h])
        gv, nv = gap([r for r in halves["TEST"] if r["hour"] == h])
        if nt < 20 or nv < 20:
            print("  %02d:00    %-18s %-18s thin"
                  % (h, f"{gt:+.2f} ({nt})" if gt is not None else f"- ({nt})",
                     f"{gv:+.2f} ({nv})" if gv is not None else f"- ({nv})"))
            continue
        both = gt < 0 and gv < 0
        if both:
            flagged.append((h, gt, gv, nt + nv))
        print("  %02d:00    %-18s %-18s %s"
              % (h, f"{gt:+.2f} ({nt})", f"{gv:+.2f} ({nv})",
                 "NEGATIVE IN BOTH" if both else ""))
    print()
    live = (3, 7, 8)
    print("  The live record flagged 03:00, 07:00 and 08:00 CDT.")
    for h in live:
        gt, nt = gap([r for r in halves["TRAIN"] if r["hour"] == h])
        gv, nv = gap([r for r in halves["TEST"] if r["hour"] == h])
        if gt is None or gv is None:
            continue
        print("    %02d:00 -> train %+.2f (n=%d), test %+.2f (n=%d)%s"
              % (h, gt, nt, gv, nv,
                 "  <-- holds" if (gt < 0 and gv < 0) else "  <-- does not hold"))
    print()
    print("  %d hour(s) negative in both halves: %s"
          % (len(flagged), ", ".join("%02d:00" % h for h, _, _, _ in flagged)
             or "none"))
    print()
    print("  Expect one or two hours to be negative in both halves by chance")
    print("  alone out of 24. A result is only interesting if it is both")
    print("  consistent AND one the live record independently pointed at.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

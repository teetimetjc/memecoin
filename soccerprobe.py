"""Can a live soccer favourite even be backtested on Kalshi?

THE PROPOSED RULE: live soccer, second half, favourite priced near 90c, take
it and hold to settlement.

THE RULE IS TESTABLE ONLY IF THE PRICE HISTORY EXISTS. The crypto backtest
works because /candlesticks returns a per-minute quote for every market, so
the price at T-9 can be read years later. Nobody has checked whether sports
series carry the same thing. If they do not, there is no backtest, the idea
cannot be evaluated before money is risked, and that is the end of it until
a forward collector has run for months.

WHAT IT CHECKS, in order:
  1. a soccer series with settled markets and how long they live
  2. whether candlesticks come back for one, and at what granularity
  3. how the price actually travelled -- because 'second half' has to be
     approximated by time-to-close, and that approximation is only honest if
     the market's lifetime is predictable

A NOTE ON WHAT 90c MEANS. It is a price, not a probability. The question the
eventual test must answer is not whether 90c favourites usually win -- they
will, about nine times in ten -- but whether they win MORE than nine times in
ten, by enough to clear the fee. Everything this project has measured so far
says markets of this shape are calibrated.

Read-only.
"""
import sys, time
import backfill as B

CANDIDATES = ["KXMLSGAME", "KXEPLGAME", "KXUCLGAME", "KXSERIEAGAME",
              "KXLALIGAGAME", "KXBUNDESLIGAGAME", "KXLIGAMXGAME"]

def main():
    print("="*92)
    print("CAN A LIVE SOCCER FAVOURITE BE BACKTESTED ON KALSHI?")
    print("="*92)
    found = None
    for tk in CANDIDATES:
        try:
            mk = B.settled_markets(tk, 40)
        except Exception as e:
            print(f"  {tk:<22} error {str(e)[:40]}")
            continue
        print(f"  {tk:<22} {len(mk):>4} settled market(s)")
        if mk and found is None:
            found = (tk, mk)
        time.sleep(B.PAUSE)
    if not found:
        print("\n  No settled soccer markets found under those tickers.")
        print("  The series names are a guess; list them from the series")
        print("  index before concluding the markets do not exist.")
        return 0

    tk, mk = found
    print(f"\n  Using {tk}. Inspecting up to 5 markets for price history:\n")
    ok = 0
    for m in mk[:5]:
        mt = str(m.get("ticker") or "")
        close = m.get("close_time")
        open_t = m.get("open_time")
        ts_c, ts_o = B.ts(close), B.ts(open_t)
        life = ((ts_c - ts_o) / 3600.0) if (ts_c and ts_o) else float("nan")
        d, err = B.get(f"/trade-api/v2/series/{tk}/markets/{mt}/candlesticks",
                       start_ts=int(ts_c) - 3 * 3600 if ts_c else 0,
                       end_ts=int(ts_c) if ts_c else 0,
                       period_interval=1)
        cs = (d or {}).get("candlesticks") or []
        quoted = 0
        band = 0
        for c in cs:
            v = c.get("yes_ask")
            a = B._f(v.get("close_dollars")) if isinstance(v, dict) else None
            if a is None or not (0 < a < 1):
                continue
            quoted += 1
            if 0.88 <= a < 0.96:
                band += 1
        print(f"    {mt:<40} life {life:>5.1f}h  candles {len(cs):>4}  "
              f"quoted {quoted:>4}  in 88-96c {band:>4}"
              + ("" if cs else f"   [{err}]"))
        if quoted:
            ok += 1
        time.sleep(B.PAUSE)

    print("\n" + "="*92)
    if ok:
        print(f"  BACKTESTABLE. {ok} of 5 markets returned a per-minute quote")
        print("  history, so a 'second half at 90c' rule can be scored against")
        print("  real outcomes before a cent is risked.")
        print()
        print("  The next question is the trigger. 'Second half' has to be")
        print("  expressed as minutes before close, and that is only sound if")
        print("  market lifetimes are consistent -- which the life column")
        print("  above is there to show.")
    else:
        print("  NOT BACKTESTABLE TODAY. Settled soccer markets exist but no")
        print("  per-minute quote history came back, so the rule cannot be")
        print("  evaluated against history. The only honest path would be a")
        print("  forward collector running for months before any bet.")
    print("="*92)
    return 0

if __name__ == "__main__":
    sys.exit(main())

"""What keys does a candlestick actually have, and where is volume?

WHY THIS EXISTS. Twice now longrun has dropped every single market for
"volume under 100" with exactly zero survivors. The first time the cause was
that the settled /markets response has no volume key at all. The second time
the candles came back fine (only 64 of 5,906 were empty) and yet nothing
passed, which is the same signature a second time: a key that isn't there
read as a zero. So before any conclusion, print the raw candle.

Prints the key names of real candlesticks from long-lived settled markets
and a couple of whole candle objects, verbatim. Read-only, tiny budget.
"""
import collections, json, sys, time
import backfill as B

MIN_LIFE_H = 24
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 300
WANT = 6
SKIP = ("15m", "KXBTC", "KXETH", "KXSOL", "KXXRP", "KXDOGE")


def main():
    print("=" * 86)
    print("CANDLESTICK KEY PROBE")
    print("=" * 86)
    d, _e = B.get("/trade-api/v2/series")
    ser = [s for s in ((d or {}).get("series") or [])
           if not any(k.lower() in str(s.get("ticker") or "").lower()
                      for k in SKIP)]
    started = time.time()
    keys = collections.Counter()
    shown = 0
    markets = 0
    for s in ser:
        if shown >= WANT or time.time() - started > BUDGET:
            break
        tk = str(s.get("ticker") or "")
        if not tk:
            continue
        try:
            mk = B.settled_markets(tk, 12)
        except Exception:
            continue
        for m in mk:
            if shown >= WANT:
                break
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if o is None or c is None:
                continue
            life = (c - o) / 3600.0
            if life < MIN_LIFE_H:
                continue
            hrs = min(int(life) + 1, 240)
            dd, _x = B.get(
                f"/trade-api/v2/series/{tk}/markets/{m.get('ticker')}/candlesticks",
                start_ts=int(c) - hrs * 3600, end_ts=int(c),
                period_interval=60)
            cands = (dd or {}).get("candlesticks") or []
            markets += 1
            if not cands:
                continue
            for cd in cands:
                keys.update(cd.keys())
            print(f"\n--- {m.get('ticker')}  life {life:.0f}h  "
                  f"{len(cands)} candles")
            mid = cands[len(cands) // 2]
            print(json.dumps(mid, indent=2, default=str)[:1400])
            shown += 1
            time.sleep(B.PAUSE)

    print(f"\n  {markets} long-lived markets fetched, {shown} printed\n")
    print("  KEY FREQUENCY ACROSS ALL CANDLES SEEN:")
    for k, n in keys.most_common():
        print(f"    {k:<28} {n:,}")
    print()
    print("  Look for whatever carries traded size. If there is no volume-ish")
    print("  key at top level, the filter has to read it from wherever it is")
    print("  -- or the backtest has to use open interest instead.")


if __name__ == "__main__":
    main()

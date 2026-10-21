"""Does the UNDERLYING's volatility before entry predict a favourite failing?

THE QUESTION THE HOUR TEST COULD NOT ANSWER. Losses cluster at 03:00, 08:00
and 22:00 CDT -- the London open, the London-NY overlap and the Asia open.
Ten bets an hour cannot tell a real effect from clustering, and the backtest
by hour came back too thin to split. But "hour" was only ever a proxy for
the thing that actually matters: how much the coin was MOVING when the bet
was placed. That applies at any hour and has far more data behind it.

WHY THE UNDERLYING AND NOT THE CONTRACT. fragile.py already tested the
contract's own price path and found nothing -- every bucket flipped sign
between halves. But a contract's path is the market's OPINION, and by
construction it is already priced in. The coin's own movement is the input
to that opinion, and the question is whether the market under-reacts to it.
Those are different questions and only one has been asked.

SPOT COMES FROM COINBASE, one-minute bars, fetched once per coin for the
whole period and indexed, rather than per market. spot_probe.py established
that Coinbase serves explicit start/end windows while Kraken only returns
the most recent ~720 candles -- which is twelve hours, not three weeks.

FEATURES at T-9, from the fifteen minutes BEFORE it, so nothing after the
decision leaks in:
    rv15   realised volatility: stdev of the 1-minute log returns
    move   absolute move over those fifteen minutes, in percent
    rng    high-to-low range over those fifteen minutes, in percent

Chronological split, as always: a bucket counts only if its gap is negative
in BOTH halves. Read-only.
"""
import collections
import math
import statistics as st
import sys
import time

import requests

import backfill as B

BAND = (0.90, 0.96)
ENTRY_OFFSET = 9
LOOKBACK_MIN = 15
PER_SERIES = 800
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
PAIRS = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD",
         "KXXRP15M": "XRP-USD", "KXDOGE15M": "DOGE-USD"}
CB = "https://api.exchange.coinbase.com/products/%s/candles"


def spot_series(pair, start, end):
    """{minute_epoch: close} for pair over [start, end], 1-minute bars.

    Coinbase caps a response at 300 candles, so the window is walked in
    300-minute chunks. Fetched ONCE per coin for the whole period: doing it
    per market would be thousands of calls for the same bars.
    """
    out = {}
    t = start
    while t < end:
        stop = min(end, t + 300 * 60)
        try:
            r = requests.get(CB % pair, timeout=20, params=dict(
                granularity=60,
                start=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)),
                end=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stop))))
            if r.ok:
                for row in r.json() or []:
                    # [ time, low, high, open, close, volume ]
                    try:
                        out[int(row[0])] = (float(row[4]), float(row[1]),
                                            float(row[2]))
                    except (TypeError, ValueError, IndexError):
                        continue
        except Exception:
            pass
        t = stop
        time.sleep(0.25)
    return out


def features(series, entry_ts):
    """(rv15, move_pct, range_pct) from the 15 minutes before entry."""
    bars = []
    for k in range(LOOKBACK_MIN + 1):
        m = int((entry_ts - k * 60) // 60 * 60)
        if m in series:
            bars.append((m, series[m]))
    if len(bars) < 8:
        return None
    bars.sort()
    closes = [b[1][0] for b in bars]
    lows = [b[1][1] for b in bars]
    highs = [b[1][2] for b in bars]
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 5:
        return None
    rv = st.pstdev(rets) * 100.0
    move = abs(closes[-1] / closes[0] - 1.0) * 100.0
    rng = (max(highs) / min(lows) - 1.0) * 100.0 if min(lows) > 0 else 0.0
    return rv, move, rng


def main():
    print("=" * 98)
    print("UNDERLYING VOLATILITY BEFORE ENTRY vs the favourite failing")
    print("=" * 98)
    started = time.time()
    markets = collections.defaultdict(list)
    lo_ts, hi_ts = None, None
    for ser in PAIRS:
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception:
            continue
        for m in mk:
            res = str(m.get("result") or "").lower()
            close = B.ts(m.get("close_time"))
            if res not in ("yes", "no") or close is None:
                continue
            markets[ser].append((close, m, res))
            lo_ts = close if lo_ts is None else min(lo_ts, close)
            hi_ts = close if hi_ts is None else max(hi_ts, close)
        print(f"  {ser}: {len(markets[ser])} settled markets")
    if not markets or lo_ts is None:
        print("  nothing to do")
        return 0
    span_days = (hi_ts - lo_ts) / 86400.0
    print(f"\n  period spans {span_days:.1f} days; fetching 1-minute spot\n")

    spots = {}
    for ser, pair in PAIRS.items():
        if not markets[ser]:
            continue
        s = spot_series(pair, lo_ts - 3600, hi_ts + 120)
        spots[ser] = s
        print(f"  {pair}: {len(s):,} one-minute bars")

    rows = []
    for ser, items in markets.items():
        series = spots.get(ser) or {}
        if not series:
            continue
        for close, m, res in items:
            if time.time() - started > BUDGET:
                break
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            want = close - ENTRY_OFFSET * 60
            ask = None
            best_t = None
            for cd in cs or []:
                t = B._f(cd.get("end_period_ts"))
                if t is None or t > want:
                    continue
                v = cd.get("yes_ask")
                a = (B._f(v.get("close_dollars"))
                     if isinstance(v, dict) else None)
                if a is None or not (0 < a < 1):
                    continue
                if best_t is None or t > best_t:
                    best_t, ask = t, a
            if ask is None or not (BAND[0] <= ask <= BAND[1]):
                continue
            f = features(series, want)
            if not f:
                continue
            rv, move, rng = f
            rows.append(dict(ts=close, ask=ask, rv=rv, move=move, rng=rng,
                             won=(res == "yes")))

    print(f"\n  {len(rows)} in-band observations with spot data\n")
    if len(rows) < 300:
        print("  TOO FEW TO SPLIT.")
        return 0
    rows.sort(key=lambda r: r["ts"])
    cut = len(rows) // 2
    halves = (("TRAIN (older half)", rows[:cut]),
              ("TEST  (newer half)", rows[cut:]))

    def report(name, data, key, edges, unit):
        print(f"  {name} -- by {key} ({unit})")
        print("    %-16s %-7s %-9s %-10s %s"
              % ("bucket", "n", "won %", "priced %", "gap"))
        for lo, hi in edges:
            g = [r for r in data if lo <= r[key] < hi]
            if len(g) < 20:
                print("    %-16s %-7d (too few)"
                      % (f"{lo:.3f}-{hi:.3f}", len(g)))
                continue
            w = 100.0 * sum(1 for r in g if r["won"]) / len(g)
            p = 100.0 * st.mean([r["ask"] for r in g])
            print("    %-16s %-7d %-9.2f %-10.2f %+.2f"
                  % (f"{lo:.3f}-{hi:.3f}", len(g), w, p, w - p))
        print()

    for key, edges, unit in (
        ("rv", [(0, .02), (.02, .04), (.04, .07), (.07, .12), (.12, 9)],
         "% per minute"),
        ("move", [(0, .05), (.05, .1), (.1, .2), (.2, .4), (.4, 9)],
         "% over 15 min"),
        ("rng", [(0, .1), (.1, .2), (.2, .35), (.35, .6), (.6, 9)],
         "% high-low"),
    ):
        for name, data in halves:
            report(name, data, key, edges, unit)

    print("  A bucket matters only if its gap is negative in BOTH halves.")
    print("  The hour pattern predicts the HIGH-volatility buckets should be")
    print("  the negative ones; if they are not, the hour clustering was")
    print("  coincidence rather than a volatility effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

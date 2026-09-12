"""Reconstruct the collection gap from Kraken history and score the CVD rule.

Collection stopped 2026-09-11 16:30 UTC when the workbook hit Google's cell
cap. This replays that window from public Kraken data and asks one question:
did the frozen CVD rule keep predicting direction?

Read-only. Writes nothing to the sheet, so nothing it produces can contaminate
the live forward test. Kalshi prices are not recoverable for past windows, so
this reports direction accuracy only -- not P&L.

Usage:  python gap_check.py [hours_back]
"""

import sys, time, requests
from datetime import datetime, timezone, timedelta
from collections import defaultdict

KRAKEN = "https://api.kraken.com/0/public"
PAIRS = {"BTCUSDT": "XBTUSD", "ETHUSDT": "ETHUSD", "SOLUSDT": "SOLUSD",
         "XRPUSDT": "XRPUSD", "DOGEUSDT": "XDGUSD"}

# Frozen 2026-09-11, before any of this window existed. Must match predictor.py.
CVD_THRESHOLD = 0.30

MAX_CALLS_PER_SYMBOL = 120          # guard against a runaway pager
REQUEST_PAUSE = 1.1                 # Kraken public tier is about 1 req/sec


def fetch_trades(pair, since_dt, until_dt):
    """Page the public trade tape forward. Returns [(ts, volume, side)]."""
    out, cursor, calls = [], int(since_dt.timestamp() * 1_000_000_000), 0
    while calls < MAX_CALLS_PER_SYMBOL:
        calls += 1
        try:
            r = requests.get(f"{KRAKEN}/Trades",
                             params={"pair": pair, "since": cursor}, timeout=20)
            data = r.json()
        except Exception as e:
            print(f"    fetch error: {e}")
            break
        if data.get("error"):
            print(f"    kraken error: {data['error']}")
            break
        result = data.get("result", {})
        key = next((k for k in result if k != "last"), None)
        if key is None:
            break
        batch = result[key]
        if not batch:
            break
        for t in batch:
            try:
                ts, vol, side = float(t[2]), float(t[1]), t[3]
            except (TypeError, ValueError, IndexError):
                continue
            out.append((ts, vol, side))
        last = result.get("last")
        if not last or int(last) <= cursor:
            break
        cursor = int(last)
        if out and out[-1][0] >= until_dt.timestamp():
            break
        time.sleep(REQUEST_PAUSE)
    return out, calls


def fetch_closes(pair):
    """1-minute closes keyed by epoch-minute."""
    r = requests.get(f"{KRAKEN}/OHLC", params={"pair": pair, "interval": 1}, timeout=20)
    data = r.json()
    if data.get("error"):
        raise RuntimeError(data["error"])
    key = [k for k in data["result"] if k != "last"][0]
    return {int(c[0]): float(c[4]) for c in data["result"][key]}


def main():
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
    now = datetime.now(timezone.utc)
    end = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    print(f"Replaying {start:%Y-%m-%d %H:%M} -> {end:%Y-%m-%d %H:%M} UTC")
    print(f"CVD threshold {CVD_THRESHOLD} (frozen 2026-09-11)\n")

    grand = defaultdict(lambda: [0, 0])
    for symbol, pair in PAIRS.items():
        try:
            closes = fetch_closes(pair)
        except Exception as e:
            print(f"{symbol}: OHLC failed -- {e}")
            continue
        trades, calls = fetch_trades(pair, start, end)
        if not trades:
            print(f"{symbol}: no trades returned")
            continue
        covered = datetime.fromtimestamp(min(t[0] for t in trades), timezone.utc)

        # bucket aggressive volume into the 15-min window each trade falls in
        buckets = defaultdict(lambda: [0.0, 0.0])
        for ts, vol, side in trades:
            b = int(ts // 900) * 900
            buckets[b][0 if side == "b" else 1] += vol

        fired = correct = 0
        for b in sorted(buckets):
            buy, sell = buckets[b]
            total = buy + sell
            if total <= 0:
                continue
            cvd = (buy - sell) / total
            if abs(cvd) < CVD_THRESHOLD:
                continue
            call = "UP" if cvd < 0 else "DOWN"     # fade the aggressive side
            # window b covers [b, b+900); the prediction is for the NEXT window
            p0 = closes.get((b + 900) // 60)
            p1 = closes.get((b + 1800) // 60)
            if p0 is None or p1 is None:
                continue
            went_up = p1 > p0
            fired += 1
            if (call == "UP") == went_up:
                correct += 1
        grand[symbol] = [correct, fired]
        pct = f"{correct / fired * 100:5.1f}%" if fired else "   n/a"
        print(f"{symbol:9} {correct:3}/{fired:3} = {pct}   "
              f"({calls} calls, tape from {covered:%m-%d %H:%M})")

    tc = sum(v[0] for v in grand.values())
    tf = sum(v[1] for v in grand.values())
    print()
    if tf:
        import math
        p = tc / tf
        se = math.sqrt(0.25 / tf)
        print(f"TOTAL {tc}/{tf} = {p * 100:.1f}%   z vs 50% = {(p - 0.5) / se:+.2f}")
        print(f"in-sample claim was 56.8%")
    else:
        print("no windows qualified")


if __name__ == "__main__":
    main()

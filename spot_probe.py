"""Which spot-price source can actually give us 70 days of 1-minute bars?

The indicators are all computed from 1m closes, so backfilling them needs
1m history over the same window as M15H. predictor uses Kraken's OHLC
endpoint, which returns only the most recent ~720 candles no matter what
`since` is set to -- twelve hours, not seventy days. So the source has to
change, and guessing which one works from a runner is how this project has
lost days.

Three candidates, asked the same question: give me one minute of BTC from
sixty days ago. Binance has full history but geo-blocks some regions and
GitHub's runners are usually US. Coinbase serves 300 candles per call with
explicit start/end. Kraken is included to confirm the limitation rather
than assume it.

Read-only.
"""
import calendar
import json
import sys
import time

import requests

WANT_DAYS = 60
SYM = {"binance": "BTCUSDT", "coinbase": "BTC-USD", "kraken": "XBTUSD"}


def stamp(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts))


def main():
    target = int(time.time()) - WANT_DAYS * 86400
    print("=" * 76)
    print(f"CAN WE GET 1-MINUTE BARS FROM {stamp(target)} (~{WANT_DAYS}d ago)?")
    print("=" * 76)

    print("\nBINANCE  /api/v3/klines")
    try:
        r = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": SYM["binance"], "interval": "1m",
                                 "startTime": target * 1000, "limit": 3},
                         timeout=20)
        print(f"  HTTP {r.status_code}")
        if r.ok:
            d = r.json()
            print(f"  {len(d)} candles; first opens {stamp(d[0][0]//1000)}"
                  f"  close={d[0][4]}")
        else:
            print(f"  body: {r.text[:160]}")
    except Exception as e:
        print(f"  ERROR {str(e)[:140]}")

    print("\nCOINBASE  /products/{id}/candles")
    try:
        r = requests.get(f"https://api.exchange.coinbase.com/products/"
                         f"{SYM['coinbase']}/candles",
                         params={"granularity": 60,
                                 "start": stamp(target).replace(" ", "T") + ":00Z",
                                 "end": stamp(target + 600).replace(" ", "T") + ":00Z"},
                         headers={"User-Agent": "research"}, timeout=20)
        print(f"  HTTP {r.status_code}")
        if r.ok:
            d = r.json()
            print(f"  {len(d)} candles")
            if d:
                # coinbase returns [time, low, high, open, close, volume]
                oldest = min(d, key=lambda c: c[0])
                print(f"  oldest {stamp(oldest[0])}  close={oldest[4]}")
        else:
            print(f"  body: {r.text[:160]}")
    except Exception as e:
        print(f"  ERROR {str(e)[:140]}")

    print("\nKRAKEN  /0/public/OHLC with since  (expected to refuse to go back)")
    try:
        r = requests.get("https://api.kraken.com/0/public/OHLC",
                         params={"pair": SYM["kraken"], "interval": 1,
                                 "since": target}, timeout=20)
        print(f"  HTTP {r.status_code}")
        if r.ok:
            d = r.json()
            res = d.get("result") or {}
            key = [k for k in res if k != "last"]
            if key:
                c = res[key[0]]
                print(f"  {len(c)} candles; oldest {stamp(int(c[0][0]))}"
                      f"  newest {stamp(int(c[-1][0]))}")
                age = (time.time() - int(c[0][0])) / 86400.0
                print(f"  oldest candle is {age:.1f} days old -- asked for "
                      f"{WANT_DAYS}")
    except Exception as e:
        print(f"  ERROR {str(e)[:140]}")

    print("\n" + "=" * 76)
    print("Whichever source returns a candle from the requested day is the one")
    print("the indicator backfill gets built on. If none do, indicators cannot")
    print("be reconstructed for the history and only forward collection can")
    print("add them -- which is a real answer, not a setback.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())

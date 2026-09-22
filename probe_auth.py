"""Does the missing Kalshi data actually need credentials?

WHY THIS RUNS BEFORE ANY PROXY IS BUILT. The overnight paper run produced
mid=None, spread=None and orderbook=None on 1978 of 1978 ticks, and the
obvious reading is "unauthenticated, so no market data". But predictor.py's
own signing helper carries a note saying /markets is PUBLIC and returns data
whether the signature is valid, invalid or absent -- and the bot plainly did
reach it, because tickers and close_times came back populated on every tick.

Both cannot be true. Building a signing proxy on the strength of the guess
would be a day's work aimed at a cause that was never measured, and if the
real fault is the query rather than the credentials the proxy would change
nothing while looking like a fix.

So this asks the three endpoints directly, each way:

  /markets?series_ticker=...&status=open&limit=1   what the bot asks for
  /markets?series_ticker=...&status=open&limit=20  the same without limit=1
  /markets/{ticker}                                single market
  /markets/{ticker}/orderbook                      the book

with a signature and without, and prints whether yes_bid / yes_ask / the
book actually arrive. Whatever comes back decides the fix.

Read-only. GETs public market data. Places nothing.
"""

import json
import os
import sys

import requests

HOST = "https://api.elections.kalshi.com"
SERIES = "KXBTC15M"


def show(label, path, signed):
    import predictor as P
    hdrs = {}
    if signed:
        h = P._kalshi_headers("GET", path.split("?")[0])
        if not h:
            print(f"  {label:<34} SIGNED: no credentials in env")
            return None
        hdrs = h
    try:
        r = requests.get(HOST + path, headers=hdrs, timeout=15)
    except Exception as e:
        print(f"  {label:<34} {'signed' if signed else 'public':>6}: "
              f"ERROR {str(e)[:60]}")
        return None
    tag = "signed" if signed else "public"
    if not r.ok:
        print(f"  {label:<34} {tag:>6}: HTTP {r.status_code} "
              f"{r.text[:70]}")
        return None
    return r.json()


def main():
    import predictor as P
    have = bool(P._kalshi_headers("GET", "/trade-api/v2/markets"))
    print("=" * 72)
    print("DOES THE MISSING MARKET DATA NEED CREDENTIALS?")
    print("=" * 72)
    print(f"\ncredentials present in this job: {have}\n")

    ticker = None
    for signed in (False, True):
        for lim in (1, 20):
            path = (f"/trade-api/v2/markets?series_ticker={SERIES}"
                    f"&status=open&limit={lim}")
            d = show(f"markets limit={lim}", path, signed)
            if not d:
                continue
            mk = d.get("markets", [])
            quoted = [m for m in mk
                      if (m.get("yes_bid") or 0) > 0 or (m.get("yes_ask") or 0) > 0]
            print(f"  {'markets limit=%d' % lim:<34} "
                  f"{'signed' if signed else 'public':>6}: "
                  f"{len(mk)} market(s), {len(quoted)} with a live quote")
            for m in mk[:3]:
                print(f"       {m.get('ticker'):<28} "
                      f"yes_bid={m.get('yes_bid')} yes_ask={m.get('yes_ask')} "
                      f"vol={m.get('volume')} close={str(m.get('close_time'))[:19]}")
            if quoted and not ticker:
                ticker = quoted[0].get("ticker")
            if mk and not ticker:
                ticker = mk[0].get("ticker")
        print()

    if not ticker:
        print("no ticker to test the book with")
        return 1

    print(f"single market + orderbook, using {ticker}\n")
    for signed in (False, True):
        d = show("markets/{ticker}", f"/trade-api/v2/markets/{ticker}", signed)
        if d:
            m = d.get("market", {})
            print(f"  {'markets/{ticker}':<34} "
                  f"{'signed' if signed else 'public':>6}: "
                  f"yes_bid={m.get('yes_bid')} yes_ask={m.get('yes_ask')} "
                  f"result={m.get('result')!r}")
        d = show("markets/{ticker}/orderbook",
                 f"/trade-api/v2/markets/{ticker}/orderbook?depth=5", signed)
        if d:
            ob = d.get("orderbook") or {}
            fp = d.get("orderbook_fp") or {}
            print(f"  {'markets/{ticker}/orderbook':<34} "
                  f"{'signed' if signed else 'public':>6}: "
                  f"orderbook keys={list(ob.keys())} "
                  f"orderbook_fp keys={list(fp.keys())}")
            print(f"       raw: {json.dumps(d)[:220]}")
    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())

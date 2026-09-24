"""What else does Kalshi list, and can it be graded cheaply?

WHY A PROBE FIRST. Eleven strategies have now failed, every one of them on
15-minute crypto -- the highest-frequency, most bot-saturated market on the
exchange. That says nothing about the rest of it, and the honest next step
is to look rather than to assume either way.

But building a collector against a guessed response shape is how this
project lost a day to `orderbook` when the answer was `orderbook_fp`, and
another to a settlement flag that was wrong 19% of the time. So this asks
three questions and writes nothing:

  1. WHAT SERIES EXIST, and which have enough volume to be tradeable at all.

  2. DOES A SETTLED MARKET REMEMBER ITS PRICE. If the market object keeps a
     last price alongside `result`, then calibration -- did contracts priced
     at 70c win 70% of the time -- is computable in bulk from history, with
     no waiting and no collection. That single field decides whether test A
     takes an afternoon or a month.

  3. WHAT LONGER-HORIZON CRYPTO SERIES LOOK LIKE. Fifteen minutes of a coin
     is almost pure noise; a day of one is not. Same infrastructure, very
     different ratio of information to randomness.

Read-only. Places nothing, writes nothing.
"""

import collections
import json
import sys

import requests

HOST = "https://api.elections.kalshi.com"


def get(path, **params):
    import predictor as P
    try:
        h = P._kalshi_headers("GET", path.split("?")[0]) or {}
        r = requests.get(HOST + path, headers=h, params=params or None,
                         timeout=25)
        if not r.ok:
            print(f"    HTTP {r.status_code} {path} :: {r.text[:140]}")
            return None
        return r.json()
    except Exception as e:
        print(f"    ERROR {path} :: {str(e)[:120]}")
        return None


def q1_series():
    print("=" * 78)
    print("1. WHAT SERIES EXIST")
    print("=" * 78)
    d = get("/trade-api/v2/series")
    if not d:
        # The endpoint may want a category, or may not be public at all.
        print("  /series did not answer; falling back to scanning markets")
        return None
    s = d.get("series") or d.get("data") or []
    print(f"  {len(s)} series returned")
    if s:
        print(f"  keys on a series object: {list(s[0].keys())}")
        for x in s[:12]:
            print(f"    {str(x.get('ticker','?')):<16} "
                  f"{str(x.get('category','')):<22} "
                  f"{str(x.get('title',''))[:44]}")
    return s


def q2_settled_fields(series_ticker=None):
    print("\n" + "=" * 78)
    print("2. DOES A SETTLED MARKET REMEMBER ITS PRICE?")
    print("=" * 78)
    print("  (if yes, calibration is computable from history in bulk)")
    # MUST filter by series. Unfiltered, /markets returns a thousand rows of
    # whatever one series it feels like -- the first run reported "0 crypto
    # families open" purely because of that, which is a statement about the
    # query and not the exchange.
    params = dict(status="settled", limit=200)
    if series_ticker:
        params["series_ticker"] = series_ticker
    d = get("/trade-api/v2/markets", **params)
    if not d:
        return
    mk = d.get("markets") or []
    print(f"\n  {len(mk)} settled markets returned")
    if not mk:
        return
    print(f"  keys on a settled market: {sorted(mk[0].keys())}\n")
    price_fields = [k for k in mk[0]
                    if any(w in k.lower() for w in
                           ("price", "bid", "ask", "close", "last", "settle"))]
    print(f"  price-ish fields: {price_fields}\n")
    for m in mk[:4]:
        print(f"    {m.get('ticker','?')[:40]:<42} result={m.get('result','?'):<4}")
        for k in price_fields:
            print(f"        {k:<26} {m.get(k)}")
        break
    # how many carry a usable last price AND a result?
    usable = 0
    byser = collections.Counter()
    for m in mk:
        res = str(m.get("result") or "").lower()
        # The API moved to _dollars-suffixed fields -- the same migration
        # that turned `orderbook` into `orderbook_fp`. Checking the old
        # spelling reported 0/200 on a payload that plainly had the data.
        lp = m.get("last_price_dollars")
        if lp is None:
            lp = m.get("previous_price_dollars")
        if res in ("yes", "no") and lp is not None:
            usable += 1
        t = str(m.get("ticker") or "")
        byser[t.split("-")[0]] += 1
    print(f"\n  settled markets with BOTH a result and a last price: "
          f"{usable}/{len(mk)}")
    print(f"\n  series represented in this sample:")
    for k, v in byser.most_common(18):
        print(f"    {k:<20} {v}")


def q3_horizons(series):
    """Crypto series by FREQUENCY -- the field that separates a 15-minute
    coin flip from a market where information actually matters."""
    print("\n" + "=" * 78)
    print("3. CRYPTO SERIES BY HORIZON")
    print("=" * 78)
    if not series:
        print("  no series list to work from")
        return []
    cry = [s for s in series
           if "crypto" in str(s.get("category", "")).lower()
           or any(c in str(s.get("ticker", "")).upper()
                  for c in ("BTC", "ETH", "SOL", "XRP", "DOGE"))]
    print(f"  {len(cry)} crypto-ish series of {len(series)} total\n")
    byfreq = collections.defaultdict(list)
    for s in cry:
        byfreq[str(s.get("frequency") or "?")].append(s)
    print(f"  {'frequency':<16} {'count':>6}   examples")
    for f, v in sorted(byfreq.items(), key=lambda x: -len(x[1])):
        ex = ", ".join(str(x.get("ticker")) for x in v[:3])
        print(f"  {f:<16} {len(v):>6}   {ex[:56]}")
    return cry


def main():
    series = q1_series()
    cry = q3_horizons(series)
    # Check price retention on a series we actually care about, not on
    # whatever the unfiltered endpoint happens to return.
    q2_settled_fields("KXBTC15M")
    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())

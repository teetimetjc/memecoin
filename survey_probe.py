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


def q2_settled_fields():
    print("\n" + "=" * 78)
    print("2. DOES A SETTLED MARKET REMEMBER ITS PRICE?")
    print("=" * 78)
    print("  (if yes, calibration is computable from history in bulk)")
    d = get("/trade-api/v2/markets", status="settled", limit=200)
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
        lp = m.get("last_price")
        if lp is None:
            lp = m.get("close_price") or m.get("settlement_value")
        if res in ("yes", "no") and lp is not None:
            usable += 1
        t = str(m.get("ticker") or "")
        byser[t.split("-")[0]] += 1
    print(f"\n  settled markets with BOTH a result and a last price: "
          f"{usable}/{len(mk)}")
    print(f"\n  series represented in this sample:")
    for k, v in byser.most_common(18):
        print(f"    {k:<20} {v}")


def q3_horizons():
    print("\n" + "=" * 78)
    print("3. LONGER-HORIZON CRYPTO")
    print("=" * 78)
    d = get("/trade-api/v2/markets", status="open", limit=1000)
    if not d:
        return
    mk = d.get("markets") or []
    print(f"  {len(mk)} open markets scanned")
    fam = collections.defaultdict(list)
    for m in mk:
        fam[str(m.get("ticker") or "").split("-")[0]].append(m)
    crypto = {k: v for k, v in fam.items()
              if any(c in k.upper() for c in
                     ("BTC", "ETH", "SOL", "XRP", "DOGE", "CRYPTO"))}
    print(f"\n  crypto-ish families open now: {len(crypto)}")
    print(f"    {'series':<18} {'open':>5} {'vol sum':>9} {'OI sum':>9} "
          f"{'example':<34}")
    for k, v in sorted(crypto.items(), key=lambda x: -len(x[1])):
        vol = sum(int(m.get("volume") or 0) for m in v)
        oi = sum(int(m.get("open_interest") or 0) for m in v)
        print(f"    {k:<18} {len(v):>5} {vol:>9} {oi:>9} "
              f"{str(v[0].get('ticker'))[:34]:<34}")
    print(f"\n  ALL open families by volume (where the liquidity actually is):")
    tot = [(sum(int(m.get('volume') or 0) for m in v), k, len(v))
           for k, v in fam.items()]
    tot.sort(reverse=True)
    print(f"    {'series':<20} {'open':>5} {'volume':>10}")
    for vol, k, n in tot[:20]:
        print(f"    {k:<20} {n:>5} {vol:>10}")


def main():
    q1_series()
    q2_settled_fields()
    q3_horizons()
    print("\n" + "=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())

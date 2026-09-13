"""Probe Polymarket's public API for 15-minute crypto up/down markets.

Read-only reconnaissance. Answers three questions before any logging is built:

  1. Do 15-minute crypto markets exist there at all?
  2. Which endpoint and fields carry the price and the spread?
  3. How do their quotes compare to Kalshi's for the same window?

Writes nothing. Prints what it finds so the logging can be built against the
real response shape rather than a guess -- the same approach that finally
cracked the Kalshi fetch.
"""

import json, requests
from datetime import datetime, timezone, timedelta

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
COINS = ("BTC", "BITCOIN", "ETH", "ETHEREUM", "SOL", "SOLANA",
         "XRP", "RIPPLE", "DOGE", "DOGECOIN")


def get(url, **params):
    try:
        r = requests.get(url, params=params or None, timeout=25)
        if not r.ok:
            print(f"    HTTP {r.status_code} {url} -- {r.text[:160]}")
            return None
        return r.json()
    except Exception as e:
        print(f"    error {url} -- {e}")
        return None


def looks_short_dated(m):
    """Ends within the next 2 hours -- the intraday markets we care about."""
    for key in ("endDate", "end_date_iso", "endDateIso"):
        v = m.get(key)
        if not v:
            continue
        try:
            end = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except Exception:
            continue
        # Gamma returns some dates without an offset; assume UTC for those
        # rather than letting the comparison raise.
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        mins = (end - datetime.now(timezone.utc)).total_seconds() / 60
        if -5 <= mins <= 120:
            return mins
    return None


def main():
    print("=" * 78)
    print("1. Can we reach the Gamma API at all?")
    probe = get(f"{GAMMA}/markets", closed="false", limit=3)
    if probe is None:
        print("   no -- stopping.")
        return
    sample = probe[0] if isinstance(probe, list) and probe else probe
    print(f"   yes. {len(probe) if isinstance(probe, list) else '?'} markets returned.")
    print(f"   fields on a market object:\n   {sorted(sample.keys())}\n")

    print("=" * 78)
    print("2. Are there short-dated crypto markets?")
    found, seen, fees = [], 0, []
    # Page through active markets ordered by volume; short-dated ones churn fast.
    for offset in range(0, 2000, 500):
        page = get(f"{GAMMA}/markets", closed="false", limit=500, offset=offset,
                   order="volumeNum", ascending="false")
        if not page:
            break
        seen += len(page)
        for m in page:
            if m.get("takerBaseFee") is not None:
                fees.append(m.get("takerBaseFee"))
            q = (m.get("question") or "").upper()
            slug = (m.get("slug") or "").upper()
            if not any(c in q or c in slug for c in COINS):
                continue
            mins = looks_short_dated(m)
            if mins is None:
                continue
            found.append((mins, m))
        if len(page) < 500:
            break
    print(f"   scanned {seen} open markets; {len(found)} crypto markets ending within 2h\n")
    if fees:
        from collections import Counter
        print(f"   takerBaseFee values seen across all scanned markets: "
              f"{dict(Counter(fees).most_common(6))}\n")

    found.sort(key=lambda x: x[0])
    for mins, m in found[:8]:
        print(f"   [{mins:+6.1f} min] {m.get('question')}")
        print(f"      slug        : {m.get('slug')}")
        for k in ("outcomes", "outcomePrices", "bestBid", "bestAsk", "spread",
                  "lastTradePrice", "liquidityNum", "volumeNum",
                  "takerBaseFee", "makerBaseFee", "feeType", "feesEnabled",
                  "orderMinSize", "orderPriceMinTickSize", "clobTokenIds"):
            if m.get(k) not in (None, ""):
                v = str(m.get(k))
                print(f"      {k:12}: {v[:110]}")
        print()

    if not found:
        print("   none found. Trying the events endpoint by slug pattern instead.")
        for pat in ("bitcoin-up-or-down", "btc-up-or-down", "ethereum-up-or-down"):
            ev = get(f"{GAMMA}/events", slug=pat)
            if ev:
                print(f"   events?slug={pat} -> {json.dumps(ev)[:400]}")
        return

    print("=" * 78)
    print("3. Live order book for the nearest one (CLOB midpoint / spread)")
    mins, m = found[0]
    raw = m.get("clobTokenIds")
    try:
        tokens = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        tokens = []
    if not tokens:
        print("   no clobTokenIds on that market.")
        return
    for t in tokens[:2]:
        mid = get(f"{CLOB}/midpoint", token_id=t)
        spr = get(f"{CLOB}/spread", token_id=t)
        print(f"   token {str(t)[:24]}...  midpoint={mid}  spread={spr}")


if __name__ == "__main__":
    main()

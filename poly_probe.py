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
    # Gamma caps a page at 100 regardless of what limit says, and ordering by
    # volume buries these: a 15-minute market lives 15 minutes and accumulates
    # almost none. Filter on end date instead and page properly.
    now = datetime.now(timezone.utc)
    lo = (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    hi = (now + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    found, seen, fees = [], 0, []
    attempts = [
        ("end_date filter", dict(closed="false", limit=100,
                                 end_date_min=lo, end_date_max=hi)),
        ("newest first",    dict(closed="false", limit=100,
                                 order="startDate", ascending="false")),
    ]
    for label, base in attempts:
        if found:
            break
        print(f"   trying: {label}")
        for offset in range(0, 1000, 100):
            page = get(f"{GAMMA}/markets", offset=offset, **base)
            if not page:
                break
            seen += len(page)
            for m in page:
                if m.get("takerBaseFee") is not None:
                    fees.append(m.get("takerBaseFee"))
                text = ((m.get("question") or "") + " " + (m.get("slug") or "")).upper()
                if not any(c in text for c in COINS):
                    continue
                mins = looks_short_dated(m)
                if mins is not None:
                    found.append((mins, m))
            if len(page) < 100:
                break
        print(f"     scanned {seen} so far, {len(found)} matches")

    print(f"\n   total scanned {seen}; {len(found)} crypto markets ending within 2h")
    if fees:
        from collections import Counter
        print(f"   takerBaseFee values: {dict(Counter(fees).most_common(6))}")
    print()

    if not found:
        print("   Still none. Searching by keyword instead:")
        for q in ("bitcoin up or down", "btc 15", "ethereum up or down", "bitcoin hourly"):
            res = get(f"{GAMMA}/public-search", q=q, limit_per_type=4) or \
                  get(f"{GAMMA}/markets", slug=q.replace(" ", "-"))
            if res:
                txt = json.dumps(res)[:500]
                print(f"     {q!r} -> {txt}")
            else:
                print(f"     {q!r} -> nothing")
        return

    found.sort(key=lambda x: x[0])
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

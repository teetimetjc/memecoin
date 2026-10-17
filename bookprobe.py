"""Why does the orderbook read return nothing?

shadow.orderbook() has returned None on all 56 attempts, so every depth,
spread and abort-bid field is blank and the abort measurement is dead. This
tries the plausible variants against a real open market and prints exactly
what each one returns -- status code, keys, a sample -- instead of guessing
which is right.

Read-only.
"""
import json
import requests
import predictor as P
import live
import collect15 as C


def try_path(path, **params):
    hdrs = P._kalshi_headers("GET", path)
    url = f"{live.ORDER_HOST}{path}"
    try:
        r = requests.get(url, headers=hdrs or {}, params=params or None,
                         timeout=10)
    except Exception as e:
        print(f"    EXC {str(e)[:90]}")
        return None
    print(f"    HTTP {r.status_code}  {len(r.content)} bytes")
    if not r.ok:
        print(f"      body: {r.text[:200]}")
        return None
    try:
        j = r.json()
    except Exception:
        print("      not json")
        return None
    print(f"      top-level keys: {list(j.keys())}")
    ob = j.get("orderbook")
    if ob is not None:
        print(f"      orderbook type {type(ob).__name__} "
              f"keys {list(ob.keys()) if isinstance(ob, dict) else 'n/a'}")
        print(f"      sample: {json.dumps(ob)[:300]}")
    return j


def main():
    d, _e = C.get("/trade-api/v2/markets", status="open", limit=200)
    mk = [m for m in ((d or {}).get("markets") or [])
          if str(m.get("ticker") or "").startswith(("KXBTC15M", "KXETH15M",
                                                    "KXSOL15M", "KXDOGE15M",
                                                    "KXXRP15M"))]
    if not mk:
        print("  no open 15m crypto market right now; using any open market")
        mk = (d or {}).get("markets") or []
    if not mk:
        print("  no open markets at all")
        return 0
    tk = str(mk[0].get("ticker"))
    print(f"  probing with ticker {tk}\n")

    print("  A) /trade-api/v2/markets/{ticker}/orderbook   (what shadow uses)")
    try_path(f"/trade-api/v2/markets/{tk}/orderbook")
    print("\n  B) same, with depth param")
    try_path(f"/trade-api/v2/markets/{tk}/orderbook", depth=10)
    print("\n  C) /trade-api/v2/markets/orderbook?ticker=")
    try_path("/trade-api/v2/markets/orderbook", ticker=tk)
    print("\n  D) the market object itself (what the bot already reads)")
    j = try_path(f"/trade-api/v2/markets/{tk}")
    if j:
        m = j.get("market") or {}
        keep = {k: v for k, v in m.items()
                if any(s in k for s in ("bid", "ask", "volume",
                                        "open_interest", "liquidity"))}
        print(f"      price/size fields: {json.dumps(keep)[:400]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

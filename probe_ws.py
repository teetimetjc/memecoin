"""Does Kalshi's market-data WebSocket require authentication?

WHY IT MATTERS. EdgeHunter reads its private key unconditionally at
startup -- `open(private_key_path).read()` runs before it connects to
anything -- so a missing file killed the market feed outright, which is why
it produced zero signals overnight rather than merely failing to settle.

probe_auth already showed the REST side is fully public: signed and
unsigned responses were byte-identical, and the order book comes back
complete to anyone. If the WebSocket is public too, EdgeHunter can be
patched to skip signing and run with NO credentials at all, which is
strictly better than protecting a key it never needed. If the socket does
demand auth, that is a real reason to wire the secret, and this says so.

WHAT IS MEASURED, and why a connection alone is not the answer. Kalshi can
accept the TCP upgrade and then refuse the subscription, so "connected"
proves nothing. This subscribes to orderbook_delta on live tickers and
waits for actual market messages. Only data arriving counts as working.

Read-only. Subscribes to public market data. Places nothing.
"""

import asyncio
import json
import sys

import requests

HOST = "https://api.elections.kalshi.com"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"
SERIES = "KXBTC15M"
WAIT_S = 20


def active_tickers(n=2):
    r = requests.get(f"{HOST}/trade-api/v2/markets",
                     params={"series_ticker": SERIES, "status": "open",
                             "limit": 10}, timeout=15)
    r.raise_for_status()
    return [m["ticker"] for m in (r.json().get("markets") or [])][:n]


async def attempt(label, headers, tickers):
    import websockets
    print(f"\n--- {label} ---")
    try:
        # websockets renamed this parameter; support both rather than pin.
        try:
            cm = websockets.connect(WS_URL, additional_headers=headers,
                                    open_timeout=15)
        except TypeError:
            cm = websockets.connect(WS_URL, extra_headers=headers,
                                    open_timeout=15)
        async with cm as ws:
            print("  handshake: ACCEPTED")
            await ws.send(json.dumps({
                "id": 1, "cmd": "subscribe",
                "params": {"channels": ["orderbook_delta"],
                           "market_tickers": tickers}}))
            got, kinds, err = 0, {}, None
            try:
                while got < 6:
                    raw = await asyncio.wait_for(ws.recv(), timeout=WAIT_S)
                    m = json.loads(raw)
                    t = m.get("type", "?")
                    kinds[t] = kinds.get(t, 0) + 1
                    got += 1
                    if got <= 3:
                        print(f"  msg: {json.dumps(m)[:170]}")
                    if t == "error":
                        err = m
                        break
            except asyncio.TimeoutError:
                print(f"  (no further messages within {WAIT_S}s)")
            print(f"  messages: {kinds or 'NONE'}")
            data = sum(v for k, v in kinds.items()
                       if k in ("orderbook_snapshot", "orderbook_delta"))
            if err:
                print(f"  VERDICT: subscription REFUSED -> {json.dumps(err)[:150]}")
                return False
            if data:
                print(f"  VERDICT: WORKS -- {data} real market message(s)")
                return True
            print("  VERDICT: connected but no market data arrived")
            return False
    except Exception as e:
        print(f"  handshake/stream FAILED: {type(e).__name__}: {str(e)[:150]}")
        return False


async def main():
    print("=" * 70)
    print("DOES KALSHI'S MARKET-DATA WEBSOCKET NEED CREDENTIALS?")
    print("=" * 70)
    tickers = active_tickers()
    print(f"\nlive tickers: {tickers}")
    if not tickers:
        print("no open markets to subscribe to")
        return 1

    import predictor as P
    unsigned = await attempt("NO credentials", {}, tickers)

    hdrs = P._kalshi_headers("GET", "/trade-api/ws/v2")
    if not hdrs:
        print("\n--- WITH credentials --- skipped: none in this job")
        signed = None
    else:
        signed = await attempt("WITH credentials", hdrs, tickers)

    print("\n" + "=" * 70)
    if unsigned:
        print("PUBLIC. EdgeHunter can be patched to skip signing and run with")
        print("no key at all -- better than securing a key it never needed.")
    elif signed:
        print("AUTH REQUIRED. The socket works only when signed, so wiring")
        print("the secret is the real fix rather than a convenience.")
    else:
        print("NEITHER worked. Do not conclude 'auth required' from this --")
        print("the fault is somewhere else and needs finding first.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

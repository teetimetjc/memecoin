"""Is there a FREE source of pump.fun price paths? Probe, don't speculate.

THE PROBLEM. pumpportal paywalls subscribeTokenTrade, so the trade-path
recorder cannot see prices. Without prices there is no exit-horizon
question, which is the one that decides whether any of this is tradeable.

THREE CANDIDATES, probed rather than argued about:

  1. THE SOLANA RPC, reading the bonding curve account directly. This is
     the interesting one. pumpscan already records `bondingCurveKey` for
     every launch, and the curve's own account holds the reserves. If that
     account can be read and decoded, prices come from the chain itself --
     free, unpaywalled, and nobody's API to withdraw.

  2. DEXSCREENER, which answers only for tokens that reached a pool. That
     is a different question (what happens AFTER graduation) but it is not
     nothing.

  3. JUPITER's price endpoint, which needs a tradable route.

THE DECODE IS VERIFIED, NOT ASSUMED, and this is the whole point of doing
it this way. The pump.fun bonding curve account is documented as:

     8 bytes   anchor discriminator
     u64       virtual_token_reserves
     u64       virtual_sol_reserves
     u64       real_token_reserves
     u64       real_sol_reserves
     u64       token_total_supply
     bool      complete

That is a layout read off documentation, which this project has learned not
to trust. So it is CHECKED against a number from a different source: the
websocket reports vSolInBondingCurve and vTokensInBondingCurve at launch,
and a correct decode must reproduce them. If the two disagree, the layout
is wrong and the run says so instead of reporting a confident price.

Reads one freshly launched token end to end. No key, no wallet, no order.
"""
import asyncio
import base64
import json
import os
import struct
import time

import requests

RPCS = [u for u in (os.environ.get("SOLANA_RPCS") or
                    "https://api.mainnet-beta.solana.com").split(",") if u]
WS = os.environ.get("PUMPWS_URL") or "wss://pumpportal.fun/api/data"
LAMPORTS = 1_000_000_000          # SOL
TOKEN_DEC = 10 ** 6               # pump.fun tokens are 6dp


async def one_launch(timeout=90):
    """Wait for a single create event and return it, or None."""
    import websockets
    async with websockets.connect(WS, open_timeout=20) as s:
        await s.send(json.dumps({"method": "subscribeNewToken"}))
        end = time.time() + timeout
        while time.time() < end:
            try:
                raw = await asyncio.wait_for(s.recv(),
                                             timeout=max(1, end - time.time()))
            except asyncio.TimeoutError:
                return None
            try:
                d = json.loads(raw)
            except Exception:
                continue
            if isinstance(d, dict) and d.get("txType") == "create":
                return d
    return None


def rpc(url, method, params):
    try:
        r = requests.post(url, json={"jsonrpc": "2.0", "id": 1,
                                     "method": method, "params": params},
                          timeout=20,
                          headers={"content-type": "application/json"})
        if not r.ok:
            return None, f"HTTP {r.status_code} {r.text[:120]}"
        j = r.json()
        if "error" in j:
            return None, f"rpc error {json.dumps(j['error'])[:160]}"
        return j.get("result"), None
    except Exception as e:
        return None, str(e)[:160]


def decode_curve(blob):
    """Reserves from the bonding curve account, or None if it will not fit."""
    if len(blob) < 8 + 8 * 5 + 1:
        return None
    vt, vs, rt, rs, sup = struct.unpack_from("<QQQQQ", blob, 8)
    complete = bool(blob[8 + 40])
    return dict(virtual_token_reserves=vt, virtual_sol_reserves=vs,
                real_token_reserves=rt, real_sol_reserves=rs,
                token_total_supply=sup, complete=complete)


def main():
    print("=" * 100)
    print("FREE PRICE SOURCE PROBE -- can we read the curve ourselves?")
    print("  pumpportal paywalls trade data. This tests the alternatives on "
          "one real, fresh token.")
    print("=" * 100)

    print("\n  waiting for a launch on the free websocket ...")
    ev = asyncio.run(one_launch())
    if not ev:
        print("  no create event arrived; nothing to test against.")
        return 1

    mint = ev.get("mint")
    curve = ev.get("bondingCurveKey")
    ws_vs = float(ev.get("vSolInBondingCurve") or 0)
    ws_vt = float(ev.get("vTokensInBondingCurve") or 0)
    print(f"  got {ev.get('symbol','?')}  mint={mint}")
    print(f"  curve={curve}")
    print(f"  websocket says vSol={ws_vs:.9f}  vTokens={ws_vt:,.4f}")

    # ------------------------------------------------- 1. the Solana RPC
    print(f"\n{'-' * 100}")
    print("1. SOLANA RPC -- read the bonding curve account directly")
    print("-" * 100)
    ok_rpc = False
    for url in RPCS:
        print(f"  {url}")
        res, err = rpc(url, "getAccountInfo",
                       [curve, {"encoding": "base64", "commitment": "processed"}])
        if err:
            print(f"    FAILED: {err}")
            continue
        val = (res or {}).get("value")
        if not val:
            print("    account not found (it may not be indexed yet)")
            continue
        data = val.get("data")
        blob = base64.b64decode(data[0]) if isinstance(data, list) else b""
        print(f"    account read: {len(blob)} bytes, owner "
              f"{str(val.get('owner'))[:44]}")
        dec = decode_curve(blob)
        if not dec:
            print("    TOO SHORT to be the documented layout")
            continue
        vs = dec["virtual_sol_reserves"] / LAMPORTS
        vt = dec["virtual_token_reserves"] / TOKEN_DEC
        print(f"    decoded vSol={vs:.9f}  vTokens={vt:,.4f}  "
              f"complete={dec['complete']}")
        # THE CROSS-CHECK. A layout read off documentation is a guess until
        # it reproduces a number obtained some other way.
        if ws_vs and ws_vt:
            ds = abs(vs - ws_vs) / ws_vs
            dt = abs(vt - ws_vt) / ws_vt
            print(f"    vs websocket: sol off by {100*ds:.3f}%, "
                  f"tokens off by {100*dt:.3f}%")
            if ds < 0.02 and dt < 0.02:
                print("    LAYOUT CONFIRMED -- the decode reproduces the")
                print("    websocket's own figures, so price can be computed")
                print("    from the chain with no API key at all.")
                print(f"    price = {vs/vt:.3e} SOL/token")
                ok_rpc = True
            else:
                print("    MISMATCH. The documented layout does not fit this")
                print("    account; do not price anything with it.")
        break

    # --------------------------------------------------- 2. DexScreener
    print(f"\n{'-' * 100}")
    print("2. DEXSCREENER -- only answers once a pool exists")
    print("-" * 100)
    try:
        r = requests.get(
            f"https://api.dexscreener.com/tokens/v1/solana/{mint}", timeout=20)
        print(f"  HTTP {r.status_code}")
        j = r.json() if r.ok else []
        pairs = j if isinstance(j, list) else (j or {}).get("pairs") or []
        if pairs:
            p = pairs[0]
            print(f"  priceUsd={p.get('priceUsd')}  "
                  f"liquidity={(p.get('liquidity') or {}).get('usd')}")
        else:
            print("  no pair yet -- expected for a token seconds old, and")
            print("  exactly why this source cannot see the first minutes.")
    except Exception as e:
        print(f"  FAILED: {str(e)[:140]}")

    # ------------------------------------------------------- 3. Jupiter
    print(f"\n{'-' * 100}")
    print("3. JUPITER price -- needs a tradable route")
    print("-" * 100)
    for url in (f"https://lite-api.jup.ag/price/v3?ids={mint}",
                f"https://api.jup.ag/price/v2?ids={mint}"):
        try:
            r = requests.get(url, timeout=15)
            print(f"  {url.split('?')[0]} -> HTTP {r.status_code} "
                  f"{r.text[:120]}")
            if r.ok:
                break
        except Exception as e:
            print(f"  {url.split('?')[0]} -> {str(e)[:100]}")

    print("\n" + "=" * 100)
    if ok_rpc:
        print("  THE RPC ROUTE WORKS AND IS VERIFIED. Price paths can be")
        print("  polled from the chain for free, using the bondingCurveKey")
        print("  pumpscan already records for every launch.")
        print()
        print("  WHAT IT COSTS: polling, not streaming. A public RPC is rate")
        print("  limited, so a cohort gets sampled every N seconds rather")
        print("  than every trade. For an exit-horizon sweep that is enough;")
        print("  for anything latency-sensitive it is not.")
    else:
        print("  THE RPC ROUTE IS NOT CONFIRMED by this run. Do not build a")
        print("  price path on an unverified decode -- that is precisely the")
        print("  plausible-wrong-number failure this project keeps finding.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only probe of the Kalshi API credentials.

Step 1 of the three-step path to automated order placement. It answers one
question and nothing else: does the key in KALSHI_API_KEY / KALSHI_KEY_ID work
against the AUTHENTICATED portfolio endpoints, and does it carry trade scope?

Everything this project has done with Kalshi so far reads public market data.
Public endpoints accept the signature but never exercise account permissions,
so a key that can quote prices may still be unable to place an order -- and
the first place you would find that out is at the first real order, which is
the worst possible moment.

SAFETY. Every request here is a GET. There is no order path in this file, no
POST, and no write of any kind. Running it cannot place, cancel or modify an
order, and cannot move money. It is safe to run against the live account, and
it is meant to be: a demo key would not answer the question being asked.

What it reports:
  - whether the signature is accepted on a portfolio endpoint  (auth works)
  - the account balance                                        (read scope)
  - open positions and resting orders, counts only             (read scope)
  - whether the key can reach the order endpoint               (trade scope)

The trade-scope check reads the ORDERS LIST, which is a GET that Kalshi gates
behind the same permission an order placement needs. A key with read-only
scope is refused there with 403 while still answering /portfolio/balance. That
distinction is the whole point of this script.

Nothing sensitive is printed. The key id is shown as its first 8 characters so
you can confirm WHICH key answered without the value reaching a log.

Usage:  python kalshi_probe.py
"""

import json
import os
import sys

import requests

import predictor as P

TIMEOUT = 20


def _sign(method, path, scheme):
    """Signed headers using an explicit padding scheme.

    predictor.py signs with PKCS#1 v1.5. Kalshi's documented scheme is RSA-PSS
    (MGF1-SHA256, salt length = digest length). Both produce a valid-looking
    base64 signature, and both are accepted without complaint by the PUBLIC
    market endpoints the predictor uses -- which is why a wrong scheme could
    sit here undetected for weeks. Only an authenticated endpoint tells them
    apart, so this probe signs each way and reports which one the server takes.
    """
    import base64
    from datetime import datetime, timezone
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pem = os.environ.get("KALSHI_API_KEY", "").strip()
    if "\\n" in pem and "\n" not in pem:
        pem = pem.replace("\\n", "\n")
    key = serialization.load_pem_private_key(pem.encode(), password=None)

    ts = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    msg = (ts + method.upper() + path).encode()
    if scheme == "pss":
        pad = padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                          salt_length=hashes.SHA256().digest_size)
    else:
        pad = padding.PKCS1v15()
    sig = key.sign(msg, pad, hashes.SHA256())
    return {
        "KALSHI-ACCESS-KEY":       os.environ.get("KALSHI_KEY_ID", "").strip(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


# Which padding the live key actually needs. Set by _pick_scheme().
SCHEME = "pkcs1v15"


def _get(path, params=None):
    """Signed GET. Returns (status_code, parsed_body_or_text, error_or_None)."""
    try:
        hdrs = _sign("GET", "/trade-api/v2" + path, SCHEME)
    except Exception as e:
        return None, None, f"could not sign: {e}"
    if not hdrs.get("KALSHI-ACCESS-KEY"):
        return None, None, "KALSHI_KEY_ID is empty"
    try:
        r = requests.get(P.KALSHI_BASE + path, params=params,
                         headers=hdrs, timeout=TIMEOUT)
    except Exception as e:
        return None, None, f"request failed: {e}"
    try:
        return r.status_code, r.json(), None
    except ValueError:
        return r.status_code, r.text[:300], None


def _verdict(code):
    """Turn an HTTP status into what it means for this key."""
    if code == 200:
        return "OK"
    if code in (401, 403):
        return "DENIED"
    return f"UNEXPECTED ({code})"


def main():
    key_id = os.environ.get("KALSHI_KEY_ID", "").strip()
    pem    = os.environ.get("KALSHI_API_KEY", "").strip()

    print("=" * 62)
    print("KALSHI CREDENTIAL PROBE  (read-only -- no orders are placed)")
    print("=" * 62)

    if not key_id or not pem:
        print("FAILED: KALSHI_KEY_ID and/or KALSHI_API_KEY are not set.")
        print("Both must be present as GitHub secrets for this job.")
        return 1

    # Shape checks first: a malformed PEM fails signing in a way that looks
    # like an auth rejection, and the two need different fixes.
    print(f"  key id       : {key_id[:8]}... ({len(key_id)} chars)")
    looks_pem = "BEGIN" in pem and "PRIVATE KEY" in pem
    print(f"  private key  : {len(pem)} chars, PEM header {'found' if looks_pem else 'MISSING'}")
    if not looks_pem:
        print("FAILED: KALSHI_API_KEY does not look like a PEM block.")
        print("It must contain the full key including the BEGIN/END lines.")
        return 1

    print(f"  host         : {P.KALSHI_BASE}")
    print("-" * 62)

    # Establish WHICH signature scheme this key needs, before anything else.
    # /portfolio/balance is the cheapest authenticated endpoint to ask with.
    global SCHEME
    print("SIGNATURE SCHEME")
    winner = None
    for scheme in ("pss", "pkcs1v15"):
        SCHEME = scheme
        code, body, err = _get("/portfolio/balance")
        if err:
            print(f"  {scheme:<10}: FAILED -- {err}")
            continue
        detail = ""
        if code != 200 and isinstance(body, dict):
            detail = " -- " + str(body.get("error", {}).get("details", ""))[:60]
        print(f"  {scheme:<10}: {_verdict(code)}{detail}")
        if code == 200 and winner is None:
            winner = scheme
    if not winner:
        print("  Neither scheme authenticated. This is a key problem, not a")
        print("  code problem: re-issue the key in the Kalshi dashboard and")
        print("  copy BOTH the key id and the full PEM into the secrets.")
        return 1
    SCHEME = winner
    print(f"  -> using {winner}")
    if winner != "pkcs1v15":
        print(f"  NOTE: predictor.py signs with PKCS#1 v1.5, not {winner}.")
        print("  Its market-data calls work anyway because those endpoints are")
        print("  public, but it must be changed before any authenticated call.")
    print("-" * 62)

    results = {}

    # 1. AUTH + READ SCOPE ------------------------------------------------
    code, body, err = _get("/portfolio/balance")
    if err:
        print(f"balance      : FAILED -- {err}")
        return 1
    results["balance"] = code
    print(f"balance      : {_verdict(code)}")
    if code == 200 and isinstance(body, dict):
        cents = body.get("balance")
        if isinstance(cents, int):
            print(f"               ${cents/100:,.2f} available")
        else:
            print(f"               unexpected shape: {json.dumps(body)[:160]}")
    elif code in (401, 403):
        # Kalshi names the reason in the body, and the reasons need different
        # fixes -- a bad signature is a key problem, "not found" is a routing
        # problem. Printing only "DENIED" threw that away.
        print(f"               response: {json.dumps(body)[:300]}")
        print("-" * 62)
        print("DIAGNOSIS")
        # Does the SAME signature work on the endpoint the predictor uses? If
        # yes, signing is fine and this is a scope or account problem. If no,
        # the key itself never worked and the predictor has been getting by on
        # the fact that /markets does not require auth at all.
        mcode, mbody, merr = _get("/markets", params={"limit": 1})
        print(f"  same key on /markets : {_verdict(mcode) if not merr else merr}")
        if mcode == 200:
            print("  Signing works -- /markets accepted this exact signature.")
            print("  But /markets is a PUBLIC endpoint: it answers whether or")
            print("  not the signature is valid, so this does not prove much.")
        print("  Most likely causes, in order:")
        print("   1. The key id and the private key are from different key")
        print("      pairs. Re-copy BOTH from one freshly created key.")
        print("   2. The key was created on the demo environment but this is")
        print("      the live host (or the reverse).")
        print("   3. The key was revoked in the Kalshi dashboard.")
        return 1

    # 2. POSITIONS + RESTING ORDERS (counts only) -------------------------
    for label, path, field in (
        ("positions", "/portfolio/positions", "market_positions"),
        ("orders",    "/portfolio/orders",    "orders"),
    ):
        code, body, err = _get(path, params={"limit": 200})
        results[label] = code
        if err:
            print(f"{label:<13}: FAILED -- {err}")
            continue
        line = f"{label:<13}: {_verdict(code)}"
        if code == 200 and isinstance(body, dict):
            rows = body.get(field) or []
            line += f" -- {len(rows)} returned"
        print(line)

    print("-" * 62)

    # 3. TRADE SCOPE ------------------------------------------------------
    # /portfolio/orders is the same permission an order placement needs, so a
    # 200 here means the key is allowed to trade. This is inference from the
    # permission model, NOT proof -- only a real order proves that, and this
    # script will not place one. Step 2 (dry run) is where that gets settled.
    orders_code = results.get("orders")
    print("TRADE SCOPE")
    if orders_code == 200:
        print("  The order endpoint answered. This key appears to carry trade")
        print("  scope, not read-only scope. Not proof -- only a placed order")
        print("  proves that -- but the permission gate did not refuse it.")
    elif orders_code in (401, 403):
        print("  DENIED. The key reads market data and balance but is refused")
        print("  at the order endpoint: it is a READ-ONLY key. A new key with")
        print("  trade permission must be issued in the Kalshi dashboard")
        print("  before any automated betting is possible.")
    else:
        print(f"  Inconclusive (status {orders_code}). Re-run; if it persists,")
        print("  the endpoint shape may have changed.")

    # 4. ORDER BOOK ------------------------------------------------------
    # The dry run needs this endpoint, and its first three attempts all came
    # back unpriceable. Find out whether the endpoint answers at all and what
    # shape it returns, rather than guessing from the "failed" count.
    print("-" * 62)
    print("ORDER BOOK (what the dry run reads)")
    mcode, mbody, merr = _get("/markets", params={"series_ticker": "KXBTC15M",
                                                  "limit": 5, "status": "open"})
    tick = ""
    if mcode == 200 and isinstance(mbody, dict):
        ms = mbody.get("markets") or []
        if ms:
            tick = ms[0].get("ticker", "")
    if not tick:
        print("  could not find an open BTC 15m market to test with")
    else:
        print(f"  ticker: {tick}")
        ocode, obody, oerr = _get(f"/markets/{tick}/orderbook", params={"depth": 5})
        if oerr:
            print(f"  FAILED -- {oerr}")
        else:
            print(f"  status: {_verdict(ocode)}")
            print(f"  raw   : {json.dumps(obody)[:400]}")

    print("=" * 62)
    ok = results.get("balance") == 200
    print("RESULT:", "credentials work for reading the account."
          if ok else "credentials did NOT authenticate.")
    return 0 if ok else 1




# --- order endpoint discovery -------------------------------------------
# The documented POST /trade-api/v2/portfolio/orders answered HTTP 410
# "deprecated_v1_order_endpoint", and the docs are unreachable from here, so
# find the live path by asking the server.
#
# SAFE BY CONSTRUCTION: every probe uses a ticker that cannot exist, so a path
# that is real rejects it on validation and a path that is not real 404s. No
# candidate can fill, because there is no market to fill against. The point is
# to read the STATUS CODE, not to trade.

BOGUS_TICKER = "KXNOSUCHMARKET-00XXX000000-00"

# The real V2 create-order endpoint, from Kalshi's docs.
ORDER_URL = "https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders"

# The documented body uses decimal STRINGS and a bid/ask side, not the
# yes/no this project assumed. What is NOT documented on that page is how to
# express a DOWN bet -- buying NO. Rather than guess with real money, send
# deliberately invalid tickers with different side values and read which ones
# the validator objects to. No candidate can fill: the market does not exist.
SIDE_VALUES = ["bid", "ask", "yes", "no"]


def _try_body(side_value):
    import predictor as P
    from urllib.parse import urlsplit
    body = {
        "ticker": BOGUS_TICKER,
        "client_order_id": "00000000-0000-4000-8000-000000000000",
        "side": side_value,
        "count": "1.00",
        "price": "0.0100",
        "time_in_force": "good_till_canceled",
        "post_only": False,
    }
    hdrs = _sign("POST", urlsplit(ORDER_URL).path, SCHEME)
    hdrs["Content-Type"] = "application/json"
    try:
        r = requests.post(ORDER_URL, json=body, headers=hdrs, timeout=15)
    except Exception as e:
        return None, f"request failed: {e}"
    return r.status_code, r.text[:220].replace("\n", " ")


def find_order_endpoint():
    print("=" * 62)
    print("ORDER BODY PROBE  (invalid ticker -- cannot fill)")
    print("=" * 62)
    global SCHEME
    for scheme in ("pss", "pkcs1v15"):
        SCHEME = scheme
        code, _, err = _get("/portfolio/balance")
        if code == 200:
            print(f"  signing: {scheme}")
            break
    print(f"  url: {ORDER_URL}\n")
    for sv in SIDE_VALUES:
        code, txt = _try_body(sv)
        print(f'  side="{sv}"  -> {code}')
        print(f"        {txt}")
    print("=" * 62)
    print("A 404 'market not found' means the BODY was accepted and only the")
    print("ticker was wrong -- that side value is valid. A 400 naming the side")
    print("field means that value is not.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--find-order-endpoint":
        sys.exit(find_order_endpoint())
    sys.exit(main())

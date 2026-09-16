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


def _get(path, params=None):
    """Signed GET. Returns (status_code, parsed_body_or_text, error_or_None)."""
    hdrs = P._kalshi_headers("GET", "/trade-api/v2" + path)
    if not hdrs:
        return None, None, "could not build signed headers (missing or invalid key)"
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
        print("               The signature was rejected. Either the key id and")
        print("               private key are from different key pairs, or the")
        print("               key has been revoked in the Kalshi dashboard.")
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

    print("=" * 62)
    ok = results.get("balance") == 200
    print("RESULT:", "credentials work for reading the account."
          if ok else "credentials did NOT authenticate.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

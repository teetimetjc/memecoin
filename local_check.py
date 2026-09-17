"""Run this on YOUR computer. It answers whether the block is location or account.

From a GitHub Actions runner, every order is refused with market_not_found for
a market the same host reports as active with a real book. Two explanations
remain and they cannot be told apart from there:

  LOCATION  Kalshi blocks trading from where the runner sits. One probe there
            returned "Trading is not currently allowed in Washington", and
            GitHub's runners are Azure machines, many of them in Washington.
            An exchange hiding a market you may not trade will often say the
            market does not exist rather than say you are blocked.

  ACCOUNT   The API key lacks trade permission, or the account is not enabled
            for trading. Nothing about the request would change that.

Running the identical request from your own network separates them:
  it works here          -> LOCATION. Run the bot from home or a VPS, not Actions.
  it fails the same way  -> ACCOUNT. A Kalshi support question, not a code change.

SETUP -- do not paste the key into a chat window.
  1. Put your Kalshi private key PEM in a file, e.g. ~/kalshi_key.pem
  2. export KALSHI_KEY_ID="your-key-id-uuid"
     export KALSHI_KEY_FILE=~/kalshi_key.pem
  3. pip install requests cryptography
  4. python local_check.py

It places at most ONE order for 0.01 contracts -- under a cent -- and prints
what came back. Nothing else.
"""

import os
import sys
import time
import uuid

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

HOST = "https://external-api.kalshi.com"
PRICES = "https://api.elections.kalshi.com"
ORDER_PATH = "/trade-api/v2/portfolio/events/orders"


def sign(method, path):
    key_id = os.environ.get("KALSHI_KEY_ID", "").strip()
    key_file = os.path.expanduser(os.environ.get("KALSHI_KEY_FILE", "").strip())
    if not key_id or not key_file:
        sys.exit("Set KALSHI_KEY_ID and KALSHI_KEY_FILE first (see the top of this file).")
    if not os.path.exists(key_file):
        sys.exit(f"No key file at {key_file}")
    with open(key_file, "rb") as fh:
        pk = serialization.load_pem_private_key(fh.read(), password=None)
    ts = str(int(time.time() * 1000))
    sig = pk.sign(
        (ts + method + path).encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=hashes.SHA256().digest_size),
        hashes.SHA256(),
    )
    import base64
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
    }


def main():
    print("=" * 62)
    print("LOCAL ORDER CHECK -- is the block location, or the account?")
    print("=" * 62)

    h = sign("GET", "/trade-api/v2/portfolio/balance")
    r = requests.get(HOST + "/trade-api/v2/portfolio/balance", headers=h, timeout=15)
    print(f"  balance      -> {r.status_code} {r.text[:120]}")
    if r.status_code != 200:
        print("  The key is not authenticating. Fix that before reading anything else.")
        return 1

    h = sign("GET", "/trade-api/v2/markets")
    r = requests.get(PRICES + "/trade-api/v2/markets",
                     params={"series_ticker": "KXBTC15M", "limit": 1, "status": "open"},
                     headers=h, timeout=15)
    ms = r.json().get("markets", []) if r.ok else []
    if not ms:
        print("  No open BTC 15-minute market right now. Try again shortly.")
        return 1
    m = ms[0]
    tk = m["ticker"]
    print(f"  market       -> {tk}  status={m.get('status')} "
          f"bid={m.get('yes_bid_dollars')} ask={m.get('yes_ask_dollars')}")

    h = sign("GET", f"/trade-api/v2/markets/{tk}")
    g = requests.get(f"{HOST}/trade-api/v2/markets/{tk}", headers=h, timeout=15)
    print(f"  GET on order host -> {g.status_code}   (200 means the market is visible)")

    try:
        price = min(0.99, max(0.01, round(float(m.get("yes_ask_dollars")), 2)))
    except (TypeError, ValueError):
        price = 0.50

    body = {
        "ticker": tk,
        "client_order_id": str(uuid.uuid4()),
        "side": "bid",
        "count": "0.01",
        "price": f"{price:.4f}",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False, "cancel_order_on_pause": False,
        "reduce_only": False, "subaccount": 0, "exchange_index": 0,
    }
    h = sign("POST", ORDER_PATH)
    h["Content-Type"] = "application/json"
    print(f"  ORDER 0.01 contracts at {price:.2f}  (~${0.01 * price:.4f})")
    rr = requests.post(HOST + ORDER_PATH, json=body, headers=h, timeout=15)
    print()
    print(f"  -> HTTP {rr.status_code}")
    print(f"  -> {rr.text[:400]}")
    print()

    if rr.status_code in (200, 201):
        print("  IT WORKS FROM HERE.")
        print("  The block is LOCATION: GitHub's runners are where it fails.")
        print("  Run the bot from this machine or a VPS in a permitted state.")
    elif rr.status_code == 404:
        print("  Same market_not_found as the runner gets.")
        print("  So it is the ACCOUNT, not the location -- the key may lack trade")
        print("  permission, or the account is not enabled for these markets.")
        print("  That is a Kalshi support question; no code change fixes it.")
    elif rr.status_code == 403:
        print("  Explicitly blocked, and it names the reason above.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())

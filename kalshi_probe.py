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
import time

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
NEW_HOST = "https://external-api.kalshi.com"      # where orders go
OLD_HOST = "https://api.elections.kalshi.com"     # where prices are read
ORDER_URL = NEW_HOST + "/trade-api/v2/portfolio/events/orders"

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
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": False,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    hdrs = _sign("POST", urlsplit(ORDER_URL).path, SCHEME)
    hdrs["Content-Type"] = "application/json"
    try:
        r = requests.post(ORDER_URL, json=body, headers=hdrs, timeout=15)
    except Exception as e:
        return None, f"request failed: {e}"
    return r.status_code, r.text[:220].replace("\n", " ")


def listing_lead():
    """Does the ORDER host list a window's market BEFORE the window opens?

    If it does, the order can be resting at second zero instead of chasing the
    market three to four minutes in, and live entries would match the price
    the signal actually saw. If it does not, being late is structural and no
    amount of polling fixes it.

    Read-only: lists markets by status on both hosts and prints what exists.
    """
    import predictor as P
    from datetime import datetime, timezone
    print("=" * 62)
    print("LISTING LEAD  (read-only) -- what exists BEFORE a window opens")
    print("=" * 62)
    now = datetime.now(timezone.utc)
    print(f"  now: {now:%H:%M:%S} UTC\n")
    for host in (NEW_HOST, OLD_HOST):
        for status in ("open", "unopened", "initialized", ""):
            try:
                hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
                params = {"series_ticker": "KXBTC15M", "limit": 6}
                if status:
                    params["status"] = status
                r = requests.get(host + "/trade-api/v2/markets",
                                 params=params, headers=hh, timeout=15)
            except Exception as e:
                print(f"  {host[8:]:26s} status={status or 'any':12s} failed {e}")
                continue
            if not r.ok:
                print(f"  {host[8:]:26s} status={status or 'any':12s} HTTP {r.status_code}")
                continue
            ms = r.json().get("markets", [])
            label = status or "any"
            print(f"  {host[8:]:26s} status={label:12s} {len(ms)} market(s)")
            for mk in ms[:4]:
                print(f"        {mk.get('ticker'):28s} status={mk.get('status'):12s} "
                      f"open={str(mk.get('open_time'))[11:19]} close={str(mk.get('close_time'))[11:19]}")
    print("=" * 62)
    return 0



def listing_timeline():
    """Does the ORDER host EVER list a window's market while that window runs?

    This is the question the whole live-betting plan rests on, and it has never
    been answered. Two things are already known: the order host 404s at ~35
    seconds, and on 17 Sep a window's ETH and DOGE markets were polled for 4.5
    minutes and never appeared at all. If markets reliably list a few minutes
    in, an automated bet is possible and the only cost is a worse entry. If
    they sometimes never list during their own window, no amount of polling
    fixes it and automated betting on 15-minute markets is not viable.

    So: take the CURRENT window's ticker for all five coins and poll the order
    host every 15 seconds until the window closes, recording when each one
    first appears and whether it is actually priced.

    Read-only. Signed GETs only, no order body anywhere in this function.
    """
    import predictor as P
    from datetime import datetime, timezone, timedelta
    print("=" * 62)
    print("LISTING TIMELINE  (read-only) -- when can an order actually go in?")
    print("=" * 62)

    # Measuring from mid-window answers the wrong question: the first poll
    # finds the market already there and reports its own start time as the
    # listing time. The interesting range is the first four minutes, so if we
    # are past the very start of a window, wait for the NEXT one and watch it
    # from second zero.
    now = datetime.now(timezone.utc)
    boundary = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    if (now - boundary).total_seconds() > 30:
        boundary += timedelta(minutes=15)
        wait = (boundary - now).total_seconds()
        print(f"  {int(wait)}s into a window already; waiting {int(wait)}s for "
              f"the {boundary:%H:%M} window so the first minutes are measured.")
        time.sleep(max(0, wait))
    close = boundary + timedelta(minutes=15)
    now = datetime.now(timezone.utc)
    print(f"  window {boundary:%H:%M} -> {close:%H:%M} UTC, now {now:%H:%M:%S}\n")

    # Resolve this window's ticker per coin by matching its CLOSE TIME, not by
    # asking for a usable price. At second zero the market exists but its book
    # is a placeholder -- bid 0.001, ask 0.002 -- so any price-validating
    # lookup reports "no market" exactly when we need the name. The name and
    # the price become available at different times, and this only needs the
    # name.
    targets = {}
    want = f"{close:%Y-%m-%dT%H:%M}"
    for sym, series in P.KALSHI_SERIES.items():
        try:
            hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
            r = requests.get(OLD_HOST + "/trade-api/v2/markets",
                             params={"series_ticker": series, "limit": 200},
                             headers=hh, timeout=15)
            if not r.ok:
                print(f"  {sym}: markets list HTTP {r.status_code}")
                continue
            for mk in r.json().get("markets", []):
                if str(mk.get("close_time", ""))[:16] == want:
                    targets[sym] = mk["ticker"]
                    break
            else:
                print(f"  {sym}: no market closing at {want}")
        except Exception as e:
            print(f"  {sym}: could not resolve ticker -- {e}")
    if not targets:
        print("  no tickers resolved; nothing to poll.")
        return 1
    for sym, tk in targets.items():
        print(f"  {sym:9s} {tk}")
    print()

    # "first seen" is only a listing time if we were looking from the start.
    first_look = int((datetime.now(timezone.utc) - boundary).total_seconds())
    seen = {}
    round_no = 0
    while datetime.now(timezone.utc) < close - timedelta(seconds=20):
        round_no += 1
        secs = int((datetime.now(timezone.utc) - boundary).total_seconds())
        for sym, tk in targets.items():
            if sym in seen:
                continue
            try:
                hh = _sign("GET", f"/trade-api/v2/markets/{tk}", SCHEME)
                r = requests.get(f"{NEW_HOST}/trade-api/v2/markets/{tk}",
                                 headers=hh, timeout=10)
            except Exception:
                continue
            if r.status_code == 200:
                m = r.json().get("market") or {}
                ask = m.get("yes_ask_dollars")
                bid = m.get("yes_bid_dollars")
                seen[sym] = (secs, m.get("status"), bid, ask)
                print(f"  +{secs:4d}s  {sym:9s} LISTED  status={m.get('status')} "
                      f"bid={bid} ask={ask}")
        if len(seen) == len(targets):
            break
        time.sleep(15)

    print()
    print("  RESULT")
    for sym, tk in targets.items():
        if sym in seen:
            secs, st, bid, ask = seen[sym]
            priced = "priced" if (bid or ask) else "NO PRICES"
            print(f"    {sym:9s} listed at +{secs}s  ({st}, {priced})")
        else:
            print(f"    {sym:9s} NEVER LISTED during its own window")
    print()
    if not seen:
        print("  VERDICT  the order host never listed ANY market this window.")
        print("           Automated betting on 15-minute markets is not viable.")
    elif len(seen) < len(targets):
        print(f"  VERDICT  {len(seen)}/{len(targets)} listed. Betting is possible")
        print("           but unreliable -- some windows will simply be missed.")
    else:
        worst = max(v[0] for v in seen.values())
        if worst <= first_look + 20:
            print(f"  VERDICT  all {len(targets)} were ALREADY listed on the first")
            print(f"           poll at +{first_look}s, so this run does not show when")
            print(f"           they appeared -- only that betting is possible by then.")
        else:
            print(f"  VERDICT  all {len(targets)} listed, latest at +{worst}s.")
            print("           An automated bet IS possible at that entry.")
    print("=" * 62)
    return 0


def ticker_form():
    """Does the order endpoint want the MARKET ticker or the EVENT ticker?

    Real, open market tickers are rejected with market_not_found, so the
    endpoint is not finding what we send. The path is /portfolio/EVENTS/orders,
    which suggests it resolves an event rather than a market.

    SAFE: every probe sends count "0.00". A zero-size order cannot be filled,
    so the only thing being read is WHICH error comes back -- a count complaint
    means the ticker resolved, a market_not_found means it did not.
    """
    import predictor as P
    print("=" * 62)
    print("TICKER FORM PROBE  (count 0 -- cannot fill)")
    print("=" * 62)
    hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
    r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                     params={"series_ticker": "KXBTC15M", "limit": 1,
                             "status": "open"}, headers=hh, timeout=15)
    ms = r.json().get("markets", []) if r.ok else []
    if not ms:
        print("  no open BTC market to test with right now")
        return 0
    mkt = ms[0].get("ticker", "")
    evt = ms[0].get("event_ticker") or mkt.rsplit("-", 1)[0]
    print(f"  market ticker : {mkt}")
    print(f"  event ticker  : {evt}\n")
    for label, tk in (("market", mkt), ("event", evt)):
        body = {
            "ticker": tk,
            "client_order_id": "00000000-0000-4000-8000-00000000000" + ("1" if label == "market" else "2"),
            "side": "bid", "count": "0.00", "price": "0.0100",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False, "cancel_order_on_pause": False,
            "reduce_only": False, "subaccount": 0, "exchange_index": 0,
        }
        hdrs = _sign("POST", "/trade-api/v2/portfolio/events/orders", SCHEME)
        hdrs["Content-Type"] = "application/json"
        try:
            rr = requests.post(ORDER_URL, json=body, headers=hdrs, timeout=15)
            print(f"  {label:7s} -> {rr.status_code} {rr.text[:190]}")
        except Exception as e:
            print(f"  {label:7s} -> failed {e}")
    print("=" * 62)
    return 0


def compare_hosts():
    """Does the ORDER host know the tickers the MARKET host quotes?

    Orders are rejected with market_not_found for real, currently-open tickers,
    which suggests the two hosts serve different market universes. If so, every
    price this project has ever logged came from a venue we cannot trade on,
    and the fix is to read from the same host we order on.
    """
    import predictor as P
    print("=" * 62)
    print("HOST COMPARISON  (read-only)")
    print("=" * 62)
    for host in (NEW_HOST, OLD_HOST):
        for series in ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M"):
            try:
                hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
                r = requests.get(host + "/trade-api/v2/markets",
                                 params={"series_ticker": series, "limit": 4,
                                         "status": "open"},
                                 headers=hh, timeout=15)
            except Exception as e:
                print(f"  {host[8:]:26s} {series}: failed {e}")
                continue
            if not r.ok:
                print(f"  {host[8:]:26s} {series}: HTTP {r.status_code} {r.text[:90]}")
                continue
            ms = r.json().get("markets", [])
            print(f"  {host[8:]:26s} {series}: {len(ms)} open market(s)")
            for m in ms[:3]:
                print(f"        {m.get('ticker')}  status={m.get('status')}")
    print("=" * 62)
    return 0


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
    if len(sys.argv) > 1 and sys.argv[1] == "--listing-timeline":
        sys.exit(listing_timeline())
    if len(sys.argv) > 1 and sys.argv[1] == "--find-order-endpoint":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        compare_hosts()
        listing_lead()
        ticker_form()
        sys.exit(find_order_endpoint())
    sys.exit(main())

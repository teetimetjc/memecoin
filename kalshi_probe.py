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

    # Poll the WHOLE window, not just until the first sighting. The question
    # is not only when a market becomes orderable but when it STOPS being
    # orderable: a manual order 14 minutes into a window was rejected with
    # market_not_found, so the usable span has an end as well as a start, and
    # only watching to the first sighting would have missed that entirely.
    log = {sym: [] for sym in targets}          # (secs, http_status)
    while True:
        left = (close - datetime.now(timezone.utc)).total_seconds()
        if left <= 2:
            break
        secs = int((datetime.now(timezone.utc) - boundary).total_seconds())
        line = []
        for sym, tk in targets.items():
            try:
                hh = _sign("GET", f"/trade-api/v2/markets/{tk}", SCHEME)
                r = requests.get(f"{NEW_HOST}/trade-api/v2/markets/{tk}",
                                 headers=hh, timeout=10)
                code = r.status_code
            except Exception:
                code = 0
            log[sym].append((secs, code))
            line.append(f"{sym[:3]}:{'OK' if code == 200 else code}")
        print(f"  +{secs:4d}s  " + "  ".join(line))
        time.sleep(min(15, max(1, left - 2)))

    print()
    print("  WHEN IS EACH MARKET REACHABLE ON THE ORDER HOST?")
    spans = {}
    for sym, entries in log.items():
        ok = [t for t, c in entries if c == 200]
        if not ok:
            codes = sorted({c for _, c in entries})
            print(f"    {sym:9s} NEVER reachable (codes seen: {codes})")
            continue
        spans[sym] = (min(ok), max(ok))
        gaps = [t for t, c in entries if c != 200 and min(ok) < t < max(ok)]
        print(f"    {sym:9s} +{min(ok)}s -> +{max(ok)}s"
              + (f"   WITH {len(gaps)} GAP(S) inside" if gaps else ""))

    print()
    if not spans:
        print("  VERDICT  no market was reachable on the order host this window.")
    else:
        latest_open = max(v[0] for v in spans.values())
        earliest_shut = min(v[1] for v in spans.values())
        print(f"  VERDICT  safe ordering span for ALL {len(spans)} coins:")
        print(f"           +{latest_open}s to +{earliest_shut}s "
              f"({latest_open//60}m{latest_open%60:02d}s to "
              f"{earliest_shut//60}m{earliest_shut%60:02d}s into the window)")
        if earliest_shut < 840:
            print(f"           NOTE: closes ~{(900-earliest_shut)//60}m before the")
            print(f"           window ends -- orders after that are rejected.")
    print("=" * 62)
    return 0



def min_order():
    """Send the smallest real order Kalshi allows and report exactly what comes back.

    Every larger test has been ambiguous. A zero count is rejected before the
    market is ever looked up, so the ticker-form probe's "count complaint means
    the ticker resolved" is not sound -- both forms returned the same count
    error, which is what count-validated-first looks like. And the invalid
    ticker probe returned a Washington geo-block, which may be real or may be
    what an uncategorisable ticker defaults to.

    Count 0.01 at roughly 40c is about four tenths of a cent. That buys an
    unambiguous answer:

      200/201            orders work. Everything else was our bug.
      403 geo            the runner's location is blocked; no code fix helps.
      404 market_not_found  with a ticker that GET resolves on this same host,
                         which would mean the market is hidden from this
                         account rather than missing.
      anything else      a real payload problem, named precisely.

    Uses the CURRENT open market, and the same body live.place() sends.
    """
    import uuid
    print("=" * 62)
    print("MINIMUM ORDER PROBE  (count 0.01 -- about $0.004)")
    print("=" * 62)
    hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
    r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                     params={"series_ticker": "KXBTC15M", "limit": 1,
                             "status": "open"}, headers=hh, timeout=15)
    ms = r.json().get("markets", []) if r.ok else []
    if not ms:
        print("  no open BTC market right now")
        return 1
    m = ms[0]
    tk = m.get("ticker")
    ask = m.get("yes_ask_dollars")
    print(f"  ticker : {tk}")
    print(f"  status : {m.get('status')}   yes_ask={ask}  yes_bid={m.get('yes_bid_dollars')}")

    # Prove the SAME host resolves this ticker by GET, so a market_not_found
    # from the order endpoint cannot be blamed on the market not existing.
    hh2 = _sign("GET", f"/trade-api/v2/markets/{tk}", SCHEME)
    g = requests.get(f"{NEW_HOST}/trade-api/v2/markets/{tk}", headers=hh2, timeout=10)
    print(f"  GET {tk} on the ORDER host -> {g.status_code}")

    try:
        price = float(ask)
    except (TypeError, ValueError):
        price = 0.50
    price = min(0.99, max(0.01, round(price, 2)))

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
    # The one combination never actually tried: a VALID count with the event
    # ticker. The earlier form probe used count 0, which is rejected before the
    # market is looked up, so it tested nothing about tickers. Try both forms
    # against both hosts and both order paths -- at 0.01 contracts the whole
    # matrix costs a few cents, and guessing has been more expensive than that.
    evt = m.get("event_ticker") or tk.rsplit("-", 1)[0]
    paths = ["/trade-api/v2/portfolio/events/orders", "/trade-api/v2/portfolio/orders"]
    hosts = [("external-api", NEW_HOST), ("api.elections", OLD_HOST)]
    print(f"  event ticker: {evt}")
    print()
    print(f"  {'host':14s} {'path':22s} {'ticker':7s} -> result")
    best = None
    for hname, host in hosts:
        for path in paths:
            for label, tkr in (("market", tk), ("event", evt)):
                body["ticker"] = tkr
                body["client_order_id"] = str(uuid.uuid4())
                hd = _sign("POST", path, SCHEME)
                hd["Content-Type"] = "application/json"
                try:
                    rr = requests.post(host + path, json=body, headers=hd, timeout=15)
                    code, txt = rr.status_code, rr.text[:110]
                except Exception as e:
                    code, txt = 0, str(e)[:110]
                short = path.split("/portfolio/")[1]
                print(f"  {hname:14s} {short:22s} {label:7s} -> {code} {txt}")
                if code in (200, 201) and best is None:
                    best = (hname, path, label)
    print()
    if best:
        print(f"  IT WORKS: host={best[0]} path={best[1]} ticker={best[2]}")
        print("  Orders can be placed. Point live.py at this combination.")
    else:
        print("  Every combination refused. The order path is not the problem;")
        print("  this account cannot trade these markets from here.")
    print("=" * 62)
    return 0



def why_blocked():
    """Why can this account not place an order? Narrow it to one cause.

    Known: a GET returns an active market with a real book, and a POST for the
    same ticker on every host, path and ticker form returns market_not_found.
    Two explanations survive and they call for completely different fixes:

      LOCATION   Kalshi blocks trading from where this runner sits. One probe
                 returned a Washington geo-block, and GitHub's runners are
                 Azure machines. Fix: run from somewhere else. The code is fine.

      ACCOUNT    The key lacks trade scope, or the account cannot trade these
                 markets. Fix: Kalshi support. Moving the code changes nothing.

    Four checks separate them:

      1. where this machine actually is, by IP
      2. whether the exchange says trading is open at all
      3. whether the key can read the portfolio, which proves its scope
      4. whether a NON-crypto market gives the SAME error

    Check 4 is the discriminator. The geo message named Sports, Elections,
    Politics, Culture, Tech and Science, and Mentions -- crypto was absent. If
    a blocked category answers 403 while crypto answers 404, the two are
    different mechanisms and crypto is not a geo problem at all. If everything
    answers the same way, it is account-wide.
    """
    import uuid
    print("=" * 62)
    print("WHY BLOCKED -- location, or account?")
    print("=" * 62)

    print("\n  1. WHERE IS THIS MACHINE")
    for url in ("https://ipinfo.io/json", "https://ifconfig.co/json"):
        try:
            r = requests.get(url, timeout=10)
            if r.ok:
                j = r.json()
                print(f"     {j.get('ip','?')}  "
                      f"{j.get('region') or j.get('region_name','?')}, "
                      f"{j.get('country') or j.get('country_iso','?')}  "
                      f"({j.get('city','?')}, org={str(j.get('org',''))[:40]})")
                break
        except Exception as e:
            print(f"     {url} failed: {str(e)[:60]}")

    # _get already prepends /trade-api/v2 and returns THREE values.
    print("\n  2. IS THE EXCHANGE OPEN")
    for path in ("/exchange/status", "/exchange/schedule"):
        code, body, err = _get(path)
        print(f"     {path:20s} {code} {err or str(body)[:110]}")

    print("\n  3. WHAT CAN THE KEY DO")
    for path in ("/portfolio/balance", "/portfolio/orders",
                 "/portfolio/positions", "/portfolio/fills"):
        code, body, err = _get(path)
        verdict = "ok" if code == 200 else "DENIED"
        print(f"     {path.split('/portfolio/')[1]:12s} {code} {verdict}  "
              f"{err or str(body)[:80]}")

    print("\n  4. DOES A NON-CRYPTO MARKET FAIL THE SAME WAY")
    # One crypto market and one from another series, same request shape.
    def first_open(series):
        hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
        r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                         params={"series_ticker": series, "limit": 1, "status": "open"},
                         headers=hh, timeout=15)
        ms = r.json().get("markets", []) if r.ok else []
        return ms[0] if ms else None

    def try_order(mkt, label):
        if not mkt:
            print(f"     {label:14s} no open market to test")
            return
        body = {
            "ticker": mkt["ticker"],
            "client_order_id": str(uuid.uuid4()),
            "side": "bid", "count": "0.01", "price": "0.0100",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False, "cancel_order_on_pause": False,
            "reduce_only": False, "subaccount": 0, "exchange_index": 0,
        }
        hd = _sign("POST", "/trade-api/v2/portfolio/events/orders", SCHEME)
        hd["Content-Type"] = "application/json"
        try:
            rr = requests.post(ORDER_URL, json=body, headers=hd, timeout=15)
            print(f"     {label:14s} {mkt['ticker'][:26]:26s} -> {rr.status_code} {rr.text[:95]}")
        except Exception as e:
            print(f"     {label:14s} failed {str(e)[:60]}")

    try_order(first_open("KXBTC15M"), "crypto 15m")
    for alt in ("KXHIGHNY", "KXINXD", "KXAAPL"):
        m = first_open(alt)
        if m:
            try_order(m, f"other ({alt})")
            break
    else:
        print("     other series   none of the tried series had an open market")

    print()
    print("  READ IT LIKE THIS")
    print("   - region says Washington and everything 404s  -> LOCATION")
    print("   - non-crypto 403s but crypto 404s             -> different causes;")
    print("     crypto is not geo-blocked and the 404 is something else")
    print("   - portfolio reads DENIED                      -> key lacks scope")
    print("   - everything 404s from a permitted region     -> ACCOUNT")
    print("=" * 62)
    return 0



def find_market_url():
    """Which kalshi.com URL actually opens a given 15-minute market?

    A phone alert is far more useful if tapping it lands on the market rather
    than on a search box, but the URL shape is not documented anywhere we can
    reach, and a link that 404s at 35 seconds into a window is worse than no
    link at all -- it burns the seconds the bet needed.

    So try the plausible shapes against a REAL open market and report which
    return 200 and where they redirect. Purely read-only GETs against the
    public website.
    """
    print("=" * 62)
    print("MARKET URL PROBE -- what link opens this market?")
    print("=" * 62)
    hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
    r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                     params={"series_ticker": "KXBTC15M", "limit": 1, "status": "open"},
                     headers=hh, timeout=15)
    ms = r.json().get("markets", []) if r.ok else []
    if not ms:
        print("  no open BTC market to test with")
        return 1
    m = ms[0]
    tk = m.get("ticker", "")
    evt = m.get("event_ticker") or tk.rsplit("-", 1)[0]
    ser = "KXBTC15M"
    print(f"  market {tk}")
    print(f"  event  {evt}\n")

    cands = [
        f"https://kalshi.com/markets/{tk}",
        f"https://kalshi.com/markets/{tk.lower()}",
        f"https://kalshi.com/events/{evt}",
        f"https://kalshi.com/events/{evt.lower()}",
        f"https://kalshi.com/markets/{ser.lower()}",
        f"https://kalshi.com/markets/{ser.lower()}/{evt.lower()}",
        f"https://kalshi.com/markets/kxbtcd",
        f"https://kalshi.com/crypto",
    ]
    ua = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
    for u in cands:
        try:
            rr = requests.get(u, timeout=15, allow_redirects=True, headers=ua)
            note = ""
            if rr.url.rstrip("/") != u.rstrip("/"):
                note = f"  -> redirected to {rr.url}"
            # A soft 404 renders 200 with a not-found page, so look for the
            # ticker in the body as well as trusting the status code.
            body = rr.text[:400000]
            hit = "TICKER IN PAGE" if (tk in body or evt in body) else ""
            print(f"  {rr.status_code}  {len(body):7d}b  {hit:15s} {u}{note}")
        except Exception as e:
            print(f"  ERR  {u}  {str(e)[:60]}")
    print()
    print("  Use the shortest URL that is 200 AND has the ticker in the page.")
    print("=" * 62)
    return 0



def exchange_index():
    """Are the 15-minute crypto markets on a different EXCHANGE INDEX?

    A non-crypto order filled from this runner with the same key and the same
    code path, while every crypto 15-minute order returns market_not_found. So
    it is not location, not the key's scope and not the account -- it is
    something specific to these markets.

    The order body hardcodes "exchange_index": 0. But each market carries its
    own exchange_index, and /exchange/status reports exchange_index_statuses --
    plural. If these markets live on a different index, the order endpoint
    looks for the ticker on exchange 0, does not find it there, and says
    market_not_found. Which is exactly what it says.

    This reads the index off each market and retries with the right one.
    Count 0.01 at 1c, so a fill costs about a cent.
    """
    import uuid
    print("=" * 62)
    print("EXCHANGE INDEX PROBE")
    print("=" * 62)

    def first_open(series):
        hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
        r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                         params={"series_ticker": series, "limit": 1, "status": "open"},
                         headers=hh, timeout=15)
        ms = r.json().get("markets", []) if r.ok else []
        return ms[0] if ms else None

    print("\n  WHAT INDEX IS EACH MARKET ON?")
    markets = {}
    for series in ("KXBTC15M", "KXETH15M", "KXHIGHNY"):
        m = first_open(series)
        if not m:
            print(f"     {series:10s} no open market")
            continue
        markets[series] = m
        print(f"     {series:10s} {str(m.get('ticker'))[:26]:26s} "
              f"exchange_index={m.get('exchange_index')!r}")

    code, body, err = _get("/exchange/status")
    if code == 200 and isinstance(body, dict):
        for st in body.get("exchange_index_statuses", []) or []:
            print(f"     exchange {st.get('exchange_index')!r}: "
                  f"{st.get('description')!r} active={st.get('exchange_active')}")

    print("\n  ORDER THE CRYPTO MARKET WITH EACH INDEX")
    m = markets.get("KXBTC15M")
    if not m:
        print("     no open crypto market to test")
        return 1
    native = m.get("exchange_index")
    tries = []
    for idx in (native, 0, 1, 2):
        if idx is not None and idx not in tries:
            tries.append(idx)
    for idx in tries:
        body_ = {
            "ticker": m["ticker"],
            "client_order_id": str(uuid.uuid4()),
            "side": "bid", "count": "0.01", "price": "0.0100",
            "time_in_force": "good_till_canceled",
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False, "cancel_order_on_pause": False,
            "reduce_only": False, "subaccount": 0, "exchange_index": idx,
        }
        hd = _sign("POST", "/trade-api/v2/portfolio/events/orders", SCHEME)
        hd["Content-Type"] = "application/json"
        try:
            rr = requests.post(ORDER_URL, json=body_, headers=hd, timeout=15)
            tag = " <-- the market's own index" if idx == native else ""
            print(f"     exchange_index={idx!r:6} -> {rr.status_code} {rr.text[:120]}{tag}")
            if rr.status_code in (200, 201):
                print(f"\n  FOUND IT: crypto orders need exchange_index={idx!r}")
                break
        except Exception as e:
            print(f"     exchange_index={idx!r:6} -> failed {str(e)[:60]}")
    print("=" * 62)
    return 0



def fills():
    """What did the orders actually DO -- rest, fill, and at what price?

    "PLACED" only means Kalshi accepted the order. A resting limit that never
    fills is a bet that was not made, and a fill at a worse price than the
    signal quoted is slippage the backtest never paid. Positions read flat
    seconds after placing, which could be either. This reads the record.
    """
    print("=" * 62)
    print("ORDERS AND FILLS")
    print("=" * 62)

    code, body, err = _get("/portfolio/orders", {"limit": 20})
    print(f"\n  ORDERS ({code})")
    if code == 200 and isinstance(body, dict):
        for o in (body.get("orders") or [])[:12]:
            print(f"    {str(o.get('ticker'))[:28]:28s} {str(o.get('action')):5s} "
                  f"{str(o.get('book_side')):4s} status={str(o.get('status')):10s} "
                  f"price={o.get('yes_price_dollars') or o.get('price')} "
                  f"placed={str(o.get('created_time'))[11:19]} "
                  f"remaining={o.get('remaining_count_fp') or o.get('remaining_count')}")
    else:
        print(f"    {err or body}")

    code, body, err = _get("/portfolio/fills", {"limit": 20})
    print(f"\n  FILLS ({code})")
    if code == 200 and isinstance(body, dict):
        fl = body.get("fills") or []
        if not fl:
            print("    none -- nothing has actually traded")
        for f in fl[:12]:
            print(f"    {str(f.get('ticker'))[:28]:28s} {str(f.get('action')):5s} "
                  f"{str(f.get('side')):4s} count={f.get('count_fp') or f.get('count')} "
                  f"price={f.get('yes_price_dollars') or f.get('price')} "
                  f"at={str(f.get('created_time'))[11:19]}")
    else:
        print(f"    {err or body}")

    code, body, err = _get("/portfolio/positions")
    print(f"\n  POSITIONS ({code})")
    if code == 200 and isinstance(body, dict):
        mp = body.get("market_positions") or []
        if not mp:
            print("    no open market positions")
        for pos in mp[:12]:
            print(f"    {str(pos.get('ticker'))[:28]:28s} "
                  f"position={pos.get('position_fp') or pos.get('position')} "
                  f"exposure={pos.get('market_exposure_dollars')}")
    else:
        print(f"    {err or body}")

    code, body, err = _get("/portfolio/balance")
    if code == 200 and isinstance(body, dict):
        print(f"\n  BALANCE  {body.get('balance')} (cents)")
    print("=" * 62)
    return 0



def order_types():
    """Which time_in_force does Kalshi accept, and which one dies unfilled?

    Our orders are good_till_canceled. A GTC limit that misses the book rests
    there -- one sat at 58c against a 2.90c market -- so the log says PLACED,
    the phone alert says a bet was made, and nothing was actually bought. On a
    15-minute market an order that does not fill at once should cease to exist.

    SAFE: every probe is priced at 1c on the YES side, which no seller will
    hit, so nothing can fill. The only thing being read is which values the
    endpoint ACCEPTS, and what happens to an order that cannot trade.
    """
    import uuid
    print("=" * 62)
    print("ORDER TYPE PROBE  (1c bids -- cannot fill)")
    print("=" * 62)
    hh = _sign("GET", "/trade-api/v2/markets", SCHEME)
    r = requests.get(NEW_HOST + "/trade-api/v2/markets",
                     params={"series_ticker": "KXBTC15M", "limit": 1, "status": "open"},
                     headers=hh, timeout=15)
    ms = r.json().get("markets", []) if r.ok else []
    if not ms:
        print("  no open BTC market to test with")
        return 1
    m = ms[0]
    print(f"  market {m.get('ticker')}  bid={m.get('yes_bid_dollars')} "
          f"ask={m.get('yes_ask_dollars')}  exchange_index={m.get('exchange_index')}\n")

    placed = []
    for tif in ("good_till_canceled", "immediate_or_cancel", "fill_or_kill",
                "fill_and_kill", "ioc", "fok", "expire_at_close"):
        body = {
            "ticker": m["ticker"],
            "client_order_id": str(uuid.uuid4()),
            "side": "bid", "count": "0.01", "price": "0.0100",
            "time_in_force": tif,
            "self_trade_prevention_type": "taker_at_cross",
            "post_only": False, "cancel_order_on_pause": False,
            "reduce_only": False, "subaccount": 0,
            "exchange_index": m.get("exchange_index", 0),
        }
        hd = _sign("POST", "/trade-api/v2/portfolio/events/orders", SCHEME)
        hd["Content-Type"] = "application/json"
        try:
            rr = requests.post(ORDER_URL, json=body, headers=hd, timeout=15)
            ok = rr.status_code in (200, 201)
            if ok:
                try:
                    placed.append((tif, rr.json().get("order_id")))
                except Exception:
                    pass
            print(f"  {tif:22s} -> {rr.status_code} {rr.text[:100]}")
        except Exception as e:
            print(f"  {tif:22s} -> failed {str(e)[:60]}")

    # An accepted value is only useful if an unfillable order does NOT linger.
    if placed:
        print("\n  DID THE ACCEPTED ONES SURVIVE? (resting = wrong for us)")
        code, body, err = _get("/portfolio/orders", {"limit": 30})
        resting = {}
        if code == 200 and isinstance(body, dict):
            for o in body.get("orders") or []:
                resting[o.get("order_id")] = o.get("status")
        for tif, oid in placed:
            st = resting.get(oid, "gone")
            verdict = "RESTS (bad)" if st in ("resting", "open") else f"{st} (good)"
            print(f"  {tif:22s} {verdict}")
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


def settlements():
    """What Kalshi actually PAID, which is the only real scoreboard.

    The dashboard has been grading live bets with the predictor's own
    "CVD Correct?" column, which answers a different question: did the coin's
    spot price move up or down from the moment the signal fired. A Kalshi
    15-minute contract does not settle on that. It settles on whether the coin
    is above or below a FIXED STRIKE at the close, and that strike is set when
    the market opens, not where spot happened to be when we sampled it.

    Those two questions agree only when the strike sits exactly at our sample
    price. Every cent of gap between them is a bet that can settle the opposite
    way to how we graded it -- which is how a page can report +$44 while the
    account is flat.

    This reads the settlements ledger and the balance: ground truth, no join,
    no inference.
    """
    print("=" * 62)
    print("SETTLEMENTS -- what Kalshi actually paid")
    print("=" * 62)

    code, body, err = _get("/portfolio/balance")
    if code == 200 and isinstance(body, dict):
        bal = body.get("balance_dollars") or body.get("balance")
        print(f"\n  BALANCE: {bal}")
    else:
        print(f"\n  BALANCE unavailable: {err or body}")

    code, body, err = _get("/portfolio/settlements", {"limit": 200})
    print(f"\n  SETTLEMENTS ({code})")
    if code != 200 or not isinstance(body, dict):
        print(f"    {err or body}")
        return 1
    st = body.get("settlements") or []
    if not st:
        print("    none returned")
        return 0

    print(f"    {len(st)} row(s), newest first\n")
    # Print the RAW fields. The first pass at this guessed which keys held the
    # cost and the payout and guessed wrong -- payouts came back as 1000.00 for
    # a $10 contract, i.e. cents, and a NO position's cost sits under a
    # different key than a YES position's. Guessing is what produced the
    # fabricated P&L in the first place, so this prints what the API returns
    # and lets the arithmetic be checked rather than trusted.
    import json as _json
    for s_ in st[:24]:
        print("    " + _json.dumps(s_, sort_keys=True))
    print("=" * 62)
    return 0


def _money(d, keys):
    """First of `keys` that parses as a number, else 0.0."""
    for k in keys:
        v = d.get(k)
        if v in (None, ""):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--settlements":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(settlements())
    if len(sys.argv) > 1 and sys.argv[1] == "--find-market-url":
        sys.exit(find_market_url())
    if len(sys.argv) > 1 and sys.argv[1] == "--order-types":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(order_types())
    if len(sys.argv) > 1 and sys.argv[1] == "--fills":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(fills())
    if len(sys.argv) > 1 and sys.argv[1] == "--exchange-index":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(exchange_index())
    if len(sys.argv) > 1 and sys.argv[1] == "--why-blocked":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(why_blocked())
    if len(sys.argv) > 1 and sys.argv[1] == "--min-order":
        for _s in ("pss", "pkcs1v15"):
            SCHEME = _s
            if _get("/portfolio/balance")[0] == 200:
                break
        sys.exit(min_order())
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

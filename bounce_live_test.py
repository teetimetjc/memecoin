"""ONE $5 bounce trade, to find out whether the exit leg works at all.

WHAT IS ACTUALLY IN DOUBT. Buying is proven -- orders fill, on both sides,
with the exchange_index fix. What has never been tested is the RESTING SELL:
a take-profit that sits on the book and fills unattended while nobody is
watching. The entire bounce idea is that leg. If Kalshi's API cannot hold
one, the strategy is untradeable whatever the data eventually says, and that
is worth knowing now rather than after a week of waiting for a clean signal.

So this is a MECHANICAL test, not a strategy test. Whether the trade makes
money is close to irrelevant; what matters is:

  1. does a resting sell get accepted at all
  2. does it need good_till_canceled rather than the immediate_or_cancel the
     entry uses
  3. does it survive to fill, or get cancelled at settlement, or stick

THE SHAPE OF THE EXIT, which is the part easy to get backwards. The order
body prices everything in YES terms:
    buying YES  -> side "bid",  yes_price = p
    buying NO   -> side "ask",  yes_price = 1 - p     (selling YES at 1-p)
So the exit is the mirror of the entry:
    holding YES -> sell with side "ask", yes_price = target/100
    holding NO  -> sell with side "bid", yes_price = 1 - target/100
Getting this inverted would place a SECOND buy rather than an exit, which is
why reduce_only is set: an exit that cannot reduce a position should be
refused by the exchange rather than silently double the stake.

Hard limits: one buy, one sell, $5, and it refuses to run without the
confirmation string.
"""

import math
import sys
import uuid
from datetime import datetime, timedelta, timezone

import requests

import live
import predictor as P

STAKE = 5.0
LO, HI = 20.0, 35.0          # the champion's entry band
TAKE = 2.0
PRICE_HOST = "https://api.elections.kalshi.com"


def book(ticker):
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}") or {}
        r = requests.get(f"{PRICE_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs, timeout=10)
        if not r.ok:
            return None, None
        m = r.json().get("market") or {}
        return (round(float(m["yes_bid_dollars"]) * 100, 1),
                round(float(m["yes_ask_dollars"]) * 100, 1))
    except Exception:
        return None, None


def this_window():
    now = datetime.now(timezone.utc)
    b = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    close = b + timedelta(minutes=15)
    want = f"{close:%Y-%m-%dT%H:%M}"
    out = []
    for symbol, series in P.KALSHI_SERIES.items():
        try:
            hdrs = P._kalshi_headers("GET", "/trade-api/v2/markets") or {}
            r = requests.get(f"{PRICE_HOST}/trade-api/v2/markets",
                             params={"series_ticker": series, "limit": 200},
                             headers=hdrs, timeout=10)
            for mk in (r.json().get("markets", []) if r.ok else []):
                if str(mk.get("close_time", ""))[:16] == want:
                    out.append((symbol, mk["ticker"]))
                    break
        except Exception:
            continue
    return b, out


def sell(ticker, holding_yes, contracts, target_cents, exch, tag):
    """Rest a take-profit on the book. Returns (ok, detail, order_id)."""
    # Mirror of the entry. Inverting this would place another BUY.
    yes_price = (target_cents / 100.0) if holding_yes else (1.0 - target_cents / 100.0)
    body = {
        "ticker": ticker,
        "client_order_id": str(uuid.uuid5(uuid.NAMESPACE_URL, tag)),
        "side": "ask" if holding_yes else "bid",
        "count": f"{contracts}.00",
        "price": f"{yes_price:.4f}",
        # MUST rest. immediate_or_cancel is right for an entry that is only
        # worth making at once; a take-profit that cancels immediately is not
        # a take-profit at all.
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": False,
        # An exit should only ever shrink a position. If the side is wrong,
        # let the exchange refuse it rather than quietly double the bet.
        "reduce_only": True,
        "subaccount": 0,
        "exchange_index": exch,
    }
    hdrs = P._kalshi_headers("POST", "/trade-api/v2/portfolio/events/orders")
    if not hdrs:
        return False, "could not sign", ""
    hdrs["Content-Type"] = "application/json"
    try:
        r = requests.post(live.ORDER_URL, json=body, headers=hdrs, timeout=15)
    except Exception as e:
        return False, f"request failed: {e}", ""
    if r.status_code in (200, 201):
        j = r.json()
        o = j.get("order") or j
        return True, (f"status={o.get('status')} "
                      f"resting={o.get('remaining_count_fp') or o.get('remaining_count')}"), \
               o.get("order_id", "")
    return False, f"HTTP {r.status_code} {r.text[:200]}", ""


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "RUN-ONE-FIVE-DOLLAR-TEST":
        print("Refusing: pass the confirmation string to run this.")
        return 1
    print("=" * 66)
    print("BOUNCE EXIT TEST -- one $5 buy, then a resting sell at 2x")
    print("=" * 66)
    if not live.enabled():
        print("  LIVE_TRADING / ALLOW_LIVE_TRADING are not both set. Nothing sent.")
        return 1

    boundary, markets = this_window()
    secs = (datetime.now(timezone.utc) - boundary).total_seconds()
    print(f"  window {boundary:%H:%M} UTC, now +{secs:.0f}s, {len(markets)} markets")
    if not markets:
        print("  no markets resolved for this window.")
        return 1

    pick = None
    for symbol, ticker in markets:
        b, a = book(ticker)
        if b is None or a is None:
            print(f"    {symbol:9s} no book")
            continue
        no_ask = round(100.0 - b, 1)
        cy, cn = a <= HI, no_ask <= HI
        entry = a if cy else no_ask
        flag = ""
        if cy != cn and LO <= entry < HI:
            flag = "  <- SETUP"
            if pick is None:
                pick = (symbol, ticker, cy, entry)
        print(f"    {symbol:9s} yes {a:5.1f}c / no {no_ask:5.1f}c{flag}")

    if not pick:
        print("\n  No coin is in the 20-35c band this window. Nothing sent.")
        print("  (That is a normal outcome -- run it again next window.)")
        return 0

    symbol, ticker, holding_yes, entry = pick
    side = "UP" if holding_yes else "DOWN"
    print(f"\n  BUYING {symbol} {side} at {entry:.1f}c, ${STAKE:.2f}")

    ts = boundary.strftime("%Y-%m-%d %H:%M UTC")
    status, detail, n, cost, oid = live.place(ts, symbol, side, ticker, entry, STAKE)
    print(f"    {status}  {detail or ''}  {n} contracts, ${cost:.2f}, order {oid or '-'}")
    if status != "PLACED" or n < 1:
        print("\n  Entry did not fill, so there is nothing to sell. "
              "The exit leg is still untested.")
        return 0

    target = min(99.0, round(entry * TAKE, 1))
    exch = live.market_exchange_index(ticker)
    print(f"\n  RESTING SELL of {n} at {target:.1f}c "
          f"(side {'ask' if holding_yes else 'bid'}, reduce_only)")
    ok, sdetail, soid = sell(ticker, holding_yes, int(n), target, exch,
                             f"exit-{ts}-{symbol}")
    print(f"    {'ACCEPTED' if ok else 'REFUSED'}  {sdetail}  order {soid or '-'}")

    print("\n  READ BACK")
    live.verify_positions()
    try:
        hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/orders") or {}
        r = requests.get(f"{live.ORDER_HOST}/trade-api/v2/portfolio/orders",
                         params={"limit": 5}, headers=hdrs, timeout=10)
        if r.ok:
            for o in (r.json().get("orders") or [])[:5]:
                print(f"    {str(o.get('ticker'))[:26]:26s} {str(o.get('side')):4s} "
                      f"status={str(o.get('status')):10s} "
                      f"price={o.get('yes_price_dollars') or o.get('price')} "
                      f"left={o.get('remaining_count_fp') or o.get('remaining_count')}")
    except Exception as e:
        print(f"    (could not read orders: {e})")

    print("\n  WHAT TO LOOK FOR: the sell should show as resting with the full")
    print("  count left. If it filled instantly the target was already through;")
    print("  if it was refused, the message above says why.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Sell back a position that filled far below its quote. OFF by default.

THE PROBLEM IT ADDRESSES. A buy limit has no floor: once the order is sent,
a collapsing book hands over a cheap fill and nothing can refuse it. Fills
more than 5c below quote win 50% against a 72.6% break-even -- 12 bets,
-$20.61. Buying cannot be undone. Keeping the position is a choice.

WHAT IT DOES. After an order fills, it reads the actual fill price from the
exchange, and if the fill is far enough below the quote, it offers to sell
the position straight back at the bid.

IT IS OFF UNLESS ABORT_SELL=1. With the flag unset it still does all the
reading and all the arithmetic and writes down what it WOULD have sold for,
which is the measurement that decides whether arming it is worth anything.
A dry favlive session never sells regardless of the flag.

TWO SAFETY PROPERTIES, BOTH DELIBERATE:

  reduce_only=True on the sell. The order can only close the position it was
  given, never flip it into a short. Without this, a sell for more contracts
  than are held -- a miscount, a partial fill misread -- would quietly open
  a position on the other side of a market the bot has already decided it
  does not like.

  A bid floor. Selling into a vacuum at 20c is worse than holding a contract
  that wins half the time. Below ABORT_MIN_BID_CENTS it declines and says so.

UP ONLY. Selling a NO position is the mirror trade and is not implemented,
because the NO side is switched off and untested code that sends orders is
worse than no code. It refuses explicitly rather than guessing.
"""
import os
import time

import requests

SHEET = "Aborts"

HEADERS = [
    "Timestamp", "Ticker", "Side", "Contracts", "Quote ¢", "Fill ¢",
    "Slip ¢", "Bid ¢", "Sell fee ¢", "Sell net ¢", "Hold value ¢",
    "Edge vs hold ¢", "Decision", "Armed", "Result", "Note",
]

# OFF. The flag is read at call time, not import time, so a session cannot
# be armed by anything except the workflow that started it.
def armed():
    return (os.environ.get("ABORT_SELL") or "0").strip() == "1"


def _c(name, default):
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


# How far below the quote a fill must land before this considers selling.
# 5c matches the measured cliff: 2-5c below quote is +6.7 points and
# profitable, beyond 5c is -22.6 points. Selling a 3c-below fill would be
# selling a winner.
def slip_trigger():
    return _c("ABORT_SLIP_CENTS", 5.0)


# A blunt backstop, OFF by default, because the edge test below already does
# this job properly. A first draft set it to 60c and the unit test caught it
# vetoing a sale that was 6.3c BETTER than holding: a 58c bid nets 56.3c
# against a contract worth 50c kept. The floor was protecting against
# "selling into a vacuum", but a vacuum fails the edge test by itself -- a
# 35c bid nets 33c and loses to holding without any floor being involved.
#
# It survives only as a lever for the case where HOLD_VALUE_CENTS turns out
# to be wrong and something cruder is wanted in a hurry.
def min_bid():
    return _c("ABORT_MIN_BID_CENTS", 0.0)


# What a slipped fill is worth if kept: the cohort's win rate, in cents per
# contract. 50% over 12 bets. Deliberately a constant with its provenance in
# the name of the variable, so nobody mistakes it for a live calculation.
HOLD_VALUE_CENTS = _c("ABORT_HOLD_VALUE_CENTS", 50.0)


def fee_cents(contracts, price_cents):
    """The verified Kalshi fee, in cents, for `contracts` at this price."""
    p = price_cents / 100.0
    return 100.0 * (0.07 * contracts * p * (1.0 - p))


def decide(quote_c, fill_c, bid_c, contracts,
           slip_c=None, floor_c=None, hold_c=None):
    """Pure. Returns (action, reason, sell_net_c, edge_c).

    action is SELL, HOLD or SKIP. No IO, so it can be tested without an
    exchange, which is the only way code that sends orders should be written.
    """
    slip_c = slip_trigger() if slip_c is None else slip_c
    floor_c = min_bid() if floor_c is None else floor_c
    hold_c = HOLD_VALUE_CENTS if hold_c is None else hold_c

    if quote_c is None or fill_c is None:
        return "SKIP", "fill or quote unknown", None, None
    if not contracts:
        return "SKIP", "no position", None, None
    drop = quote_c - fill_c
    if drop <= slip_c:
        return ("HOLD", f"fill {drop:.1f}c below quote, within the {slip_c:.0f}c "
                "trigger", None, None)
    if bid_c is None:
        return "SKIP", "no bid readable", None, None
    if bid_c <= 0 or bid_c >= 100:
        return "SKIP", f"bid {bid_c:.0f}c is not a usable price", None, None

    net = bid_c - fee_cents(contracts, bid_c) / contracts
    edge = net - hold_c
    if bid_c < floor_c:
        return ("HOLD", f"bid {bid_c:.0f}c is below the {floor_c:.0f}c floor",
                net, edge)
    if edge <= 0:
        return ("HOLD", f"selling nets {net:.1f}c, holding is worth "
                f"{hold_c:.0f}c", net, edge)
    return ("SELL", f"selling nets {net:.1f}c vs {hold_c:.0f}c held "
            f"({edge:+.1f}c)", net, edge)


def fill_price_cents(order_id):
    """VWAP fill for one order, in cents, from the exchange. None if unknown.

    Quantity-weighted, because an order can fill in pieces at different
    prices and the mean of the prices is not the price paid.
    """
    import predictor as P
    import live
    try:
        hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/fills")
        r = requests.get(f"{live.ORDER_HOST}/trade-api/v2/portfolio/fills",
                         headers=hdrs or {}, params=dict(limit=100), timeout=10)
        if not r.ok:
            return None, 0.0
        tot_q = tot_v = 0.0
        for f in (r.json() or {}).get("fills") or []:
            if str(f.get("order_id") or "") != str(order_id):
                continue
            q = f.get("count_fp") or f.get("count") or 0
            side = str(f.get("side") or "").lower()
            px = (f.get("no_price_dollars") if side == "no"
                  else f.get("yes_price_dollars"))
            try:
                q, px = float(q), float(px)
            except (TypeError, ValueError):
                continue
            tot_q += q
            tot_v += q * px * 100.0
        return ((tot_v / tot_q) if tot_q else None), tot_q
    except Exception:
        return None, 0.0


def sell(ticker, contracts, price_cents, dry):
    """Send ONE reduce-only IOC sell of a YES position. (ok, detail).

    Returns without sending when `dry`, so the whole path can be exercised
    against real books and real prices with nothing at risk.
    """
    import predictor as P
    import live
    import uuid

    if dry:
        return True, f"DRY: would sell {contracts} at {price_cents:.0f}c"
    exch = live.market_exchange_index(ticker)
    if exch is None:
        return False, "could not read the market's exchange index"
    body = {
        "ticker": ticker,
        "client_order_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                          f"abort-{ticker}-UP")),
        # Selling YES is the ask side, the same way buying NO is.
        "side": "ask",
        "count": f"{int(contracts)}.00",
        "price": f"{price_cents / 100.0:.4f}",
        "time_in_force": "immediate_or_cancel",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        # THE IMPORTANT ONE. This order may only close what is held. A sell
        # that could open a short would turn a miscount into a position on
        # the side the bot just decided it dislikes.
        "reduce_only": True,
        "cancel_order_on_pause": False,
        "subaccount": 0,
        "exchange_index": exch,
    }
    hdrs = P._kalshi_headers("POST", "/trade-api/v2/portfolio/events/orders")
    if not hdrs:
        return False, "could not sign the sell"
    hdrs["Content-Type"] = "application/json"
    try:
        r = requests.post(live.ORDER_URL, json=body, headers=hdrs, timeout=15)
    except Exception as e:
        return False, f"sell request failed: {e}"
    if r.status_code in (200, 201):
        try:
            j = r.json()
            filled = float(j.get("fill_count")
                           or (j.get("order") or {}).get("fill_count") or 0)
        except Exception:
            filled = 0.0
        if filled <= 0:
            return False, "sell accepted but filled nothing (no bid)"
        return True, f"sold {filled:g} at up to {price_cents:.0f}c"
    return False, f"HTTP {r.status_code} {r.text[:120]}"


def consider(client, rows, dry):
    """For each filled order: measure, decide, and log. Never raises."""
    try:
        import shadow
        out = []
        for r in rows or []:
            if len(r) < 11 or str(r[8]) != "PLACED":
                continue
            ts, side, ticker = r[0], str(r[2]), str(r[3])
            quote_c = r[4] if isinstance(r[4], (int, float)) else None
            contracts = int(r[6] or 0)
            oid = str(r[9] or "")
            if not oid or not contracts:
                continue
            if side != "UP":
                out.append([ts, ticker, side, contracts, quote_c, "", "", "",
                            "", "", "", "", "SKIP",
                            "yes" if armed() else "no", "",
                            "selling a NO position is not implemented"])
                continue
            fill_c, _q = fill_price_cents(oid)
            ob = shadow.orderbook(ticker)
            bid_c, _ask = shadow._best(ob, "UP") if ob else (None, None)
            action, why, net, edge = decide(quote_c, fill_c, bid_c, contracts)
            result = ""
            if action == "SELL":
                ok, detail = sell(ticker, contracts, bid_c,
                                  dry or not armed())
                result = detail
                print(f"  [abort] {ticker}: {why} -> {detail}")
            out.append([
                ts, ticker, side, contracts,
                round(quote_c, 1) if quote_c is not None else "",
                round(fill_c, 1) if fill_c is not None else "",
                round(quote_c - fill_c, 1)
                if (quote_c is not None and fill_c is not None) else "",
                round(bid_c, 1) if bid_c is not None else "",
                round(fee_cents(contracts, bid_c) / contracts, 2)
                if bid_c else "",
                round(net, 1) if net is not None else "",
                HOLD_VALUE_CENTS,
                round(edge, 1) if edge is not None else "",
                action, "yes" if armed() else "no", result, why,
            ])
        if not out:
            return
        import predictor as P
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=2000, cols=len(HEADERS))
            ws.update(values=[HEADERS], range_name="A1")
        ws.append_rows(out, value_input_option="USER_ENTERED",
                       table_range="A1")
        print(f"  [abort] logged {len(out)} row(s); armed={armed()}")
    except Exception as e:
        print(f"  [abort] skipped: {str(e)[:110]}")

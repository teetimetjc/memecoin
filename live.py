"""Live order placement. THIS IS THE FILE THAT SPENDS MONEY.

Everything else in this project reads. This writes, and a mistake here costs
real dollars, so it is written to fail toward doing nothing.

Configuration, all of which must line up before a single order is sent:
  ALLOW_LIVE_TRADING=1   the human's thumbs-up, checked by no_trade_guard
  LIVE_TRADING=1         this module actually places orders
  Control tab = RUNNING  the switch you can flip from your phone
  entry < MAX_ENTRY      the price cut the evidence supports
Any one of them missing means no orders. There is no default that bets.

The rules it enforces:

  ALL OR NOTHING PER WINDOW. If the balance cannot fund every signal in the
  window, none are placed. Funding three of four silently drops whichever coin
  the loop reaches last, biasing the record toward the top of the list in a
  way nobody could reconstruct later.

  A MISSING MARKET IS A SKIP, NOT A HALT. Kalshi had no priced 15-minute
  market for 1h45m on 17 Sep -- an outage across all five coins. That is
  normal and recurring; halting on it would freeze betting at 2am and wait for
  a human. Only genuinely unexpected conditions halt.

  IDEMPOTENT. Every order carries a client_order_id derived from the window
  and symbol, so the same signal cannot be bet twice if a run is retried.

  LIMIT ORDERS ONLY. A market order would take whatever the book offers. The
  limit is the quoted price plus SLIP_BUFFER_CENTS, so a book that moves
  against us between quote and send results in no fill rather than a bad one.
"""

import json
import math
import os
import uuid
from datetime import datetime, timezone

import requests

import control

LIVE_SHEET = "Live Bets"

# Kalshi's V2 create-order endpoint. NOT the api.elections host this project
# reads market data from, and NOT /portfolio/orders -- that path answers 410
# "deprecated_v1_order_endpoint" on every host.
ORDER_URL = "https://external-api.kalshi.com/trade-api/v2/portfolio/events/orders"

# The V2 book has ONE side per market, quoted as the YES price, with bid/ask
# rather than yes/no -- confirmed by probing: "side must be bid or ask".
#
#   UP   = bid: buy the YES contract at its price. Unambiguous.
#   DOWN = ask: selling YES at p is economically buying NO at (1-p). That is
#          standard, and it is still an INFERENCE -- no page we have says it.
#          Reading it backwards would put every DOWN bet on the wrong side of
#          the market, which is the worst failure this system could have, so
#          DOWN is not bet until one order has been placed and the resulting
#          position read back and confirmed. UP-only halves the bet rate and
#          costs nothing that a day of data will not replace.
ALLOW_DOWN = os.environ.get("LIVE_ALLOW_DOWN", "").strip() == "1"

# The slice the evidence supports. Entries at or above this are not bet.
MAX_ENTRY = 50.0

# How far above the quoted price we are willing to pay. One cent covers the
# ordinary tick of movement between reading the book and the order landing;
# beyond that we would rather miss the bet.
SLIP_BUFFER_CENTS = 1.0

LIVE_HEADERS = [
    "Timestamp", "Symbol", "Side", "Ticker", "Entry ¢", "Limit ¢",
    "Contracts", "Cost $", "Status", "Order ID", "Detail",
]


def enabled():
    """Live betting requires BOTH switches. Neither defaults to on."""
    return (os.environ.get("LIVE_TRADING", "").strip().lower() in ("1", "true", "yes")
            and os.environ.get("ALLOW_LIVE_TRADING", "").strip() == "1")


def _client_order_id(ts, symbol):
    """Stable per signal, so a retried run cannot double-bet the same window."""
    return f"v6-{ts.replace(' ', 'T').replace(':', '')}-{symbol}"[:64]


def place(ts, symbol, side, ticker, entry_cents, stake):
    """Send ONE limit order. Returns (status, detail, contracts, cost, order_id).

    status is PLACED, SKIPPED or FAILED. Only FAILED should halt the bot:
    SKIPPED means a bet we chose not to make, which is a normal outcome.
    """
    import predictor as P

    if not ticker:
        return "SKIPPED", "no Kalshi market for this window", 0, 0.0, ""
    if entry_cents is None or entry_cents <= 0:
        return "SKIPPED", "no usable entry price", 0, 0.0, ""
    if entry_cents >= MAX_ENTRY:
        return "SKIPPED", f"entry {entry_cents:.0f}c is at or above the {MAX_ENTRY:.0f}c cut", 0, 0.0, ""

    if side == "DOWN" and not ALLOW_DOWN:
        return ("SKIPPED",
                "DOWN needs the ask-side mapping confirmed against a real fill",
                0, 0.0, "")

    limit_c = min(99.0, math.ceil(entry_cents + SLIP_BUFFER_CENTS))
    contracts = int(stake / (limit_c / 100.0))     # never round UP into overspend
    if contracts < 1:
        return "SKIPPED", f"${stake:.2f} buys no contracts at {limit_c:.0f}c", 0, 0.0, ""
    cost = round(contracts * limit_c / 100.0, 2)

    # The order is priced in YES terms whichever way we are betting: buying YES
    # at p, or selling YES at (1-p) which is buying NO at p.
    yes_price = limit_c / 100.0 if side == "UP" else 1.0 - limit_c / 100.0

    body = {
        "ticker": ticker,
        # Deterministic UUID from the window and symbol: the server dedupes on
        # it, so a retried run cannot bet the same signal twice, and the V2
        # endpoint requires UUID form rather than a free-text id.
        "client_order_id": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                          _client_order_id(ts, symbol))),
        "side": "bid" if side == "UP" else "ask",
        "count": f"{contracts}.00",
        "price": f"{yes_price:.4f}",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "post_only": False,
        "cancel_order_on_pause": False,
        "reduce_only": False,
        "subaccount": 0,
        "exchange_index": 0,
    }
    hdrs = P._kalshi_headers("POST", "/trade-api/v2/portfolio/events/orders")
    if not hdrs:
        return "FAILED", "could not sign the order request", 0, 0.0, ""
    hdrs["Content-Type"] = "application/json"

    try:
        r = requests.post(ORDER_URL, json=body, headers=hdrs, timeout=15)
    except Exception as e:
        return "FAILED", f"request failed: {e}", contracts, cost, ""

    if r.status_code in (200, 201):
        try:
            j = r.json()
            oid = j.get("order_id") or (j.get("order") or {}).get("order_id", "")
        except Exception:
            oid = ""
        return "PLACED", "", contracts, cost, oid

    # A rejection is not necessarily a crisis, but it IS unexpected, and the
    # rule for unexpected things involving money is to stop and ask a human.
    return "FAILED", f"HTTP {r.status_code} {r.text[:150]}", contracts, cost, ""


def run_window(client, signals, stake=None):
    """Bet a whole window, or none of it. `signals` = [(ts,symbol,side,ticker,entry)].

    Returns the rows written. Halts (and notifies) on a funding shortfall or a
    genuine failure; skips quietly on anything expected.
    """
    state, why = control.get_state(client)
    if state != control.RUNNING:
        print(f"  [live] Control tab is {state} -- no orders. {why}")
        return []

    stake = stake if stake is not None else control.get_stake(client)

    # Only the signals we would actually bet count toward the funding need.
    betable = [s for s in signals
               if s[3] and s[4] is not None and 0 < s[4] < MAX_ENTRY]
    if not betable:
        print("  [live] nothing betable this window.")
        return []

    ok, needed, bal, reason = control.check_window(client, len(betable), stake=stake)
    control.record_balance(client, bal)
    if not ok:
        control.halt(client, f"Funding short -- {reason}. No orders were placed.")
        return []

    rows, placed, failures = [], 0, []
    for ts, symbol, side, ticker, entry in signals:
        status, detail, n, cost, oid = place(ts, symbol, side, ticker, entry, stake)
        rows.append([ts, symbol, side, ticker,
                     round(entry, 1) if entry else "",
                     math.ceil(entry + SLIP_BUFFER_CENTS) if entry else "",
                     n, cost, status, oid, detail])
        if status == "PLACED":
            placed += 1
        elif status == "FAILED":
            failures.append(f"{symbol}: {detail}")

    if rows:
        append(client, rows)
    print(f"  [live] {placed} order(s) placed, "
          f"{sum(1 for r in rows if r[8]=='SKIPPED')} skipped, {len(failures)} failed.")

    if failures:
        control.halt(client, "Order rejected by Kalshi -- " + "; ".join(failures[:3]))
    return rows


def append(client, rows):
    import predictor as P
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(LIVE_SHEET)
    except Exception:
        ws = sh.add_worksheet(title=LIVE_SHEET, rows=4000, cols=len(LIVE_HEADERS))
        ws.update("A1", [LIVE_HEADERS])
    ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")


# --- hourly pulse --------------------------------------------------------

def pulse(client):
    """One notification summarising the last hour. Not one per bet."""
    import predictor as P
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(LIVE_SHEET).get_all_values()
    except Exception:
        return
    if len(rows) < 2:
        return

    idx = {h: i for i, h in enumerate(rows[0])}
    def cell(r, k):
        i = idx.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    now = datetime.now(timezone.utc)
    cutoff = (now.timestamp() - 3600)
    hour_n = hour_cost = 0
    all_n = all_cost = 0
    for r in rows[1:]:
        if cell(r, "Status") != "PLACED":
            continue
        all_n += 1
        try:
            all_cost += float(cell(r, "Cost $") or 0)
        except ValueError:
            pass
        try:
            t = datetime.strptime(cell(r, "Timestamp"), "%Y-%m-%d %H:%M UTC")
            t = t.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if t.timestamp() >= cutoff:
            hour_n += 1
            try:
                hour_cost += float(cell(r, "Cost $") or 0)
            except ValueError:
                pass

    bal = control.fetch_balance()
    state, _ = control.get_state(client)
    # Balance is the honest scoreboard: it already nets fees, wins and losses,
    # and it cannot drift from reality the way a computed running total can.
    msg = (f"Last hour: {hour_n} bets, ${hour_cost:.2f} staked.\n"
           f"Total placed: {all_n} bets, ${all_cost:.2f} staked.\n"
           f"Balance: {'unreadable' if bal is None else f'${bal:.2f}'}\n"
           f"State: {state}")
    P.send_pushover("v6 pulse", msg)
    print(f"  [pulse] sent -- {hour_n} bets in the last hour, balance "
          f"{'?' if bal is None else f'${bal:.2f}'}")

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
import time
import uuid
from datetime import datetime, timezone

import requests

import control

LIVE_SHEET = "Live Bets"

# Kalshi's V2 create-order endpoint. NOT the api.elections host this project
# reads market data from, and NOT /portfolio/orders -- that path answers 410
# "deprecated_v1_order_endpoint" on every host.
ORDER_HOST = "https://external-api.kalshi.com"
ORDER_URL = ORDER_HOST + "/trade-api/v2/portfolio/events/orders"

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

# The slice the evidence supports. Entries outside this band are not bet.
#
# The ceiling is the strategy's price cut: 50-60c measures -$1.32 a bet.
#
# THE FLOOR EXISTS BECAUSE THERE WAS NONE. Live, the bot bought contracts at
# 3-5c -- 97-to-3 longshots -- because "under 50c" is trivially true of 3c. The
# cheapest entry in all 1,297 graded bets is 13c, and NOT ONE is under 10c, so
# those trades extrapolated straight off the end of the tested range.
#
# 20c is inside the measured data with room to spare, and it caps position size
# for free: at 20c a $5 stake buys 25 contracts, where at 3c it bought 165.
MAX_ENTRY = 50.0
MIN_ENTRY = 20.0

# How far above the quoted price we are willing to pay. One cent covers the
# ordinary tick of movement between reading the book and the order landing;
# beyond that we would rather miss the bet.
SLIP_BUFFER_CENTS = 1.0

# How long to wait before re-trying a market the order host has not listed yet.
# The window is 15 minutes and we fire about 35 seconds in, so this spends
# slack we have plenty of.
# Kalshi does not list the current window's market immediately. Measured on
# 17 Sep: the market ending 17:30 was not found at 17:15:35 or at 17:16:50,
# and resolved at 17:19:08 -- roughly three to four minutes in.
#
# Polling faster cannot make it list sooner. What it does is get the order in
# the moment it DOES list, instead of up to a minute later. Every second of
# delay is a second in which the price absorbs more of the move the signal is
# predicting, so the cheapest fill available is the earliest one.
RETRY_WAIT_SECONDS = 15
RETRY_ATTEMPTS = 18         # ~4.5 minutes of polling, well inside the window

# Hard ceiling on how many orders one window may place. Unset means the normal
# rule: bet every qualifying signal. Set to 1 for a live proving run, where the
# question is "does an order actually fill" and not "is the strategy good" --
# one fill answers it, and five cost five times as much to learn the same thing.
#
# This caps ORDERS, not signals. The rest of the window is still evaluated and
# logged as skipped, so the row for a bet we chose not to place is visible
# rather than silently missing.
def _max_bets():
    raw = os.environ.get("LIVE_MAX_BETS", "").strip()
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n > 0 else None


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


def current_entry(ticker, side):
    """What the market costs RIGHT NOW, in cents, for the side we want.

    The quote in the signal row was taken ~35 seconds into the window. By the
    time Kalshi lists the market, minutes later, that number is stale and a
    limit derived from it would simply miss. Re-quoting before each retry is
    what makes the retry worth doing.

    Buying YES pays the ask. Buying NO pays 100 minus the yes bid. Returns
    None if the market still is not there.
    """
    import predictor as P
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}")
        r = requests.get(f"{ORDER_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs or {}, timeout=10)
        if not r.ok:
            return None
        m = r.json().get("market") or {}
    except Exception:
        return None

    def cents(key):
        v = m.get(key)
        try:
            return float(v) * 100.0
        except (TypeError, ValueError):
            return None

    if side == "UP":
        px = cents("yes_ask_dollars") or cents("last_price_dollars")
    else:
        bid = cents("yes_bid_dollars")
        px = (100.0 - bid) if bid is not None else None
        if px is None:
            last = cents("last_price_dollars")
            px = (100.0 - last) if last is not None else None
    return px if (px and 0 < px < 100) else None


# Each market names the exchange it trades on, and they are not all the same:
# the 15-minute crypto markets are on index 2 while other series sit on 0. An
# order sent to the wrong index is looked up on an exchange that does not carry
# that ticker, and comes back market_not_found -- which is literally true and
# reads exactly like a missing market. Hours were spent chasing hosts, paths,
# ticker forms, listing delays and geography because of one hardcoded 0.
#
# So it is READ, never assumed, and a market whose index cannot be read is
# skipped rather than sent with a guess. Guessing is what caused this.
_INDEX_CACHE = {}


def market_exchange_index(ticker):
    """The exchange this market trades on, or None if it cannot be read."""
    if ticker in _INDEX_CACHE:
        return _INDEX_CACHE[ticker]
    import predictor as P
    idx = None
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}")
        r = requests.get(f"{ORDER_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs or {}, timeout=10)
        if r.ok:
            idx = (r.json().get("market") or {}).get("exchange_index")
    except Exception:
        idx = None
    if idx is not None:
        _INDEX_CACHE[ticker] = idx
    return idx


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
    if entry_cents < MIN_ENTRY:
        return ("SKIPPED",
                f"entry {entry_cents:.1f}c is below the {MIN_ENTRY:.0f}c floor "
                f"(cheapest bet in the history is 13c)", 0, 0.0, "")

    if side == "DOWN" and not ALLOW_DOWN:
        return ("SKIPPED",
                "DOWN needs the ask-side mapping confirmed against a real fill",
                0, 0.0, "")

    exch = market_exchange_index(ticker)
    if exch is None:
        return ("SKIPPED", "could not read the market's exchange index",
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
        "exchange_index": exch,
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

    # market_not_found is NOT a crisis. We quote the upcoming market seconds
    # after the window opens, and the order host does not always have it listed
    # yet -- we are racing Kalshi's own market creation. Treating that as a
    # failure halted the whole system over a timing quirk. It gets its own
    # status so the caller can wait and try once more.
    body_txt = r.text[:150]
    if r.status_code == 404 and "market_not_found" in body_txt:
        return "NOTYET", "market not listed yet", contracts, cost, ""

    # Anything else IS unexpected, and the rule for unexpected things
    # involving money is to stop and ask a human.
    return "FAILED", f"HTTP {r.status_code} {body_txt}", contracts, cost, ""


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

    # Apply the ceiling before the funding check, so a capped run only ever
    # reserves what it can actually spend. Cheapest entries win the slots: the
    # <40c bucket is where the measured edge is, so if only one order is going
    # in, it should be the one the evidence likes best.
    capped = set()
    cap = _max_bets()
    if cap is not None and len(betable) > cap:
        keep = {(s[0], s[1]) for s in sorted(betable, key=lambda s: s[4])[:cap]}
        capped = {(s[0], s[1]) for s in betable if (s[0], s[1]) not in keep}
        print(f"  [live] LIVE_MAX_BETS={cap}: placing the {cap} cheapest of "
              f"{len(betable)} qualifying signal(s); the rest are logged as "
              f"skipped.")
        betable = [s for s in betable if (s[0], s[1]) in keep]

    ok, needed, bal, reason = control.check_window(client, len(betable), stake=stake)
    control.record_balance(client, bal)
    if not ok:
        control.halt(client, f"Funding short -- {reason}. No orders were placed.")
        return []

    def attempt(sig):
        ts, symbol, side, ticker, entry = sig
        # A capped-out signal must never reach place(). Checked here rather
        # than at the call sites so the retry loop cannot route around it.
        if (ts, symbol) in capped:
            return [ts, symbol, side, ticker,
                    round(entry, 1) if entry else "", "", "", "",
                    "SKIPPED", "", f"held back by LIVE_MAX_BETS={cap}"]
        status, detail, n, cost, oid = place(ts, symbol, side, ticker, entry, stake)
        return [ts, symbol, side, ticker,
                round(entry, 1) if entry else "",
                math.ceil(entry + SLIP_BUFFER_CENTS) if entry else "",
                n, cost, status, oid, detail]

    rows = [attempt(s_) for s_ in signals]

    # Kalshi lists the window's market a few minutes in, so keep trying across
    # that span -- and RE-QUOTE each time, because a limit built from the
    # opening quote would only miss by the time the market appears.
    for round_no in range(1, RETRY_ATTEMPTS + 1):
        retry = [i for i, r in enumerate(rows) if r[8] == "NOTYET"]
        if not retry:
            break
        if round_no == 1 or round_no % 4 == 0:
            print(f"  [live] {len(retry)} market(s) not listed yet; polling "
                  f"every {RETRY_WAIT_SECONDS}s (attempt {round_no}/"
                  f"{RETRY_ATTEMPTS}).")
        time.sleep(RETRY_WAIT_SECONDS)
        for i in retry:
            ts_, sym_, side_, tk_, entry_ = signals[i]
            fresh = current_entry(tk_, side_)
            if fresh is not None:
                if fresh >= MAX_ENTRY:
                    rows[i][8] = "SKIPPED"
                    rows[i][10] = (f"re-quoted at {fresh:.0f}c, at or above the "
                                   f"{MAX_ENTRY:.0f}c cut")
                    continue
                entry_ = fresh
            rows[i] = attempt((ts_, sym_, side_, tk_, entry_))

    placed, failures = 0, []
    for r in rows:
        if r[8] == "PLACED":
            placed += 1
        elif r[8] == "FAILED":
            failures.append(f"{r[1]}: {r[10]}")
        elif r[8] == "NOTYET":
            # Still not listed after the retry. A bet not placed, like any
            # other skip -- it costs an opportunity, never money.
            r[8], r[10] = "SKIPPED", "market never listed in this window"

    if rows:
        append(client, rows)
    if placed:
        verify_positions()
    print(f"  [live] {placed} order(s) placed, "
          f"{sum(1 for r in rows if r[8]=='SKIPPED')} skipped, {len(failures)} failed.")

    if failures:
        control.halt(client, "Order rejected by Kalshi -- " + "; ".join(failures[:3]))
    return rows


def verify_positions():
    """Read positions back and print what direction they actually are.

    DOWN is sent as an ask on the YES contract, which should open a NO
    position. That is standard but it was inferred, not documented, so every
    window that places something reads the account back and says plainly what
    it got. A mismatch is visible in the log and in the pulse rather than
    discovered weeks later in the P&L.
    """
    import predictor as P
    try:
        hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/positions")
        r = requests.get(f"{ORDER_HOST}/trade-api/v2/portfolio/positions",
                         headers=hdrs or {}, params={"limit": 50}, timeout=15)
        if not r.ok:
            print(f"  [verify] positions unreadable: HTTP {r.status_code}")
            return []
        pos = r.json().get("market_positions") or []
    except Exception as e:
        print(f"  [verify] positions unreadable: {e}")
        return []
    if not pos:
        print("  [verify] no open positions after placing -- orders may be "
              "resting unfilled, which is normal for a limit order.")
        return []
    for p in pos[:10]:
        # A positive position is YES, a negative one is NO, in Kalshi's model.
        q = p.get("position") or p.get("market_exposure") or 0
        try:
            qn = float(q)
        except (TypeError, ValueError):
            qn = 0.0
        kind = "YES (up)" if qn > 0 else ("NO (down)" if qn < 0 else "flat")
        print(f"  [verify] {p.get('ticker','?')}: {q} -> {kind}")
    return pos


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

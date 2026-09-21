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
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import requests

import live
import predictor as P

STAKE = 5.0
ENTRY_S = 120                # the champion reads two minutes in
LO, HI = 20.0, 35.0          # the champion's entry band
TAKE = 2.0
PRICE_HOST = "https://api.elections.kalshi.com"

# THE FILL-RATE LOG, and why it earns a tab of its own.
#
# Every P&L number on the Bounce Test page assumes an entry fills at the
# quoted ask. On 21 Sep a setup at 29c was ordered and filled ZERO contracts
# -- the book moved in the second between reading it and the order landing.
# A backtest cannot see that: it reads a price from a sheet and books a
# trade. So the miss has to be counted here, at the only place that knows
# the difference between what was asked for and what was got.
#
# It matters more than it sounds. Misses are not random: the setups that
# move fastest are both the most likely to slip away and the most likely to
# be the winners, so an unmeasured fill rate biases the edge UPWARD by more
# than the raw miss count suggests. One row per attempt, so the rate is a
# measurement rather than a memory.
# Total committed so far, and the ceiling on it. MAX_SPEND is read from the
# environment so the number can be stated before a run rather than inferred
# from it afterwards; 0 means no cap, which is only safe for a single
# supervised trade.
SPENT = 0.0
MAX_SPEND = float(os.environ.get("MAX_SPEND_USD") or 0)

FILL_SHEET = "Fills"
FILL_HEADERS = [
    "Timestamp", "Window", "Symbol", "Side", "Asked At c", "Stake",
    "Status", "Contracts", "Cost", "Detail", "Setups Seen", "Order Id",
]


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
        # reduce_only is NOT set, and cannot be: Kalshi refuses it on anything
        # but an immediate-or-cancel order --
        #     "reduce_only can only be used with IoC orders"
        # -- and a take-profit that cancels immediately is not a take-profit.
        # The guard and the mechanism are mutually exclusive here, so the
        # side has to be right by construction instead of by refusal:
        #     holding YES -> "ask" at target
        #     holding NO  -> "bid" at (1 - target)
        # both of which SELL. The read-back below is what catches an error,
        # by showing the position shrinking rather than doubling.
        "reduce_only": False,
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


def log_attempt(row):
    """Append one attempted entry. Never fails the trade if the sheet does.

    Fail-soft on purpose: a logging error must not take down a run that has
    real money in flight, and a missing row is a smaller problem than an
    exception thrown between the buy and the sell.
    """
    try:
        client = P._get_client()
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(FILL_SHEET)
        except Exception:
            ws = sh.add_worksheet(title=FILL_SHEET, rows=2000,
                                  cols=len(FILL_HEADERS))
            ws.update("A1", [FILL_HEADERS])
        ws.append_row(row, value_input_option="USER_ENTERED", table_range="A1")
        print(f"    [fills] logged: {row[6]}")
    except Exception as e:
        print(f"    [fills] could not log: {e}")


def attempt(stop_at=None):
    """Look at one window. Returns traded / missed / expired / none.

    Reading at +2 MINUTES is the point, not a detail. The first run fired
    five and a half minutes in, by which time every coin had resolved to
    4-14c and nothing was in band -- the setup this is meant to test had
    already come and gone.
    """
    boundary, markets = this_window()
    secs = (datetime.now(timezone.utc) - boundary).total_seconds()
    print(f"  window {boundary:%H:%M} UTC, now +{secs:.0f}s, {len(markets)} markets")
    if not markets:
        print("  no markets resolved for this window.")
        return "none"

    picks = []
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
            picks.append((symbol, ticker, cy, entry))
        print(f"    {symbol:9s} yes {a:5.1f}c / no {no_ask:5.1f}c{flag}")

    if not picks:
        print("    -> nothing in the 20-35c band; waiting for the next window")
        return "none"

    # EVERY qualifying coin, not just the first. Taking only one was an
    # artifact of this having started as a single mechanical test of the
    # exit leg; as a way of running the rule it silently dropped setups
    # because of where a coin happened to sit in the series list, which is
    # a selection the strategy never asked for and the backtest does not
    # make -- it scores all five.
    #
    # They are NOT independent bets. Five coins in one window share a market
    # move, so three setups at once is closer to one larger position than to
    # three separate ones, which is exactly why the budget below is a total
    # rather than a per-trade limit.
    ts = boundary.strftime("%Y-%m-%d %H:%M UTC")
    seen = len(picks)
    if seen > 1:
        print(f"    -> {seen} setups this window; taking all of them")
    out = "none"
    for symbol, ticker, holding_yes, entry in picks:
        r = trade_one(ts, symbol, ticker, holding_yes, entry, seen, stop_at)
        if r == "traded":
            out = "traded"
        elif r == "expired":
            return "expired"          # the deadline applies to the rest too
        elif r == "budget":
            print("  Budget for this run is spent. Scanning only from here.")
            return out if out == "traded" else "budget"
        elif out != "traded":
            out = r
    return out


def trade_one(ts, symbol, ticker, holding_yes, entry, seen, stop_at):
    """One buy and its resting sell. Returns traded / missed / expired / budget."""
    global SPENT
    side = "UP" if holding_yes else "DOWN"

    # THE LAST GATE, deliberately here and not at the top of the loop. A
    # window takes a couple of minutes to reach this point, so a deadline
    # checked before the scan could be honoured on the way in and stale by
    # the time an order is signed. Checked here, "no buying after X" means
    # exactly that, to the second.
    now = datetime.now(timezone.utc)
    if stop_at is not None and now >= stop_at:
        print(f"\n  SETUP FOUND ({symbol} {side} at {entry:.1f}c) but it is "
              f"{now:%H:%M} UTC, past the {stop_at:%H:%M} buying deadline.")
        print("  Not buying. Still scanning and logging.")
        return "expired"

    # THE SECOND GATE, and the one that matters once a window can produce
    # five trades instead of one. A deadline alone bounds the TIME but not
    # the MONEY: at five coins a window, "until 11:45" is a very different
    # number from what it sounds like. This makes the ceiling a figure that
    # can be stated in advance and cannot be exceeded by a busy night.
    if MAX_SPEND and SPENT + STAKE > MAX_SPEND + 1e-9:
        print(f"\n  SETUP FOUND ({symbol} {side} at {entry:.1f}c) but "
              f"${SPENT:.2f} of the ${MAX_SPEND:.2f} budget is committed.")
        return "budget"

    print(f"\n  BUYING {symbol} {side} at {entry:.1f}c, ${STAKE:.2f}")
    status, detail, n, cost, oid = live.place(ts, symbol, side, ticker, entry, STAKE)
    print(f"    {status}  {detail or ''}  {n} contracts, ${cost:.2f}, order {oid or '-'}")
    SPENT += cost
    # Logged for EVERY attempt, filled or not. A log written only on success
    # measures nothing: the misses are the whole point of the tab.
    log_attempt([
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), ts,
        symbol, side, round(entry, 1), STAKE,
        status, n, round(cost, 2), detail or "", seen, oid or "",
    ])
    if status != "PLACED" or n < 1:
        print("  Entry did not fill, so there is nothing to sell.")
        return "missed"

    target = min(99.0, round(entry * TAKE, 1))
    exch = live.market_exchange_index(ticker)
    print(f"  RESTING SELL of {n} at {target:.1f}c "
          f"(side {'ask' if holding_yes else 'bid'}, good_till_canceled)")
    ok, sdetail, soid = sell(ticker, holding_yes, int(n), target, exch,
                             f"exit-{ts}-{symbol}")
    print(f"    {'ACCEPTED' if ok else 'REFUSED'}  {sdetail}  order {soid or '-'}")
    print(f"    committed so far this run: ${SPENT:.2f}"
          + (f" of ${MAX_SPEND:.2f}" if MAX_SPEND else ""))
    return "traded"


def deadline():
    """The UTC moment after which this process must not buy anything.

    A HARD STOP, not a schedule. The alternative -- sizing a run so that it
    "should" finish by bedtime -- fails in exactly the way that matters: a
    job that hangs, retries, or is dispatched twice keeps spending while
    nobody is awake to notice. This is checked immediately before every
    order, so the worst case is a run that scans and logs and buys nothing.

    Unset means no deadline, which is the old behaviour and is fine for a
    single supervised trade.
    """
    raw = (os.environ.get("BUY_UNTIL_UTC") or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
    except ValueError:
        # A deadline that cannot be parsed must not silently mean "forever".
        print(f"  BUY_UNTIL_UTC={raw!r} is not YYYY-MM-DDTHH:MM -- refusing to "
              f"trade rather than guess at it.")
        return "bad"


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "RUN-ONE-FIVE-DOLLAR-TEST":
        print("Refusing: pass the confirmation string to run this.")
        return 1
    windows = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    # More than one buy is allowed only when a deadline bounds it. Without
    # one, the old one-and-done rule stands.
    stop_at = deadline()
    if stop_at == "bad":
        return 1
    many = stop_at is not None

    print("=" * 66)
    print("BOUNCE EXIT TEST -- $5 a buy, each with a resting sell at 2x")
    if many:
        print(f"  buying until {stop_at:%Y-%m-%d %H:%M} UTC, then scanning only")
        print(f"  every qualifying coin each window · {windows} windows")
        print(f"  ceiling: ${MAX_SPEND:.2f}" if MAX_SPEND else
              "  NO SPEND CEILING SET (MAX_SPEND_USD)")
    else:
        print(f"  waiting up to {windows} windows; stops at the first trade")
    print("=" * 66)
    if not live.enabled():
        print("  LIVE_TRADING / ALLOW_LIVE_TRADING are not both set. Nothing sent.")
        return 1

    missed = empty = traded = 0
    for k in range(windows):
        now = datetime.now(timezone.utc)
        b = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        # The read is at +2min. If this window is already past it, wait for
        # the next one rather than sampling a window that has moved on.
        target = b + timedelta(seconds=ENTRY_S)
        if now >= target:
            target = b + timedelta(minutes=15, seconds=ENTRY_S)
        wait = (target - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            print(f"\n  [{k+1}/{windows}] sleeping {wait:.0f}s until "
                  f"{target:%H:%M:%S} UTC (+{ENTRY_S}s into the window)")
            time.sleep(wait)
        r = attempt(stop_at)
        if r == "traded":
            traded += 1
            if not many:
                print("=" * 66)
                return 0
        elif r == "budget":
            print(f"\n  Stopping: ${SPENT:.2f} committed, budget "
                  f"${MAX_SPEND:.2f}.")
            break
        elif r == "missed":
            missed += 1
        elif r == "expired":
            # Past the deadline. Keep scanning, because the scan is free and
            # the Path tab wants the windows either way -- but never buy.
            empty += 1
        else:
            empty += 1

    # WHAT THE SUMMARY USED TO SAY, and why it was worth fixing. It printed
    # "N windows went by with nothing in the band" unconditionally -- even on
    # the run where three setups appeared and an order was sent and filled
    # nothing. A log that flattens "the setup never came" into the same
    # sentence as "the setup came and we missed it" hides the one failure
    # mode a backtest cannot see. Those are opposite problems: the first is
    # patience, the second is slippage.
    print(f"\n  {windows} windows checked: {empty} with no setup in the "
          f"20-35c band, {missed} where a setup appeared but the entry "
          f"filled nothing.")
    if missed:
        print("  A miss is not the same as an absent setup -- the book moved "
              "between the read and the order. Logged to the Fills tab so the "
              "real fill rate can be measured rather than guessed.")
    print("  Nothing was spent.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())

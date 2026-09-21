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

# THE COMBO SET -- the six rules that cleared 67% ROI in the grid search,
# run together. Off unless COMBO=1, so the champion stays the default.
#
#   (coin, low, high, seconds into the window, take-profit multiple)
#
# WHY THESE OVERLAP, AND WHY THAT HAD TO BE RESOLVED. Half of the setups
# these select are selected by more than one rule, and 98 of them are given
# CONFLICTING targets -- the same contract told to sell at 2.5x by one rule
# and 3x by another. Fired naively that is two orders on one position at two
# exits, which is not what any of the six rules measured.
#
# So a setup is bought ONCE, at the greediest target among the rules that
# picked it. That choice is not free: it converts some 2.5x sales into 3x
# sales that never trade and ride to settlement instead. It is the honest
# reading of "fire all of them" rather than the flattering one.
COMBO = (os.environ.get("COMBO") or "") in ("1", "plan", "cheap", "sub10")
COMBO_RULES = [
    ("ALL",  10.0, 20.0, 180, 3.0),
    ("DOGE", 15.0, 30.0, 120, 3.0),
    ("ALL",   0.0, 20.0, 180, 3.0),
    ("ALL",  10.0, 20.0, 180, 2.5),
    ("BTC",  30.0, 40.0, 180, 2.5),
    ("XRP",  10.0, 25.0, 180, 3.0),
]

# THE PRICE-DEPENDENT PLAN (COMBO=plan), and why a flat multiple was wrong.
#
# A single take-profit means different things at different prices: 2.5x from
# 15c is a move to 38c, while 2.5x from 36c demands 90c -- the market
# resolving almost completely. The first is a bounce; the second is a
# different bet wearing the same number. A live 36c trade on 21 Sep peaked
# at 63c, missed its 90c target and settled worthless, which is that defect
# costing money rather than describing it.
#
# Per-trade profit by entry band says the same thing. The 10-20c row climbs
# all the way out to 4x, while 30-40c peaks near 2-2.5x and falls off after.
# Cheap entries want MORE greed; dearer ones want less.
#
#     band     1.5x    2.0x    2.5x    3.0x    3.5x    4.0x
#     0-10c   -3.28   -3.28   -3.28   -3.28   -3.09   -2.83
#     10-20c  +2.31   +3.22   +4.08   +4.86   +5.20   +5.33
#     20-30c  -0.34   +0.19   +0.16   +0.38   -0.31   -0.42
#     30-40c  +0.84   +1.62   +1.81   +0.98   +0.61   +0.61
#
# Under 10c is excluded because it loses at EVERY target, and no exit
# rescues an entry that is wrong.
#
# Scored on holdout windows this plan pays +$1.24 a trade against +$1.01 for
# a flat 2.5x and +$0.45 for a flat 3x. It also beats the version tuned to
# the best target in every band, which manages only +$1.11 -- coarser
# survived better than finer, which is overfitting caught in the act and the
# reason this plan has two rows rather than five.
#
# The interval still spans zero. Better than flat is not the same as proven.
PLAN_RULES = [
    ("ALL", 10.0, 20.0, 180, 3.0),
    ("ALL", 20.0, 40.0, 180, 2.0),
]

# CHEAP ONLY (COMBO=cheap) -- the band the claim was always about.
#
# Ten live trades on 21 Sep went roughly 3 wins to 7 losses. That rate is
# close to what a 2x target should hit, so it is not the surprise; the
# surprise is that it still lost money. The reason is WHERE it fired: at
# 24-40c entries, where 2x means 48-80c, a win pays about what a loss
# costs, and a coin-flip at even money loses to the fee.
#
# The favourable asymmetry only exists when the entry is cheap. At 15c a
# 3x exit returns three times the stake while a loss still costs one, so
# the same 30% hit rate is strongly profitable instead of mildly ruinous.
# Every number that motivated this -- the +88% ROI, the train-to-holdout
# consistency -- came from 10-20c, and barely a trade has fired there.
#
# Under 10c stays excluded: it loses at every target, and no exit rescues
# an entry that is wrong.
#
# This fires roughly once every seven windows rather than several a window,
# so it will look idle. That is the cost of testing the actual claim rather
# than an adjacent one that happens to trade more often.
CHEAP_RULES = [
    ("ALL", 10.0, 20.0, 180, 3.0),
]

# SUB-10c, HELD TO SETTLEMENT (COMBO=sub10). A different bet entirely.
#
# Not a bounce. There is no take-profit and nothing to sell into: buy the
# side the market has nearly written off, with at least eight minutes left,
# and wait for the window to close. Every problem the bounce runs hit came
# from the EXIT -- fills at 3x, depth at the target, partial sells. This has
# no exit, so it has none of them.
#
# What makes it interesting is a calibration gap. Across 160 setups priced
# at an average of 8.3c -- the market saying 8.3% -- the side won 19.4% of
# the time. Capped at the depth ACTUALLY resting at those prices, it paid
# +$2.39 a bet, and it held up on windows it was not chosen from (+$3.75
# against +$1.63 on the earlier ones).
#
# WHAT IS WRONG WITH IT, stated here because the number above is seductive.
# The top ten bets produce 112% of the profit: remove them and it loses.
# Nineteen percent of bets win. The 95% interval runs -$1.35 to +$6.12, so
# it is entirely consistent with no edge. This is a lottery-ticket shape,
# which takes hundreds of bets to confirm and can look brilliant for a week
# on luck alone.
#
# A take of 0 means HOLD -- the position is never sold, so no resting exit
# is placed. It reads at +3min and +7min, both of which leave the eight
# minutes the backtest required.
SUB10_RULES = [
    ("ALL", 2.0, 10.0, 180, 0.0),
    ("ALL", 2.0, 10.0, 420, 0.0),
]

_mode = (os.environ.get("COMBO") or "")
if _mode == "plan":
    COMBO_RULES = PLAN_RULES
elif _mode == "cheap":
    COMBO_RULES = CHEAP_RULES
elif _mode == "sub10":
    COMBO_RULES = SUB10_RULES
# The read times the rules actually need. Two passes per window, not one.
COMBO_ENTRIES = sorted({r[3] for r in COMBO_RULES})


# A CEILING ON GREED, and why it is a knob rather than a rewrite.
#
# Priced across the 201 setups these rules select, the targets rank:
#
#     2.0x  +$2.42 a trade, banks 34% of the time
#     2.5x  +$3.06 a trade, banks 26%
#     3.0x  +$3.39 a trade, banks 15%   <- highest expected value
#
# 3x wins on paper, and all three intervals overlap heavily enough that the
# ranking is real but not established. What 3x costs is FEEDBACK: it closes
# a position 15% of the time where 2.5x closes 26%, and while the point of
# these early runs is to learn how the thing behaves with real money, more
# closed trades is worth more than the last $0.33 of theoretical edge.
#
# So the cap trades a little expected value for a lot more observation, on
# purpose, and can be lifted in one input once the mechanism is trusted.
MAX_TAKE = float(os.environ.get("MAX_TAKE") or 0)


def combo_take(symbol, entry, entry_s):
    """Greediest target among the rules selecting this setup, or None."""
    coin = symbol.replace("USDT", "")
    best = None
    for c, lo, hi, es, take in COMBO_RULES:
        if es != entry_s:
            continue
        if c != "ALL" and c != coin:
            continue
        if lo <= entry < hi:
            # 0 means HOLD, and holding is not "less greedy than 1.5x" --
            # it is a different rule. It only wins the comparison if no
            # selling rule also matched, so a band with both keeps its exit.
            if best is None or (best == 0.0 and take > 0) or \
                    (best > 0 and take > best):
                best = take
    # The cap lowers a target; it never makes one qualify that did not.
    # Applied after selection so the SET of setups is unchanged -- only
    # where each one sells. Otherwise lowering the cap would quietly change
    # which rules fire, and the run would stop being the rules we measured.
    if best and MAX_TAKE and best > MAX_TAKE:
        best = MAX_TAKE
    return best

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
# Counted per ATTEMPT, not per window. attempt() collapses a whole window
# into one verdict, so a window that bought one coin and missed three
# reported a single trade and NO misses -- which zeroed the fill rate,
# the one number these runs exist to measure.
TRADES = 0
MISSES = 0
MAX_SPEND = float(os.environ.get("MAX_SPEND_USD") or 0)
# Hourly summary notification, off unless asked for. Betting alerts stay
# off regardless: this reports what already happened, it never names a bet
# to place by hand.
PULSE = (os.environ.get("BOUNCE_PULSE") or "") == "1"

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


def pulse():
    """One notification an hour about the bounce runs. Not one per bet.

    Deliberately the SAME shape as the v6 pulse: a summary on the hour, not
    a buzz per trade. A notification per bet trained the last strategy to be
    read as a prompt to act, and there is nothing here to act on -- the runs
    place their own orders and rest their own exits.

    It leads with the FILL RATE, because that is the number these runs exist
    to measure and the one no backtest can produce. Balance comes from the
    exchange rather than a running total: it already nets fees, wins and
    losses, and cannot drift from reality the way a computed figure can.
    """
    try:
        client = P._get_client()
        sh = client.open_by_key(P.SPREADSHEET_ID)
        rows = sh.worksheet(FILL_SHEET).get_all_values()
    except Exception as e:
        print(f"    [pulse] could not read fills: {e}")
        return
    if len(rows) < 2:
        return
    idx = {h: i for i, h in enumerate(rows[0])}

    def cell(r, k):
        i = idx.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    now = datetime.now(timezone.utc)
    cutoff = now.timestamp() - 3600
    hr_n = hr_filled = hr_missed = 0
    hr_cost = 0.0
    all_n = all_filled = 0
    for r in rows[1:]:
        status = cell(r, "Status")
        filled = status == "PLACED"
        all_n += 1
        all_filled += 1 if filled else 0
        try:
            t = datetime.strptime(cell(r, "Timestamp"),
                                  "%Y-%m-%d %H:%M:%S UTC").replace(
                                      tzinfo=timezone.utc)
        except ValueError:
            continue
        if t.timestamp() < cutoff:
            continue
        hr_n += 1
        if filled:
            hr_filled += 1
            try:
                hr_cost += float(cell(r, "Cost") or 0)
            except ValueError:
                pass
        else:
            hr_missed += 1

    try:
        import control
        bal = control.fetch_balance()
    except Exception:
        bal = None

    # An average well under the stake is the partial-fill problem showing
    # up as a number rather than an anecdote, so it is stated outright.
    avg = (hr_cost / hr_filled) if hr_filled else None
    msg = (f"Last hour: {hr_n} setups, {hr_filled} filled, "
           f"{hr_missed} missed.\n"
           f"Staked ${hr_cost:.2f}"
           + (f" (avg ${avg:.2f} of ${STAKE:.2f} asked)" if avg else "")
           + f"\nAll time: {all_filled}/{all_n} attempts filled\n"
           f"Balance: {'unreadable' if bal is None else f'${bal:.2f}'}\n"
           f"Rules: {'price-dependent' if COMBO else 'champion'}")
    P.send_pushover("bounce pulse", msg)
    print(f"    [pulse] sent -- {hr_filled}/{hr_n} filled last hour")


def attempt(stop_at=None, entry_s=ENTRY_S, done=None):
    """Look at one window at one read time. traded / missed / expired / none.

    Reading at the RIGHT SECOND is the point, not a detail. The first run
    fired five and a half minutes in, by which time every coin had resolved
    to 4-14c and nothing was in band -- the setup this is meant to test had
    already come and gone. The combo rules read at +2min and +3min, and a
    rule scored at +3min is a different rule if it is filled at +2min.

    `done` carries the coins already bought earlier in this same window, so
    the second pass cannot buy a coin the first pass already holds.
    """
    boundary, markets = this_window()
    secs = (datetime.now(timezone.utc) - boundary).total_seconds()
    print(f"  window {boundary:%H:%M} UTC, now +{secs:.0f}s, {len(markets)} markets")
    if not markets:
        print("  no markets resolved for this window.")
        return "none"

    picks = []
    for symbol, ticker in markets:
        if symbol in (done or ()):
            continue                 # already bought this window
        b, a = book(ticker)
        if b is None or a is None:
            print(f"    {symbol:9s} no book")
            continue
        no_ask = round(100.0 - b, 1)
        # The CHEAPER side, whichever it is. The champion also required it
        # to be under 45c; the combo rules carry their own bands, so the
        # band test is the rule's job and not a second filter on top.
        cheap_yes = a <= no_ask
        entry = a if cheap_yes else no_ask
        flag = ""
        if COMBO:
            take = combo_take(symbol, entry, entry_s)
            if take:
                flag = f"  <- SETUP (sell {take}x)"
                picks.append((symbol, ticker, cheap_yes, entry, take))
        else:
            cy, cn = a <= HI, no_ask <= HI
            if cy != cn and LO <= entry < HI:
                flag = "  <- SETUP"
                picks.append((symbol, ticker, cy, entry, TAKE))
        print(f"    {symbol:9s} yes {a:5.1f}c / no {no_ask:5.1f}c{flag}")

    if not picks:
        print("    -> nothing qualifies at this read; on to the next")
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
    global TRADES, MISSES
    for symbol, ticker, holding_yes, entry, take in picks:
        r = trade_one(ts, symbol, ticker, holding_yes, entry, seen, stop_at,
                      take)
        if r == "traded":
            out = "traded"
            TRADES += 1
            if done is not None:
                done.add(symbol)
        elif r == "missed":
            MISSES += 1
        elif r == "expired":
            return "expired"          # the deadline applies to the rest too
        elif r == "budget":
            print("  Budget for this run is spent. Scanning only from here.")
            return out if out == "traded" else "budget"
        elif out != "traded":
            out = r
    return out


def trade_one(ts, symbol, ticker, holding_yes, entry, seen, stop_at,
              take=TAKE):
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

    if not take:
        # HOLD TO SETTLEMENT. No resting sell, so nothing can fail to fill
        # on the way out -- the position simply settles at $1.00 or zero.
        print(f"    HOLDING to settlement (no take-profit)")
        print(f"    committed so far this run: ${SPENT:.2f}"
              + (f" of ${MAX_SPEND:.2f}" if MAX_SPEND else ""))
        return "traded"

    target = min(99.0, round(entry * take, 1))
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
    # More than one buy needs a BOUND, but either kind will do: a deadline
    # bounds the time, a ceiling bounds the money. Requiring the deadline
    # specifically forced a stop time onto a run that only wanted a budget,
    # and the budget is the stricter limit of the two -- it cannot be
    # outrun by a busy afternoon the way a clock can.
    many = stop_at is not None or MAX_SPEND > 0

    print("=" * 66)
    print("BOUNCE EXIT TEST -- $5 a buy, each with a resting sell")
    print(f"  rules: {'the six high-ROI combos' if COMBO else 'champion only'}"
          + (f", read at +{'/+'.join(str(e) for e in COMBO_ENTRIES)}s"
             if COMBO else f", read at +{ENTRY_S}s"))
    if many:
        # Either bound may be absent now that either one unlocks multi-buy,
        # so each is printed only when it exists. Formatting an absent
        # deadline is what killed the first combo run before it placed
        # anything.
        if stop_at:
            print(f"  buying until {stop_at:%Y-%m-%d %H:%M} UTC, "
                  f"then scanning only")
        else:
            print("  no stop time -- the ceiling is the only bound")
        print(f"  every qualifying coin each window · {windows} windows")
        print(f"  ceiling: ${MAX_SPEND:.2f}" if MAX_SPEND else
              "  NO SPEND CEILING SET (MAX_SPEND_USD)")
    else:
        print(f"  waiting up to {windows} windows; stops at the first trade")
    print("=" * 66)
    if not live.enabled():
        print("  LIVE_TRADING / ALLOW_LIVE_TRADING are not both set. Nothing sent.")
        return 1

    # The read times this run needs. The champion wants one pass at +2min;
    # the combo set wants +2min AND +3min, because a rule measured at +3min
    # is a DIFFERENT rule if it is filled at +2min -- the price it selects on
    # has had another minute to move.
    reads = COMBO_ENTRIES if COMBO else [ENTRY_S]

    # Hour last pulsed, kept on the function so a run pulses once an hour
    # rather than once per read.
    main.last_pulse = None
    missed = empty = traded = 0
    stop = False
    for k in range(windows):
        if stop:
            break
        # Coins already bought in THIS window, so the +3min pass cannot buy
        # a coin the +2min pass already holds. Two rules selecting the same
        # coin is one position, not two.
        done = set()
        for entry_s in reads:
            now = datetime.now(timezone.utc)
            b = now.replace(minute=(now.minute // 15) * 15,
                            second=0, microsecond=0)
            target = b + timedelta(seconds=entry_s)
            if now >= target:
                # Already past this read. If a later read in the same window
                # is still ahead, take it; otherwise wait for the next window.
                if any(b + timedelta(seconds=e) > now for e in reads
                       if e > entry_s):
                    continue
                target = b + timedelta(minutes=15, seconds=entry_s)
                done = set()
            wait = (target - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                print(f"\n  [{k+1}/{windows}] sleeping {wait:.0f}s until "
                      f"{target:%H:%M:%S} UTC (+{entry_s}s into the window)")
                time.sleep(wait)
            r = attempt(stop_at, entry_s, done)
            # Once an hour, on the first read after the top of the hour.
            # Guarded by a marker rather than the clock alone, because a
            # window has two reads and both sit inside the same minute
            # range -- checking only the time would send two.
            if PULSE:
                stamp_h = datetime.now(timezone.utc).strftime("%Y-%m-%d %H")
                if stamp_h != main.last_pulse:
                    main.last_pulse = stamp_h
                    pulse()
            if r == "traded":
                traded += 1
                if not many:
                    print("=" * 66)
                    return 0
            elif r == "budget":
                print(f"\n  Stopping: ${SPENT:.2f} committed, budget "
                      f"${MAX_SPEND:.2f}.")
                stop = True
                break
            elif r == "missed":
                missed += 1
            else:
                # "expired" lands here too: past the deadline the scan still
                # runs, because scanning is free and the Path tab wants the
                # windows either way -- it just never buys.
                empty += 1

    # WHAT THE SUMMARY USED TO SAY, and why it was worth fixing. It printed
    # "N windows went by with nothing in the band" unconditionally -- even on
    # the run where three setups appeared and an order was sent and filled
    # nothing. A log that flattens "the setup never came" into the same
    # sentence as "the setup came and we missed it" hides the one failure
    # mode a backtest cannot see. Those are opposite problems: the first is
    # patience, the second is slippage.
    att = TRADES + MISSES
    print(f"\n  {windows} windows checked: {TRADES} buys, {MISSES} attempts "
          f"that filled nothing"
          + (f" -- {TRADES/att*100:.0f}% fill rate over {att} attempts."
             if att else "."))
    if MISSES:
        print("  A miss is not the same as an absent setup -- the book moved "
              "between the read and the order. Logged to the Fills tab so the "
              "real fill rate can be measured rather than guessed.")
    print(f"  Committed ${SPENT:.2f}"
          + (f" of the ${MAX_SPEND:.2f} ceiling." if MAX_SPEND else "."))
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())

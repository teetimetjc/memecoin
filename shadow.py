"""Shadow execution guard: what the candidate rules WOULD have done.

IT NEVER PLACES, BLOCKS, DELAYS OR CANCELS AN ORDER. It does not call
live.place, does not touch live.ORDERED, and writes only to its own tab.
Every entry point is wrapped so that a failure here cannot reach the bot:
the worst case is a missing shadow row.

WHY IT RUNS AFTER THE ORDERS, NOT BEFORE -- AND WHAT THAT COSTS.
Depth lives in the orderbook endpoint, which the live path does not call.
Calling it BEFORE submission would put an extra HTTP round trip between the
decision and the order, which is exactly the interval the whole slippage
question is about: more time there means more chance the ask moves, which
would change real fills. That is a live impact, and this module exists on
the condition of having none.

So the book is read immediately AFTER the window's orders are sent, and the
depth it records is therefore POST-submission. Read it accordingly:

  - For a signal that was NOT bet (skipped, capped, out of band), nothing was
    sent, so the reading is a clean pre-trade observation.
  - For a signal that WAS bet, the reading is the book as it stands a second
    or two after our own order consumed part of it. It UNDERSTATES the depth
    that was available, and the understatement is largest exactly when our
    order was largest.

That second case cannot be fixed without accepting live impact. It is
recorded rather than papered over, and the `when` column says which kind of
reading each row is. Depth conclusions drawn from bet rows are therefore
lower bounds, not measurements.

WHAT IS A CLEAN MEASUREMENT HERE: spread, crossed/invalid books, the
executable ask after the fact, and -- from the new Live Bets columns, not
from this file -- quote age and the pre-submission ask. Those have no such
caveat.

NOTHING HERE DECIDES ANYTHING. The rules below are evaluated and logged with
their reasons so they can be tested out of sample later. Adopting one is a
separate, deliberate change to live.py that a human has to make.
"""
import os
import time

import requests

SHADOW_SHEET = "Shadow"

HEADERS = [
    "Timestamp", "Symbol", "Side", "Ticker", "Entry ¢", "Status",
    "Contracts", "When", "Book ¢ bid", "Book ¢ ask", "Spread ¢",
    "Ask depth", "Levels to fill", "Crossed",
    # One column per candidate rule: ALLOW, REJECT:<reason>, or "" when the
    # rule could not be evaluated. Separate columns rather than one verdict,
    # because the point is to compare rules independently.
    "R_spread", "R_depth", "R_crossed", "R_askmove",
    # THE ABORT MEASUREMENT. What the position could have been sold back for,
    # seconds after it was bought. Buying cannot be undone -- a buy limit has
    # no floor, so a collapsing book hands over a cheap fill no matter what
    # was asked for -- but the position does not have to be kept.
    #
    # The comparison this feeds: a fill well below quote wins about 71% of
    # the time, so HOLDING one is worth roughly 71c per contract. If the
    # position can be sold back for more than that after fees, aborting beats
    # holding. That number has only ever been estimated; these columns
    # measure it.
    #
    # Selling a YES position means hitting the yes BID, which is the side of
    # the book that falls first in a collapse, so the honest expectation is
    # that it is worse than the naive guess.
    "Abort bid ¢", "Abort fee ¢", "Abort net ¢",
    "Note",
]

# CANDIDATE THRESHOLDS, NOT RECOMMENDATIONS. Each is a starting point for a
# sweep; none has been validated. The live bot uses none of them.
MAX_SPREAD_C = float(os.environ.get("SHADOW_MAX_SPREAD_C") or 3.0)
MIN_DEPTH_MULT = float(os.environ.get("SHADOW_MIN_DEPTH_MULT") or 2.0)
MAX_ASK_MOVE_C = float(os.environ.get("SHADOW_MAX_ASK_MOVE_C") or 5.0)
BOOK_TIMEOUT_S = 4
MAX_BOOKS_PER_WINDOW = 8        # bounds the cost of a busy window


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def orderbook(ticker):
    """Top levels of the real book, or None. Read-only, never retried."""
    import predictor as P
    import live
    try:
        path = f"/trade-api/v2/markets/{ticker}/orderbook"
        hdrs = P._kalshi_headers("GET", path)
        r = requests.get(f"{live.ORDER_HOST}{path}", headers=hdrs or {},
                         timeout=BOOK_TIMEOUT_S)
        if not r.ok:
            return None
        j = r.json() or {}
        # THE KEY IS orderbook_fp. Reading .get("orderbook") returned None on
        # all 56 attempts, so every depth, spread and abort-bid field came
        # back blank and the measurement built to answer the abort question
        # produced nothing for two days. Fourth instance of this same family:
        # volume_fp, open_interest_fp, volume_fp on candles, now this one.
        return j.get("orderbook_fp") or j.get("orderbook") or None
    except Exception:
        return None


def _price_size(row):
    """(price_in_cents, size) from one book level, or (None, 0).

    UNIT BY VALUE, NOT BY ASSUMPTION, and only because the field name cannot
    settle it here: the key says dollars, but every other price in this API
    appears in both spellings somewhere, and the probe could only sample an
    EMPTY book so the element shape was never confirmed on real data. A value
    at or below 1.0 is dollars; above that it is already cents. The ambiguous
    case -- exactly 1.00 -- is a 100c contract, which cannot trade, so
    nothing real is lost either way.
    """
    try:
        p, sz = float(row[0]), float(row[1])
    except (TypeError, ValueError, IndexError):
        return None, 0.0
    return (p * 100.0 if p <= 1.0 else p), sz


def _levels(ob, side):
    """[(price_cents, size)] for the side we would BUY, best price first.

    Kalshi keeps ONE book. The yes side's asks are the no side's bids
    mirrored, so a yes ask at 93c is a no bid at 7c. Getting this backwards
    would report the depth of the side we are not buying, which is the kind
    of mistake that looks like data.
    """
    if not isinstance(ob, dict):
        return []
    # ...and the inner keys are yes_dollars / no_dollars, in DOLLARS.
    yes = ob.get("yes_dollars") or ob.get("yes") or []
    no = ob.get("no_dollars") or ob.get("no") or []
    out = []
    if side == "UP":
        # Buying YES lifts the yes ask = 100 - (no bid).
        for row in no:
            p, sz = _price_size(row)
            if p is None:
                continue
            out.append((100.0 - p, sz))
        out.sort(key=lambda t: t[0])          # cheapest ask first
    else:
        for row in yes:
            p, sz = _price_size(row)
            if p is None:
                continue
            out.append((100.0 - p, sz))
        out.sort(key=lambda t: t[0])
    return out


def _best(ob, side):
    """(bid_c, ask_c) for the side being bought, or (None, None)."""
    asks = _levels(ob, side)
    bids = _levels(ob, "DOWN" if side == "UP" else "UP")
    ask = asks[0][0] if asks else None
    # The other side's cheapest buy is 100 minus our best bid.
    bid = (100.0 - bids[0][0]) if bids else None
    return bid, ask


def evaluate(ticker, side, entry_c, contracts, status, ob):
    """Every candidate rule, independently. Returns a dict of verdicts."""
    v = dict(R_spread="", R_depth="", R_crossed="", R_askmove="")
    if ob is None:
        return v, None, None, None, None, ""
    bid, ask = _best(ob, side)
    levels = _levels(ob, side)
    depth = sum(sz for _p, sz in levels[:1]) if levels else None
    spread = (ask - bid) if (ask is not None and bid is not None) else None

    # How many levels an order of this size would have to eat.
    need, lv = float(contracts or 0), 0
    for _p, sz in levels:
        if need <= 0:
            break
        need -= sz
        lv += 1
    levels_to_fill = lv if contracts else ""

    crossed = (bid is not None and ask is not None and bid >= ask)
    v["R_crossed"] = "REJECT:crossed or inverted book" if crossed else "ALLOW"

    if spread is None:
        v["R_spread"] = ""
    else:
        v["R_spread"] = ("REJECT:spread %.0fc > %.0fc" % (spread, MAX_SPREAD_C)
                         if spread > MAX_SPREAD_C else "ALLOW")

    if depth is None or not contracts:
        v["R_depth"] = ""
    else:
        want = MIN_DEPTH_MULT * float(contracts)
        v["R_depth"] = ("REJECT:top-of-book %.0f < %.0f wanted" % (depth, want)
                        if depth < want else "ALLOW")

    if ask is None or entry_c is None:
        v["R_askmove"] = ""
    else:
        drop = entry_c - ask
        v["R_askmove"] = ("REJECT:ask %.1fc below entry" % drop
                          if drop > MAX_ASK_MOVE_C else "ALLOW")

    return v, bid, ask, spread, depth, levels_to_fill


def observe(client, signals, rows, stake):
    """Log the counterfactual for one window. Never raises."""
    try:
        import predictor as P
        by_ticker = {}
        for r in rows or []:
            if len(r) > 3:
                by_ticker[str(r[3])] = r
        out, books = [], 0
        for sig in signals or []:
            try:
                ts, symbol, side, ticker, entry = sig[:5]
            except (ValueError, TypeError):
                continue
            if not ticker:
                continue
            row = by_ticker.get(str(ticker))
            status = str(row[8]) if row and len(row) > 8 else "NOSIGNALROW"
            contracts = (row[6] if row and len(row) > 6 else 0) or 0
            try:
                contracts = int(contracts)
            except (TypeError, ValueError):
                contracts = 0
            if books >= MAX_BOOKS_PER_WINDOW:
                break
            ob = orderbook(ticker)
            books += 1
            when = ("post-submission (our order already took size)"
                    if status == "PLACED" else "pre-trade (nothing was sent)")
            v, bid, ask, spread, depth, lv = evaluate(
                ticker, side, entry, contracts, status, ob)

            # What a sell-back would fetch right now, for bets that were
            # actually filled. Selling YES hits the yes bid; selling a NO
            # position means buying YES back, which pays the ask.
            abort_bid = abort_fee = abort_net = ""
            if status == "PLACED" and contracts:
                px = bid if side == "UP" else (
                    (100.0 - ask) if ask is not None else None)
                if px is not None and 0 < px < 100:
                    pp = px / 100.0
                    # The exit is charged like the entry: 0.07*n*p*(1-p),
                    # verified against 162 of 163 real fills.
                    fee_c = 100.0 * (0.07 * contracts * pp * (1 - pp))
                    abort_bid = round(px, 1)
                    abort_fee = round(fee_c / contracts, 2)
                    abort_net = round(px - fee_c / contracts, 1)
            out.append([
                ts, symbol, side, ticker,
                round(entry, 1) if entry else "", status, contracts, when,
                round(bid, 1) if bid is not None else "",
                round(ask, 1) if ask is not None else "",
                round(spread, 1) if spread is not None else "",
                depth if depth is not None else "",
                lv,
                "yes" if (bid is not None and ask is not None and bid >= ask) else "no",
                v["R_spread"], v["R_depth"], v["R_crossed"], v["R_askmove"],
                abort_bid, abort_fee, abort_net,
                "" if ob is not None else "orderbook unreadable",
            ])
        if not out:
            return
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHADOW_SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHADOW_SHEET, rows=4000,
                                  cols=len(HEADERS))
            ws.update(values=[HEADERS], range_name="A1")
        ws.append_rows(out, value_input_option="USER_ENTERED",
                       table_range="A1")
        print(f"  [shadow] logged {len(out)} counterfactual row(s)")
    except Exception as e:
        # A shadow failure is never allowed to matter.
        print(f"  [shadow] skipped: {str(e)[:100]}")

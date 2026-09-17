"""Record what a bet could be CASHED OUT for, partway through its window.

Kalshi lets you sell a position before settlement. Whether that is worth doing
cannot be answered from the data we have: every row stores an entry price and
a final outcome, and nothing in between, so there is no way to know what exit
prices were even available. This logs them.

THE DETAIL THAT DECIDES WHETHER THIS DATA IS USEFUL: you enter by BUYING at
the ask and exit by SELLING at the bid. Recording a mid-price for the exit
would flatter every cash-out by half a spread and could easily manufacture an
edge that does not exist. So:

    bet UP   -> you hold YES -> you exit at the YES BID
    bet DOWN -> you hold NO  -> you exit at the NO BID, which is 100 - yes_ask

Both are the pessimistic, real number: what someone will actually pay you now.

Worth knowing while reading any of this: the ENTRY prices in the history are
mid-prices, not asks, so they are already about half a spread optimistic.
Comparing a true exit bid against an optimistic entry understates cash-out
slightly -- which is the safe direction for a decision about whether to start
doing something new.

Read-only. Two signed GETs per signal, no orders.
"""

import os
import time
from datetime import datetime, timezone

import requests

SHEET = "Mid Window"
HEADERS = [
    "Timestamp", "Symbol", "Side", "Ticker", "Entry ¢",
    "Exit @5m ¢", "Exit @10m ¢", "Spread @5m", "Spread @10m", "Note",
]

# When to look. Early enough that a decision is still worth making, late
# enough that the window has actually moved.
OFFSETS = (300, 600)

# The host a phone sees. Orders cannot be placed from CI at all, so the order
# host is irrelevant here -- what matters is the price the app would show the
# person doing the selling.
PRICE_HOST = "https://api.elections.kalshi.com"


def enabled():
    return os.environ.get("MEASURE_MIDWINDOW", "").strip() == "1"


def _book(ticker):
    """(yes_bid_cents, yes_ask_cents) or (None, None)."""
    import predictor as P
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}") or {}
        r = requests.get(f"{PRICE_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs, timeout=10)
        if not r.ok:
            return None, None
        m = r.json().get("market") or {}
    except Exception:
        return None, None

    def c(k):
        try:
            return float(m[k]) * 100.0
        except (KeyError, TypeError, ValueError):
            return None

    return c("yes_bid_dollars"), c("yes_ask_dollars")


def exit_value(ticker, side):
    """What this position could be SOLD for right now, in cents, plus spread.

    Selling means hitting the bid, never the mid and never the ask.
    """
    bid, ask = _book(ticker)
    if bid is None and ask is None:
        return None, ""
    spread = round(ask - bid, 1) if (bid is not None and ask is not None) else ""
    if side == "UP":
        px = bid                      # sell the YES you hold
    else:
        px = (100.0 - ask) if ask is not None else None   # sell the NO you hold
    if px is None or not (0 < px < 100):
        return None, spread
    return px, spread


def measure(client, signals, boundary):
    """Sample each signal's exit price at the configured offsets, then log."""
    if not signals:
        return
    rows = {}
    for ts, symbol, side, ticker, entry in signals:
        if ticker and entry is not None:
            rows[symbol] = [ts, symbol, side, ticker, round(entry, 1),
                            "", "", "", "", ""]
    if not rows:
        return

    for i, offset in enumerate(OFFSETS):
        wait = offset - (datetime.now(timezone.utc) - boundary).total_seconds()
        if wait > 0:
            time.sleep(min(wait, offset))
        got = 0
        for symbol, row in rows.items():
            px, spread = exit_value(row[3], row[2])
            if px is not None:
                row[5 + i] = round(px, 1)
                got += 1
            row[7 + i] = spread
        print(f"  [midwindow] +{offset}s: priced {got}/{len(rows)}")

    import predictor as P
    try:
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=4000, cols=len(HEADERS))
            ws.update("A1", [HEADERS])
        ws.append_rows(list(rows.values()), value_input_option="USER_ENTERED",
                       table_range="A1")
        print(f"  [midwindow] logged {len(rows)} row(s).")
    except Exception as e:
        print(f"  [midwindow] could not write tab: {e}")

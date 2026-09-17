"""Measure what the 3-4 minute entry delay costs.

Every price in the 1,300-bet history was taken about 35 seconds into the
window. Live orders cannot go in until Kalshi lists the market on the order
host, which is three to four minutes later -- and by then the price has
absorbed part of the very move the signal is predicting.

So the backtest and the live system are not the same bet, and nothing we have
says whether the edge survives the delay. This logs the evidence that would
settle it, and places nothing: two signed GETs per signal.

For each champion signal it records:
  the quote at signal time      (what the history is priced at)
  the quote about 4 minutes in  (what a live order would actually pay)
and, once the window resolves, the outcome is already in the Predictions tab,
so a day of this is enough to re-price the whole strategy at the later entry
and compare the two EVs directly.

Writes to a "Entry Decay" tab. Read-only against Kalshi and against every
other tab.
"""

import os
import time
from datetime import datetime, timezone

import requests

DECAY_SHEET = "Entry Decay"
DECAY_HEADERS = [
    "Timestamp", "Symbol", "Side", "Ticker",
    "Entry @35s ¢", "Entry @4min ¢", "Drift ¢", "Secs In", "Note",
]

# Where live orders have to go, and therefore the venue whose price a live bet
# would actually pay. The predictor quotes api.elections, which lists the
# market immediately; this one lags it by minutes.
ORDER_HOST = "https://external-api.kalshi.com"

# How long after the window opens to take the second quote. Matches the
# measured listing delay: not found at +35s or +95s, resolved at +4min.
SECOND_QUOTE_DELAY = 240


def _quote(ticker, side):
    """Current cost in cents for the side we want, from the ORDER host."""
    import predictor as P
    try:
        hdrs = P._kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}")
        r = requests.get(f"{ORDER_HOST}/trade-api/v2/markets/{ticker}",
                         headers=hdrs or {}, timeout=10)
        if not r.ok:
            return None, f"HTTP {r.status_code}"
        m = r.json().get("market") or {}
    except Exception as e:
        return None, str(e)[:60]

    def c(k):
        try:
            return float(m[k]) * 100.0
        except (KeyError, TypeError, ValueError):
            return None

    if side == "UP":
        px = c("yes_ask_dollars") or c("last_price_dollars")
    else:
        bid = c("yes_bid_dollars")
        px = (100.0 - bid) if bid is not None else None
    return (px, "") if (px and 0 < px < 100) else (None, "no price")


def measure(client, signals, boundary):
    """Wait out the listing delay, re-quote, and record both prices."""
    if not signals:
        return
    waited = SECOND_QUOTE_DELAY - (datetime.now(timezone.utc) - boundary).total_seconds()
    if waited > 0:
        time.sleep(min(waited, SECOND_QUOTE_DELAY))
    secs_in = round((datetime.now(timezone.utc) - boundary).total_seconds())

    rows = []
    for ts, symbol, side, ticker, entry in signals:
        if not ticker or entry is None:
            continue
        later, note = _quote(ticker, side)
        rows.append([ts, symbol, side, ticker,
                     round(entry, 1),
                     round(later, 1) if later is not None else "",
                     round(later - entry, 1) if later is not None else "",
                     secs_in, note])
    if not rows:
        return

    import predictor as P
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(DECAY_SHEET)
    except Exception:
        ws = sh.add_worksheet(title=DECAY_SHEET, rows=4000, cols=len(DECAY_HEADERS))
        ws.update("A1", [DECAY_HEADERS])
    ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")

    drifts = [r[6] for r in rows if r[6] != ""]
    if drifts:
        print(f"  [decay] {len(rows)} signal(s) re-quoted at +{secs_in}s; "
              f"mean drift {sum(drifts)/len(drifts):+.2f}c")


def enabled():
    return os.environ.get("MEASURE_ENTRY_DECAY", "").strip() == "1"

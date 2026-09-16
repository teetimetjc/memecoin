"""Dry-run order logging -- step 2 on the path to automated betting.

For every champion signal, work out the order that WOULD be sent and write it
to a "Dry Run" tab. Nothing is placed. There is no POST in this file.

The dashboard's entry price is the market's mid quote captured at signal time.
An order does not fill at the mid: it crosses the spread and eats depth. This
module re-fetches the book at ORDER time and walks it, so the difference
between "the price we scored" and "the price we could have paid" is measured
instead of assumed. That difference is the one number that can invalidate the
whole edge -- entries under 50c currently show 18.4% ROI, and a couple of
cents of slippage per bet is a large share of that.

Three things it settles, none of which are answerable today:
  1. Can the signal's market be resolved and its book read, every time?
  2. What does a $10 marketable order actually average, versus the logged mid?
  3. Is there depth for $10 at all, and how often is there not?

SAFETY: GETs only. No order path, no POST, no credentials beyond the read
already used for market data. A failure here is caught by the caller and
cannot interrupt collection.
"""

import os

import requests

DRY_RUN_SHEET = "Dry Run"

# What a live bet would stake. Matches the dashboard's default so the logged
# slippage is directly comparable to the P&L it would distort.
STAKE_DOLLARS = 10.0

DRY_RUN_HEADERS = [
    "Timestamp", "Symbol", "Side", "Ticker",
    "Signal Entry ¢",      # the mid we scored the bet at
    "Book Best ¢",         # cheapest contract actually offered
    "Fill VWAP ¢",         # average paid to fill $10, walking the book
    "Slippage ¢",          # Fill VWAP - Signal Entry, positive = worse
    "Contracts", "Spent $", "Depth $",
    "Fillable?", "Note",
]


def _book(ticker, hdrs, depth=32):
    """Fetch one market's order book. Returns (yes_levels, no_levels, error).

    Each level is (price_cents, contracts) and both books are BIDS: `yes` are
    bids to buy YES, `no` are bids to buy NO. To BUY yes you cross the no book
    at 100 - no_price, and vice versa -- Kalshi's two sides are complementary,
    so there is no separate ask book to read.
    """
    from predictor import KALSHI_BASE
    try:
        r = requests.get(f"{KALSHI_BASE}/markets/{ticker}/orderbook",
                         params={"depth": depth}, headers=hdrs or {}, timeout=10)
    except Exception as e:
        return None, None, f"request failed: {e}"
    if not r.ok:
        return None, None, f"HTTP {r.status_code} {r.text[:80]}"
    try:
        ob = r.json().get("orderbook") or {}
    except Exception as e:
        return None, None, f"bad JSON: {e}"

    def levels(side):
        raw = ob.get(side)
        if not raw:
            return []
        out = []
        for lv in raw:
            try:
                if isinstance(lv, dict):
                    # Newer payloads name the fields; older ones are bare pairs.
                    p = lv.get("price") or lv.get("price_cents")
                    if p is None and lv.get("price_dollars") is not None:
                        p = float(lv["price_dollars"]) * 100
                    q = lv.get("size") or lv.get("quantity") or lv.get("count")
                else:
                    p, q = lv[0], lv[1]
                out.append((float(p), float(q)))
            except (TypeError, ValueError, IndexError):
                continue
        return out

    return levels("yes"), levels("no"), None


def _walk(offers, stake):
    """Fill `stake` dollars against ascending-cost offers [(cost_c, qty)].

    Returns (contracts, spent, vwap_cents, depth_dollars). Partial fills are
    reported as they are -- a half-filled order is a real outcome, not an
    error, and pretending otherwise would hide exactly the illiquidity this
    module exists to find.
    """
    offers = sorted(offers, key=lambda x: x[0])
    contracts = spent = depth = 0.0
    for cost_c, qty in offers:
        if cost_c <= 0 or qty <= 0:
            continue
        depth += (cost_c / 100.0) * qty
        if spent >= stake:
            continue
        room = (stake - spent) / (cost_c / 100.0)
        take = min(qty, room)
        contracts += take
        spent += take * (cost_c / 100.0)
    vwap = (spent / contracts * 100.0) if contracts else None
    return contracts, spent, vwap, depth


def plan_order(ts_str, symbol, side, kalshi, signal_entry):
    """Build the Dry Run row for one signal. Never raises; returns a row list."""
    from predictor import _kalshi_headers

    ticker = (kalshi or {}).get("ticker", "")
    base = [ts_str, symbol, side, ticker,
            round(signal_entry, 1) if signal_entry is not None else ""]

    if not ticker:
        return base + ["", "", "", "", "", "", "NO", "no ticker on the signal row"]

    hdrs = _kalshi_headers("GET", f"/trade-api/v2/markets/{ticker}/orderbook")
    yes_b, no_b, err = _book(ticker, hdrs)
    if err:
        return base + ["", "", "", "", "", "", "NO", f"book unavailable: {err}"]

    # Buying YES means lifting the no-side bids, and vice versa.
    if side == "UP":
        offers = [(100.0 - p, q) for p, q in (no_b or [])]
    else:
        offers = [(100.0 - p, q) for p, q in (yes_b or [])]
    offers = [(c, q) for c, q in offers if 0 < c < 100]

    if not offers:
        return base + ["", "", "", "", "", 0, "NO", "no resting size on the other side"]

    contracts, spent, vwap, depth = _walk(offers, STAKE_DOLLARS)
    best = min(c for c, _ in offers)
    slip = (vwap - signal_entry) if (vwap is not None and signal_entry) else ""
    filled = spent >= STAKE_DOLLARS - 0.01

    return base + [
        round(best, 1),
        round(vwap, 2) if vwap is not None else "",
        round(slip, 2) if slip != "" else "",
        round(contracts, 2), round(spent, 2), round(depth, 2),
        "YES" if filled else "PARTIAL",
        "" if filled else f"only ${spent:.2f} of ${STAKE_DOLLARS:.0f} available",
    ]


def enabled():
    """Dry-run logging is opt-in, so turning it off needs no code change."""
    return os.environ.get("DRY_RUN_ORDERS", "").strip().lower() in ("1", "true", "yes")


def append(client, rows):
    """Append planned orders to the Dry Run tab, creating it if needed."""
    if not rows:
        return
    import predictor as P
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(DRY_RUN_SHEET)
    except Exception:
        # Sized to the header, not wide: a workbook is capped at 10,000,000
        # cells across all tabs and blank columns count against it. The
        # Predictions tab already cost a day of collection by drifting wide.
        ws = sh.add_worksheet(title=DRY_RUN_SHEET,
                              rows=4000, cols=len(DRY_RUN_HEADERS))
        ws.update("A1", [DRY_RUN_HEADERS])
    # One call for the batch. table_range pins the append to the table at A1
    # for the same reason the Predictions writer does.
    ws.append_rows(rows, value_input_option="USER_ENTERED", table_range="A1")
    print(f"  {DRY_RUN_SHEET}: logged {len(rows)} intended order(s) -- nothing placed.")

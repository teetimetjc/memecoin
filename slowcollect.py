"""Measure the thing that decides the slow-market maker question: fills.

slowbook left resting in slow markets bounded but unresolved -- optimistic
+$0.117 a bet, adverse -$0.130, straddling zero. Those are not two estimates
to average. They are assumptions:

    optimistic  every resting order fills, free, whenever the price is there
    adverse     an order fills ONLY when the market trades through it, which
                is also exactly when the quote was wrong

Which one you get depends on where your order sat in the queue when a taker
arrived, and Kalshi's candlestick history does not record that. No further
backtest narrows it. It has to be collected forward, and this is the
collector.

WHAT IT RECORDS, AND WHY EACH PIECE IS NEEDED.

  THE BOOK, with SIZES. The market list call carries yes_bid_size_fp and
  yes_ask_size_fp beside the prices, so the depth resting at the touch costs
  no extra request. That number IS the queue: 40 contracts in front means a
  taker has to clear 40 before reaching yours.

  THE TRADES, with the taker's side. /markets/trades gives price, count and
  taker_side per print. A taker_side of yes LIFTED the ask, so a resting ask
  at that price would have been hit. This is what turns "would it have
  filled" from an assumption into an observation.

  THE BOOK AFTER. Every snapshot is timestamped, so a trade can be followed
  by what the mid did next. A fill that is immediately followed by the price
  running away is the adverse case; one followed by nothing is the
  optimistic case. The point of collecting both streams is to measure the
  MIX rather than assume it.

WRITES CHANGE ONLY. Slow markets are quiet -- that is the entire reason we
are here -- so a row every poll would be tens of thousands of identical
lines a day. A book row is written only when the touch or its size moves,
and a trade row only for prints not already seen.

NO ORDERS. This module contains no order path, places nothing and resolves
nothing about whether to trade. It only measures what a resting order would
have met.
"""

import sys
import time

import collect15 as C
import predictor as P

SHEET = "SLOWBOOK"
# The families exchange.py measured as actually trading, by volume. Weather
# dailies dominate the liquid non-crypto exchange; the crypto monthlies are
# here as the slow counterpart to the dead fifteen-minute series.
SERIES = ["KXHIGHLAX", "KXBTCMINMON", "KXFEDCOMBO", "KXHIGHAUS", "KXWTIW",
          "KXHIGHPHIL", "KXHIGHTBOS", "KXHIGHTATL", "KXBTCMAXMON",
          "KXTRUMPACT"]
POLL_S = 45
PAUSE = 0.06
FLUSH_EVERY_S = 300
STRIKE_CAP = 8          # markets kept per series, nearest the money
TRADE_LOOKBACK = 50

HEADERS = ["UTC", "Kind", "Ticker", "Series", "Close Time",
           "Yes Bid", "Yes Ask", "Bid Size", "Ask Size",
           "Volume", "OpenInt", "Trade Price", "Trade Count",
           "Taker Side", "Trade ID"]


def _f(v):
    return C._f(v)


def near_money(mk):
    """The strikes a maker could actually quote, nearest a 50c book.

    A temperature ladder lists thirty strikes and twenty-eight of them are
    dead at 1c or 99c. Quoting those is not the question being asked, and
    polling them is most of the request budget.
    """
    scored = []
    for m in mk:
        b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
        if b is None or a is None:
            continue
        mid = (b + a) / 2.0
        if not (0.02 <= mid <= 0.98):
            continue
        scored.append((abs(mid - 0.5), m))
    scored.sort(key=lambda x: x[0])
    return [m for _, m in scored[:STRIKE_CAP]]


def touch(m):
    """Top of book with sizes. The sizes are the point of this file."""
    b, a = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars"))
    if a is None:
        nb = _f(m.get("no_bid_dollars"))
        a = (1.0 - nb) if nb is not None else None
    if b is None:
        na = _f(m.get("no_ask_dollars"))
        b = (1.0 - na) if na is not None else None
    return dict(bid=b, ask=a,
                bidsz=_f(m.get("yes_bid_size_fp")),
                asksz=_f(m.get("yes_ask_size_fp")),
                vol=_f(m.get("volume_fp")),
                oi=_f(m.get("open_interest_fp")))


def trades(ticker):
    """Public prints for one market, newest first."""
    d, err = C.get("/trade-api/v2/markets/trades", ticker=ticker,
                   limit=TRADE_LOOKBACK)
    if not d:
        return []
    return d.get("trades") or []


def sheet():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=40000, cols=len(HEADERS))
        ws.append_row(HEADERS, value_input_option="RAW")
    return ws


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 3540
    ws = sheet()
    deadline = time.time() + seconds
    prev = {}           # ticker -> last written touch
    seen_trades = set()  # trade ids already recorded
    out = []
    last_flush = time.time()
    polls = 0
    print(f"collecting {len(SERIES)} slow series for {seconds}s")

    while time.time() < deadline:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        for s in SERIES:
            d, err = C.get("/trade-api/v2/markets", series_ticker=s,
                           status="open", limit=200)
            time.sleep(PAUSE)
            if not d:
                continue
            for m in near_money(d.get("markets") or []):
                tk = str(m.get("ticker") or "")
                if not tk:
                    continue
                t = touch(m)
                p = prev.get(tk)
                # A row only when something MOVED. Quiet is the normal state
                # of these markets and writing it down repeatedly would bury
                # the events that matter.
                if (p is None or p["bid"] != t["bid"] or p["ask"] != t["ask"]
                        or p["bidsz"] != t["bidsz"]
                        or p["asksz"] != t["asksz"]
                        or p["vol"] != t["vol"]):
                    out.append([stamp, "book", tk, s,
                                str(m.get("close_time") or ""),
                                t["bid"], t["ask"], t["bidsz"], t["asksz"],
                                t["vol"], t["oi"], "", "", "", ""])
                    # Volume moving means a print happened; only then is a
                    # trades call worth the round trip.
                    if p is not None and p["vol"] != t["vol"]:
                        for tr in trades(tk):
                            tid = str(tr.get("trade_id") or tr.get("id") or "")
                            if not tid or tid in seen_trades:
                                continue
                            seen_trades.add(tid)
                            px = _f(tr.get("yes_price_dollars"))
                            if px is None:
                                yp = _f(tr.get("yes_price"))
                                px = (yp / 100.0) if yp is not None else None
                            out.append([
                                str(tr.get("created_time") or stamp), "trade",
                                tk, s, str(m.get("close_time") or ""),
                                t["bid"], t["ask"], t["bidsz"], t["asksz"],
                                t["vol"], t["oi"], px,
                                _f(tr.get("count_fp")) or _f(tr.get("count")),
                                str(tr.get("taker_side") or ""), tid])
                        time.sleep(PAUSE)
                    prev[tk] = t

        polls += 1
        now = time.time()
        if out and (now - last_flush > FLUSH_EVERY_S):
            try:
                ws.append_rows([["" if v is None else v for v in r]
                                for r in out], value_input_option="RAW")
                print(f"[{time.strftime('%H:%M:%S')}] wrote {len(out)} rows "
                      f"({polls} polls, {len(seen_trades)} prints seen)")
                out = []
            except Exception as e:
                print(f"  WRITE FAILED ({str(e)[:70]}) -- keeping rows")
            last_flush = now
        time.sleep(POLL_S)

    if out:
        try:
            ws.append_rows([["" if v is None else v for v in r] for r in out],
                           value_input_option="RAW")
            print(f"wrote final {len(out)} rows")
        except Exception as e:
            print(f"  FINAL WRITE FAILED ({str(e)[:70]}) -- {len(out)} lost")
    print(f"finished; {polls} polls, {len(seen_trades)} prints recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())

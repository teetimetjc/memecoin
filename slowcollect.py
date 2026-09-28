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

PRINTS ARE AGGREGATED PER POLL, not written one per row. A raw print feed
is 70,000 rows a day and a Google Sheet holds about 660,000 at this width --
nine days before the thing wedges, which is not a collector, it is a fuse.
Each poll writes at most one row per market per taker side: how many prints,
how many contracts, and the volume-weighted price. Nothing needed for the
question is lost, because what matters is whether a resting order WOULD have
been filled in that interval and what the price did next, not the identity
of each individual print.

WRITES CHANGE ONLY, AND THE DEFINITION OF "CHANGE" IS DELIBERATE. The
first ten-minute run wrote 1,691 rows, which annualises to a quarter of a
million a day and would bury the sheet inside a week. Two causes, both
fixed here.

  A BOOK ROW NEEDS A PRICE MOVE. Not a size move, and -- after the fourth
  smoke run -- not a volume move either. Volume changes with every print, so
  keying on it wrote a book row beside every trade row, and the trade row
  already carries the book as it stood. Volume is still what TRIGGERS the
  trades call; it just no longer earns a row of its own.

  The older half of this note stands: a size move is not an event. The size at the
  touch churns constantly even in a quiet market -- someone adding and
  pulling ten contracts is not an event -- and keying on it wrote a row per
  market per poll. Sizes are still recorded, and every trade row carries the
  book AS IT STOOD when the print landed, which is where the queue number
  actually matters.

  A TRADE MUST BE NEW TO THIS WORLD, not merely new to this process. The
  trades endpoint returns the last fifty prints whatever their age, and the
  seen-set is per-run, so each hourly run would re-ingest the same history:
  eighty markets times fifty prints is four thousand duplicate rows an hour,
  every hour, of trades already recorded. Prints older than the run start
  are now skipped.

NO ORDERS. This module contains no order path, places nothing and resolves
nothing about whether to trade. It only measures what a resting order would
have met.
"""

import calendar
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
POLL_S = 60
PAUSE = 0.06
FLUSH_EVERY_S = 300
STRIKE_CAP = 6          # markets kept per series, nearest the money
TRADE_LOOKBACK = 50

HEADERS = ["UTC", "Kind", "Ticker", "Series", "Close Time",
           "Yes Bid", "Yes Ask", "Bid Size", "Ask Size",
           "Volume", "OpenInt", "VWAP", "Contracts",
           "Taker Side", "Prints"]


def _f(v):
    return C._f(v)


def ts_any(v):
    """Epoch seconds from whatever shape Kalshi used, or None.

    collect15.close_ts takes a market dict, not a string, so reusing it here
    would return None for every print and defeat the age filter.

    BOTH SHAPES ARE ACCEPTED because this project has now been bitten six
    times by a field that quietly changed type or name, and an age filter
    that silently passes everything is worse than no filter: it looks like
    a quiet market producing 1,646 prints in ten minutes.
    """
    if isinstance(v, (int, float)) and v > 0:
        # seconds or milliseconds -- anything past the year 2100 is ms
        return float(v) / 1000.0 if v > 4102444800 else float(v)
    s = str(v or "")
    if s.isdigit():
        return ts_any(int(s))
    if len(s) < 19:
        return None
    try:
        return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def trade_ts(tr):
    """The print's timestamp, whichever key carries it."""
    for k in ("created_time", "created_ts", "ts", "timestamp", "time"):
        t = ts_any(tr.get(k))
        if t is not None:
            return t, k
    return None, None


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


ROTATE_AT = 400000      # rows in a tab before the next one is started


def sheet():
    """The current tab, rolling to a new one before the sheet fills.

    Five rounds of trimming took this from 240,000 rows a day to about
    32,000, which is still only three weeks of runway -- and a study that
    dies of a full spreadsheet mid-measurement has answered nothing. So the
    limit stops being a deadline: at 400,000 rows the collector starts
    SLOWBOOK2, then SLOWBOOK3, and the analysis reads whatever tabs exist.
    Rotating is cheap; discovering the wedge three weeks in is not.
    """
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    n = 1
    while True:
        title = SHEET if n == 1 else f"{SHEET}{n}"
        try:
            ws = sh.worksheet(title)
        except Exception:
            ws = sh.add_worksheet(title=title, rows=20000,
                                  cols=len(HEADERS))
            ws.append_row(HEADERS, value_input_option="RAW")
            print(f"  started tab {title}")
            return ws
        try:
            used = len(ws.col_values(1))
        except Exception:
            used = 0
        if used < ROTATE_AT:
            print(f"  writing to {title} ({used} rows)")
            return ws
        n += 1
        if n > 20:
            raise SystemExit("20 SLOWBOOK tabs is not a rotation, it is a "
                             "runaway -- stopping rather than filling more")


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 3540
    ws = sheet()
    deadline = time.time() + seconds
    started = time.time() - 120     # small grace for clock skew
    old_n = fresh = unstamped = 0
    stamped_key = None
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
                moved = (p is None or p["bid"] != t["bid"]
                         or p["ask"] != t["ask"])
                traded = (p is not None and p["vol"] != t["vol"])
                if moved or traded:
                    # A quote that moved is an event. Volume moving is
                    # merely how we learn a print happened, and the trade
                    # rows below carry this same book with it.
                    if moved:
                        out.append([stamp, "book", tk, s,
                                    str(m.get("close_time") or ""),
                                    t["bid"], t["ask"], t["bidsz"],
                                    t["asksz"], t["vol"], t["oi"],
                                    "", "", "", ""])
                    # Volume moving means a print happened; only then is a
                    # trades call worth the round trip.
                    if traded:
                        # One row per taker side per poll, not per print.
                        agg = {}
                        for tr in trades(tk):
                            tid = str(tr.get("trade_id") or tr.get("id") or "")
                            if not tid or tid in seen_trades:
                                continue
                            ct, tkey = trade_ts(tr)
                            if ct is None:
                                if not unstamped:
                                    print("  TRADE HAS NO PARSEABLE TIME. "
                                          f"keys: {sorted(tr.keys())}")
                                unstamped += 1
                                continue
                            if ct < started:
                                seen_trades.add(tid)
                                old_n += 1
                                continue
                            if not stamped_key:
                                stamped_key = tkey
                                print(f"  trade timestamps read from "
                                      f"'{tkey}'; keys: {sorted(tr.keys())}")
                            seen_trades.add(tid)
                            fresh += 1
                            px = _f(tr.get("yes_price_dollars"))
                            if px is None:
                                yp = _f(tr.get("yes_price"))
                                px = (yp / 100.0) if yp is not None else None
                            n = (_f(tr.get("count_fp"))
                                 or _f(tr.get("count")) or 0.0)
                            side = str(tr.get("taker_side") or "?")
                            a = agg.setdefault(side, [0.0, 0.0, 0])
                            if px is not None:
                                a[0] += px * n      # for the VWAP
                            a[1] += n
                            a[2] += 1
                        for side, (pxn, n, cnt) in agg.items():
                            out.append([
                                stamp, "trade", tk, s,
                                str(m.get("close_time") or ""),
                                t["bid"], t["ask"], t["bidsz"], t["asksz"],
                                t["vol"], t["oi"],
                                (pxn / n) if n else "", n, side, cnt])
                        time.sleep(PAUSE)
                    prev[tk] = t

        polls += 1
        now = time.time()
        if out and (now - last_flush > FLUSH_EVERY_S):
            try:
                ws.append_rows([["" if v is None else v for v in r]
                                for r in out], value_input_option="RAW")
                print(f"[{time.strftime('%H:%M:%S')}] wrote {len(out)} rows "
                      f"({polls} polls, {fresh} fresh / {old_n} old prints)")
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
    print(f"finished; {polls} polls, {fresh} fresh prints written, "
          f"{old_n} skipped as older than this run, {unstamped} unstamped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

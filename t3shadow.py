"""What a T-3 bet WOULD have been, on the signals actually bet at T-9.

THE CLAIM BEING TESTED. lateentry swept five offsets by four bands over
6,000 settled markets and, split chronologically, T-3 in the 90-94c band
held at +3.29% and +3.13% net ROI across the two halves. The same band at
the T-9 the bot runs read +0.05% and +0.26%. That is the largest gap this
project has found, and it is entirely a QUOTE result.

THREE THINGS A QUOTE BACKTEST CANNOT SEE, and this file exists for all
three:

  FILL RATE. 65 of 229 orders once filled nothing at T-9 because the ask
  moved up past the limit, and those markets settled YES 98.4% of the time.
  At T-3 there is six minutes less for a seller to appear, so the miss rate
  should be worse -- and the bets it misses are precisely the ones that were
  about to win. A cohort that cannot be bought is not an edge.

  SWEEP COST. A limit is a maximum, so a thin book does not refuse the
  order, it fills it higher up the ladder. The difference between the ask
  the decision was made on and the price actually paid is where this
  project's backtest-to-live shortfall has always lived.

  SIGNAL CORRESPONDENCE, which is the one nobody asks about and may matter
  most. The backtest scored whatever sat in 90-94c AT T-3. That is not the
  same set of markets the bot picks at T-9: in six minutes a 93c favourite
  can strengthen past 96c and leave the band entirely. If a large share of
  T-9 signals have drifted out by T-3, the tradeable T-3 cohort is smaller
  and different than the backtest implied, and the per-day estimate shrinks
  with it. This records both directions -- signals that drifted OUT, and
  markets that drifted IN without ever being a T-9 signal.

IT PLACES NOTHING. It imports no ordering code, holds no order state, and
is called only after the window's real orders have already been sent -- the
same rule shadow.py follows, and for the same reason: an extra round trip
between the decision and the order is extra time for the ask to move, which
would change real fills. Every entry point is wrapped so a failure here
cannot reach the bot. The worst case is a missing row.

READ IT AGAINST THE REAL BETS IN THE SAME WINDOWS. The T-9 columns are what
was actually paid; the T-3 columns are what the book would have offered six
minutes later. Same markets, same minutes, one difference.
"""
import os
import time

SHEET = "T3 Shadow"

HEADERS = [
    "Window UTC", "Sampled UTC", "Ticker", "Series",
    # What happened at T-9 -- the real bet, or the real skip.
    "T9 entry ¢", "T9 status", "T9 contracts", "T9 fill limit ¢",
    # What the book looked like at T-3.
    "Secs to close", "T3 ask ¢", "T3 bid ¢", "T3 spread ¢",
    "T3 size at ask", "T3 levels for n", "T3 fillable",
    # The comparison the whole file is for.
    "Moved ¢", "In band at T-3", "Correspondence", "Note",
]

BAND_LO = float(os.environ.get("T3_BAND_LO") or 90.0)
BAND_HI = float(os.environ.get("T3_BAND_HI") or 96.0)
# The window this samples in. Non-overlapping with the T-9 entry window by a
# wide margin, so a reading can never be filed as the wrong offset.
LO_SECS = int(os.environ.get("T3_LO_SECS") or 150)
HI_SECS = int(os.environ.get("T3_HI_SECS") or 215)

# ON by default. It writes to its own tab and sends nothing, so the cost of
# leaving it on is one sheet append per window.
def enabled():
    return (os.environ.get("T3_SHADOW") or "1").strip() == "1"


# What the bot saw at T-9, keyed by ticker, filled in by remember() and read
# back by sample() a few minutes later. Process-local on purpose: a session
# that restarts loses the pending windows rather than guessing at them.
PENDING = {}


def remember(tuples, rows, stake):
    """Record this window's T-9 decisions. Never raises."""
    try:
        if not enabled():
            return
        by_ticker = {}
        for r in rows or []:
            if len(r) > 3:
                by_ticker[str(r[3])] = r
        for t in tuples or []:
            try:
                ts, _symbol, _side, ticker, entry = t[:5]
            except (ValueError, TypeError):
                continue
            if not ticker:
                continue
            row = by_ticker.get(str(ticker))
            PENDING[str(ticker)] = dict(
                window=ts,
                entry=entry,
                status=(str(row[8]) if row and len(row) > 8 else "NOROW"),
                contracts=(int(row[6] or 0)
                           if row and len(row) > 6 and str(row[6]).strip()
                           else 0),
                limit=(row[5] if row and len(row) > 5 else ""),
                stake=stake,
                sampled=False,
            )
    except Exception as e:
        print(f"  [t3shadow] remember skipped: {str(e)[:90]}")


def _price_cents(q):
    try:
        a = q.get("ask")
        return 100.0 * float(a) if a is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def due(now=None):
    """Is a T-3 moment plausibly open right now?

    THIS GATE IS A SAFETY PROPERTY, NOT AN OPTIMISATION. The bot polls every
    10 seconds for 5h45m. An unguarded market read here would add roughly
    two thousand extra HTTP calls per session and about double the bot's
    traffic against the exchange -- and the one thing this module promises
    is that it cannot affect live betting. Rate-limiting the order path to
    collect research data would break that promise in the most expensive
    way available.

    The windows close on the quarter hour, so T-3 is a fixed arc of the
    900-second cycle and can be checked off the clock for free. This turns
    ~90 reads per window into ~7.
    """
    secs = int(now if now is not None else time.time()) % 900
    left = (900 - secs) % 900
    # A little slack on each side so a slow poll cannot step over the arc.
    return (LO_SECS - 15) <= left <= (HI_SECS + 15)


def sample(client):
    """Sample T-3 for anything pending, plus in-band markets that drifted in.

    Called on every poll, but reads the exchange only inside the T-3 arc --
    see due(). The T-3 moment arrives six minutes after the T-9 decision,
    long after cycle() has returned, so the loop has to be the thing
    looking. Never raises.
    """
    try:
        if not enabled() or not due():
            return
        import predictor as P
        import backfill as B
        import collect15 as C
        import openmk
        import shadow

        # One call per series. The unfiltered form returns an arbitrary page
        # of 200 open markets out of thousands and never includes these, so
        # it collects nothing while looking healthy -- see openmk.py.
        markets = openmk.open_markets(openmk.MAJORS)
        now = time.time()
        out = []
        for m in markets:
            tk = str(m.get("ticker") or "")
            if not tk:
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            left = close - now
            if not (LO_SECS <= left <= HI_SECS):
                continue
            pend = PENDING.get(tk)
            q = C.quote_from_market(m)
            ask_c = _price_cents(q) if q else None
            in_band = (ask_c is not None and BAND_LO <= ask_c <= BAND_HI)

            # Three cases, and all three are recorded. A market that was a
            # T-9 signal is the comparison; one that drifted into the band
            # without being a signal is a bet the T-3 rule would take and
            # the T-9 rule never saw; everything else is skipped.
            if pend is None and not in_band:
                continue
            if pend is not None and pend.get("sampled"):
                continue
            if pend is None:
                corr = "drifted IN -- never a T-9 signal"
            elif in_band:
                corr = "still in band"
            else:
                corr = "drifted OUT of band"

            want = (pend or {}).get("contracts") or 0
            ob = shadow.orderbook(tk)
            bid_c, book_ask = shadow._best(ob, "UP") if ob else (None, None)
            levels = shadow._levels(ob, "UP") if ob else []
            size = levels[0][1] if levels else None
            need, lv = float(want or 6), 0
            for _p, sz in levels:
                if need <= 0:
                    break
                need -= sz
                lv += 1
            fillable = ("yes" if (need <= 0 and lv == 1)
                        else ("sweeps %d levels" % lv if lv else "no book"))
            t3 = book_ask if book_ask is not None else ask_c
            moved = ((t3 - pend["entry"])
                     if (pend and t3 is not None
                         and isinstance(pend.get("entry"), (int, float)))
                     else "")
            if pend is not None:
                pend["sampled"] = True
            out.append([
                (pend or {}).get("window", ""),
                time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
                tk, tk.split("-")[0],
                round(pend["entry"], 1) if (pend and isinstance(
                    pend.get("entry"), (int, float))) else "",
                (pend or {}).get("status", ""),
                (pend or {}).get("contracts", ""),
                (pend or {}).get("limit", ""),
                int(left),
                round(t3, 1) if t3 is not None else "",
                round(bid_c, 1) if bid_c is not None else "",
                round(book_ask - bid_c, 1)
                if (book_ask is not None and bid_c is not None) else "",
                size if size is not None else "",
                lv, fillable,
                round(moved, 1) if isinstance(moved, (int, float)) else "",
                "yes" if in_band else "no",
                corr,
                "" if ob is not None else "orderbook unreadable",
            ])
        if not out:
            return
        sh = client.open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET)
        except Exception:
            ws = sh.add_worksheet(title=SHEET, rows=4000, cols=len(HEADERS))
            ws.update(values=[HEADERS], range_name="A1")
        ws.append_rows(out, value_input_option="USER_ENTERED",
                       table_range="A1")
        for r in out:
            print(f"  [t3shadow] {r[3]:<10} {r[8]:>3}s  T-9 {r[4]}c -> "
                  f"T-3 {r[9]}c  size {r[12]}  {r[14]}  ({r[17]})")
    except Exception as e:
        # A shadow failure is never allowed to matter.
        print(f"  [t3shadow] sample skipped: {str(e)[:100]}")

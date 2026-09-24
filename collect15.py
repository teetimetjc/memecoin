"""Collect every Kalshi 15-minute market, priced all the way to expiry.

WHY A NEW COLLECTOR RATHER THAN MORE OF THE OLD DATA. The v6 tab holds one
price snapshot per window, taken early, and that single choice quietly
decided what could ever be found in it: 5,564 graded bets contained 52
contracts under 20c and 42 over 80c. The extremes are not rare on this
exchange -- they are where every market ENDS. We simply never looked, and
no amount of re-analysis can add a sample that was not taken.

So this samples the PATH: the same market at roughly 14, 12, 9, 6, 3 and 1
minutes before close. A market quoted 0.52 at T-14 and 0.91 at T-1 produces
observations in both regimes, which is the only way to ask whether the fee
-- maximised at 50c and a third of that at the extremes -- is the thing
standing between this project and a profitable bet.

IT COLLECTS FACTS, NOT DECISIONS. No signal, no call, no side. Every column
is something the exchange said, plus the settlement Kalshi itself reports.
A collector that stores its own opinions can only ever confirm them, and
the opinions here have been wrong twelve times.

ALL fifteen-minute series, not just the five coins. The crypto ones have
company -- oil, metals, rates, FX -- on identical mechanics and a different
crowd, and the comparison is free.

WHAT IT DOES NOT DO. It does not search, score, rank or notify. The search
protocol is deliberately a separate step run against a frozen date cut,
because the expensive mistake in this project has never been collecting too
little -- it has been looking at everything at once and believing the best
number that came back.

Read-only against the exchange. Places no orders.
"""

import collections
import json
import os
import sys
import time

import requests

import predictor as P

HOST = "https://api.elections.kalshi.com"
SHEET = "M15"
# Minutes before close at which to sample. The late ones matter most: they
# are the only source of extreme prices this project has ever had.
OFFSETS = (14, 12, 9, 6, 3, 1)
TOL_S = 45          # a sample counts for an offset if within this many seconds
POLL_S = 20
PAUSE = 0.06
SERIES_REFRESH_S = 3600
FLUSH_EVERY_S = 300
SETTLE_GRACE_S = 120

HEADERS = (["Ticker", "Series", "Event", "Close Time", "Strike",
            "Collected UTC"]
           + [f"{f}{m}" for m in OFFSETS
              for f in ("bid", "ask", "bidsz", "asksz")]
           + ["Volume", "OpenInt", "LastPrice", "Result", "Settled UTC"])


def get(path, **params):
    try:
        h = P._kalshi_headers("GET", path.split("?")[0]) or {}
        r = requests.get(HOST + path, headers=h, params=params or None,
                         timeout=20)
        if not r.ok:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except Exception as e:
        return None, str(e)[:80]


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def fifteen_min_series():
    """Every series Kalshi labels fifteen_min."""
    d, err = get("/trade-api/v2/series")
    if not d:
        print(f"  could not list series: {err}")
        return []
    out = [str(s.get("ticker")) for s in (d.get("series") or [])
           if str(s.get("frequency") or "") == "fifteen_min"]
    return sorted(t for t in out if t and t != "None")


def open_markets(series):
    """Markets in this series that have not closed yet."""
    d, err = get("/trade-api/v2/markets", series_ticker=series,
                 status="open", limit=200)
    if not d:
        return []
    return d.get("markets") or []


def close_ts(m):
    c = str(m.get("close_time") or "")
    if len(c) < 19:
        return None
    try:
        import calendar
        return calendar.timegm(time.strptime(c[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def book(ticker):
    """Top of book, in DOLLARS -- or None if the exchange gave us nothing.

    THE SHAPE IS PROBED, NOT ASSUMED. The first run of this collector wrote
    24 well-formed rows whose every book column was empty, because the path
    and field names here were written from memory. That is the fifth time
    this project has produced a green run full of blanks. `probe` below
    dumps the raw response so the guessing stops.

    Kalshi runs ONE book: a resting YES bid at p IS a NO ask at 1-p. So the
    YES ask is derived from the NO bid rather than read as its own quote.
    """
    for path in (f"/trade-api/v2/markets/{ticker}/orderbook",
                 f"/trade-api/v2/markets/{ticker}/orderbook_fp"):
        d, err = get(path, depth=3)
        if not d:
            continue
        # The envelope is {"orderbook_fp": {"yes_dollars": [...],
        # "no_dollars": [...]}} -- the PATH is /orderbook but the KEY
        # inside it carries the _fp suffix. Looking for d["orderbook"],
        # finding nothing and falling back to the envelope itself is what
        # produced 24 rows of empty book columns: the fields were one level
        # further down the whole time.
        ob = d.get("orderbook_fp") or d.get("orderbook") or d
        if not isinstance(ob, dict):
            continue

        def side(*names):
            """Best (highest) resting bid on one side, with its size."""
            lv = None
            for n in names:
                v = ob.get(n)
                if v:
                    lv = v
                    break
            if not lv:
                return None, None
            best_p = best_s = None
            for row in lv:
                try:
                    p_, s_ = _f(row[0]), _f(row[1])
                except Exception:
                    continue
                if p_ is None:
                    continue
                p_ = p_ / 100.0 if p_ > 1.5 else p_
                if best_p is None or p_ > best_p:
                    best_p, best_s = p_, s_
            return best_p, best_s

        yb, ybs = side("yes_dollars", "yes")
        nb, nbs = side("no_dollars", "no")
        if yb is None and nb is None:
            continue                      # this path answered but said nothing
        ya = (1.0 - nb) if nb is not None else None
        return dict(bid=yb, ask=ya, bidsz=ybs, asksz=nbs)
    return None


def probe():
    """Dump the raw orderbook response for one live market and stop.

    Costs one run and settles the shape question that four previous
    failures were all versions of."""
    ser = fifteen_min_series()
    print(f"{len(ser)} fifteen-minute series")
    for s in ser[:12]:
        for m in open_markets(s):
            tk = str(m.get("ticker") or "")
            ct = close_ts(m)
            if not tk or not ct or ct - time.time() > 900:
                continue
            print(f"\nmarket {tk}   closes in {(ct-time.time())/60:.1f} min")
            print(f"  market-object price fields: "
                  f"{ {k: v for k, v in m.items() if 'price' in k.lower()} }")
            for path in (f"/trade-api/v2/markets/{tk}/orderbook",
                         f"/trade-api/v2/markets/{tk}/orderbook_fp"):
                d, err = get(path, depth=3)
                if not d:
                    print(f"  {path} -> {err}")
                    continue
                print(f"  {path} -> OK")
                print(f"    verbatim: {json.dumps(d)[:700]}")
            print(f"  parsed by book(): {book(tk)}")
            return 0
    print("no market within 15 minutes of close right now")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        return probe()
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 3600
    deadline = time.time() + seconds
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
        have = {r[0] for r in ws.get_all_values()[1:] if r and r[0]}
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=20000, cols=len(HEADERS))
        ws.update("A1", [HEADERS])
        have = set()
    print(f"{SHEET}: {len(have)} markets already recorded")

    rows = {}              # ticker -> partial record
    done = []              # finished records waiting to be written
    series, series_at = [], 0.0
    last_flush = time.time()
    polls = 0

    while time.time() < deadline:
        now = time.time()
        if now - series_at > SERIES_REFRESH_S:
            series = fifteen_min_series()
            series_at = now
            print(f"[{time.strftime('%H:%M:%S')}] {len(series)} fifteen-minute "
                  f"series: {', '.join(series[:10])}"
                  f"{' ...' if len(series) > 10 else ''}")

        live = 0
        for s in series:
            for m in open_markets(s):
                tk = str(m.get("ticker") or "")
                ct = close_ts(m)
                if not tk or not ct or tk in have:
                    continue
                left = ct - now
                if left <= 0 or left > (max(OFFSETS) + 2) * 60:
                    continue
                live += 1
                rec = rows.get(tk)
                if rec is None:
                    rec = rows[tk] = dict(
                        tk=tk, series=s, ev=str(m.get("event_ticker") or ""),
                        close=str(m.get("close_time") or ""),
                        strike=m.get("floor_strike") or m.get("cap_strike")
                        or m.get("strike_type") or "",
                        start=time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                            time.gmtime()),
                        snap={})
                # which offset, if any, does this moment serve?
                want = None
                for off in OFFSETS:
                    if abs(left - off * 60) <= TOL_S and off not in rec["snap"]:
                        want = off
                        break
                if want is not None:
                    b = book(tk)
                    if b:
                        rec["snap"][want] = b
                rec["vol"] = _f(m.get("volume_fp")) or _f(m.get("volume"))
                rec["oi"] = (_f(m.get("open_interest_fp"))
                             or _f(m.get("open_interest")))
                rec["last"] = _f(m.get("last_price_dollars"))
                time.sleep(PAUSE)

        # markets that have closed: ask Kalshi what they settled to
        for tk in [t for t, r in rows.items()
                   if close_ts({"close_time": r["close"]})
                   and close_ts({"close_time": r["close"]}) + SETTLE_GRACE_S < now]:
            rec = rows.pop(tk)
            # A snapshot dict full of Nones is not an observation. The
            # first run wrote 24 rows that passed this check and carried no
            # prices at all, because only the MISSING case was tested.
            if not any(s.get("bid") is not None or s.get("ask") is not None
                       for s in rec["snap"].values()):
                continue
            d, err = get(f"/trade-api/v2/markets/{tk}")
            mk = (d or {}).get("market") or {}
            rec["result"] = str(mk.get("result") or "").lower()
            rec["settled"] = time.strftime("%Y-%m-%d %H:%M:%S UTC",
                                           time.gmtime())
            if rec["last"] is None:
                rec["last"] = _f(mk.get("last_price_dollars"))
            done.append(rec)

        polls += 1
        if polls % 15 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] tracking {len(rows)} open, "
                  f"{len(done)} ready, {live} in range")

        if done and (now - last_flush > FLUSH_EVERY_S):
            flush(ws, done, have)
            last_flush = now
        time.sleep(POLL_S)

    if done:
        flush(ws, done, have)
    print(f"finished; {len(have)} markets recorded in total")
    return 0


def flush(ws, done, have):
    out = []
    while done:
        r = done.pop()
        if r["tk"] in have:
            continue
        have.add(r["tk"])
        row = [r["tk"], r["series"], r["ev"], r["close"], r["strike"],
               r["start"]]
        for off in OFFSETS:
            s = r["snap"].get(off) or {}
            row += [s.get("bid"), s.get("ask"), s.get("bidsz"), s.get("asksz")]
        row += [r.get("vol"), r.get("oi"), r.get("last"),
                r.get("result", ""), r.get("settled", "")]
        out.append(["" if v is None else v for v in row])
    if not out:
        return
    try:
        ws.append_rows(out, value_input_option="RAW")
        print(f"  wrote {len(out)} markets")
    except Exception as e:
        print(f"  WRITE FAILED ({str(e)[:70]}) -- {len(out)} rows lost")


if __name__ == "__main__":
    sys.exit(main())

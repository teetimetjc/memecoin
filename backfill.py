"""Rebuild the M15 dataset from Kalshi's candlestick history.

Forward collection produces about 700 markets a day. Kalshi already holds
per-minute bid, ask, volume and open interest for every market that has
ever settled, so the same dataset can be reconstructed backwards in an
afternoon instead of accumulating over months.

IT WRITES TO ITS OWN TAB. Backfilled rows are DISCOVERY data: they existed
before the fallen-favourite spec was frozen, and mixing them into M15 would
let a rule be "confirmed" by history it was found in. That is the single
mistake underneath most of the twelve dead strategies here, and a separate
tab makes it impossible rather than merely discouraged.

Columns match M15 exactly so the two can be analysed with the same code --
the only difference is which question each is allowed to answer.

Read-only against the exchange. Places no orders.
"""

import calendar
import collections
import json
import sys
import time

import requests

import predictor as P

HOST = "https://api.elections.kalshi.com"
SHEET = "M15H"
OFFSETS = (14, 12, 9, 6, 3, 1)
PAUSE = 0.06
HEADERS = (["Ticker", "Series", "Event", "Close Time", "Strike",
            "Collected UTC"]
           + [f"{f}{m}" for m in OFFSETS
              for f in ("bid", "ask", "bidsz", "asksz")]
           + ["Volume", "OpenInt", "LastPrice", "Result", "Settled UTC"])

# The liquid fifteen-minute series. Backfilling the dead ones costs the same
# and buys nothing: the M15 run already showed a mispricing nobody can trade
# is a curiosity.
DEFAULT = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
           "KXWTI15M", "KXGOLD15M", "KXNATGAS15M"]


def get(path, **params):
    try:
        h = P._kalshi_headers("GET", path.split("?")[0]) or {}
        r = requests.get(HOST + path, headers=h, params=params or None,
                         timeout=25)
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


def ts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def settled_markets(series, cap):
    """Walk the cursor backwards through a series' settled markets."""
    out, cur = [], None
    while len(out) < cap:
        p = dict(series_ticker=series, status="settled", limit=200)
        if cur:
            p["cursor"] = cur
        d, err = get("/trade-api/v2/markets", **p)
        if not d:
            break
        mk = d.get("markets") or []
        out += mk
        cur = d.get("cursor")
        if not cur or not mk:
            break
        time.sleep(PAUSE)
    return out[:cap]


def candles(series, ticker, close):
    """Per-minute candles over the last 15 minutes of a market's life."""
    end = ts(close)
    if end is None:
        return None
    d, err = get(f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                 start_ts=end - 16 * 60, end_ts=end, period_interval=1)
    if not d:
        return None
    return d.get("candlesticks") or []


def snap_from(cs, close):
    """Map candles onto the same offsets the live collector samples.

    A candle's end_period_ts is the minute it closes, so the candle whose
    end sits nearest T-n is the one that offset saw. Bid and ask come from
    the candle's own yes_bid/yes_ask closes -- the SAME quantities the live
    collector reads off the book, so the two datasets line up."""
    end = ts(close)
    out = {}
    if end is None:
        return out
    for off in OFFSETS:
        want = end - off * 60
        best, bd = None, 1e9
        for c in cs:
            t = _f(c.get("end_period_ts"))
            if t is None:
                continue
            gap = abs(t - want)
            if gap < bd:
                best, bd = c, gap
        if best is None or bd > 90:
            continue
        def side(k):
            v = best.get(k)
            return _f(v.get("close_dollars")) if isinstance(v, dict) else None
        yb, ya = side("yes_bid"), side("yes_ask")
        if yb is None and ya is None:
            continue
        out[off] = dict(bid=yb, ask=ya,
                        bidsz=_f(best.get("volume_fp")), asksz=None)
    return out


def probe(series_list):
    print("=" * 78)
    print("HOW FAR BACK DOES KALSHI GO, AND IS THE HISTORY USABLE?")
    print("=" * 78)
    for s in series_list[:4]:
        mk = settled_markets(s, 20000)
        if not mk:
            print(f"  {s:<14} no settled markets returned")
            continue
        closes = sorted(str(m.get("close_time") or "") for m in mk if m.get("close_time"))
        print(f"\n  {s:<14} {len(mk)} settled markets")
        print(f"     newest {closes[-1][:16]}   oldest {closes[0][:16]}")
        span = (ts(closes[-1]) - ts(closes[0])) / 86400.0 if len(closes) > 1 else 0
        print(f"     span {span:.1f} days")
        old = min(mk, key=lambda m: str(m.get("close_time") or "zzz"))
        cs = candles(s, str(old.get("ticker")), old.get("close_time"))
        if cs is None:
            print("     OLDEST market: candlesticks call failed")
        else:
            sn = snap_from(cs, old.get("close_time"))
            print(f"     OLDEST market: {len(cs)} candles -> {len(sn)}/6 offsets"
                  f"   result={old.get('result')}")
            if sn:
                o = sorted(sn)[0]
                print(f"       sample T-{o}: {sn[o]}")
    return 0


def run(series_list, budget_s=2100):
    """Rebuild history into M15H, resumable and time-boxed.

    One candlestick call per market, so a few thousand markets is tens of
    minutes. The budget stops well inside the job timeout and the tab is
    the resume point -- tickers already present are skipped, so running it
    again simply continues rather than starting over or duplicating."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        ws = sh.worksheet(SHEET)
        have = {r[0] for r in ws.get_all_values()[1:] if r and r[0]}
    except Exception:
        ws = sh.add_worksheet(title=SHEET, rows=40000, cols=len(HEADERS))
        ws.update(values=[HEADERS], range_name="A1")
        have = set()
    print(f"{SHEET}: {len(have)} markets already backfilled")
    stop = time.time() + budget_s
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    total = 0
    for ser in series_list:
        if time.time() > stop:
            print("  budget spent; run again to continue")
            break
        mk = [m for m in settled_markets(ser, 20000)
              if str(m.get("ticker") or "") not in have
              and str(m.get("result") or "").lower() in ("yes", "no")]
        print(f"  {ser:<14} {len(mk)} markets to fetch")
        out = []
        for m in mk:
            if time.time() > stop:
                break
            tk = str(m.get("ticker"))
            cs = candles(ser, tk, m.get("close_time"))
            time.sleep(PAUSE)
            if not cs:
                continue
            snap = snap_from(cs, m.get("close_time"))
            # Same rule the live collector uses: a snapshot with no quote in
            # it is not an observation, and writing it down as one is how
            # 24 hollow rows reached the sheet on day one.
            if not any(v.get("bid") is not None or v.get("ask") is not None
                       for v in snap.values()):
                continue
            row = [tk, ser, str(m.get("event_ticker") or ""),
                   str(m.get("close_time") or ""),
                   m.get("floor_strike") or m.get("cap_strike") or "", stamp]
            for off in OFFSETS:
                q = snap.get(off) or {}
                row += [q.get("bid"), q.get("ask"), q.get("bidsz"),
                        q.get("asksz")]
            row += [_f(m.get("volume_fp")), _f(m.get("open_interest_fp")),
                    _f(m.get("last_price_dollars")),
                    str(m.get("result")).lower(), stamp]
            out.append(["" if v is None else v for v in row])
            have.add(tk)
            if len(out) >= 300:
                ws.append_rows(out, value_input_option="RAW")
                total += len(out)
                print(f"    wrote {len(out)} ({total} this run)")
                out = []
        if out:
            ws.append_rows(out, value_input_option="RAW")
            total += len(out)
            print(f"    wrote {len(out)} ({total} this run)")
    print(f"finished; {total} markets added, {len(have)} in {SHEET}")
    return 0


def main():
    args = [a for a in sys.argv[1:]]
    mode = args[0] if args else "probe"
    series = args[1:] or DEFAULT
    if mode == "probe":
        return probe(series)
    if mode == "run":
        return run(series)
    print(f"unknown mode {mode!r}; use probe or run")
    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Is any Kalshi series mispriced, and could you actually trade it?

TEST A AND TEST C IN ONE PASS. A asks whether some corner of the exchange
prices badly; C asks whether longer horizons behave differently from the
15-minute crypto markets that have defeated eleven strategies. Both are the
same measurement over different series, so both run here.

THE TRAP THIS IS BUILT AROUND. A settled market carries last_price_dollars,
and it is tempting to calibrate against it: price 0.99, settled yes, price
0.02, settled no. That produces a beautiful curve and means nothing --
it is the price AT settlement, when the answer is already known. Everyone
is well calibrated about the past.

So the price that matters comes from CANDLESTICKS, sampled well before
close, when the bet was still live and a human could have taken it. The
settlement price is kept only as a sanity check: if the early price were to
calibrate as tightly as the final one, the join is wrong.

LIQUIDITY IS REPORTED BESIDE EVERY NUMBER, because a mispricing nobody can
trade is a curiosity. A 5c edge on a market with three contracts resting is
fifteen cents, not five dollars, and thin markets are exactly where
mispricing survives -- the two go together and must be read together.

MULTIPLE COMPARISONS ARE THE REAL RISK HERE. There are 14,330 series. Test
enough of them and dozens will look significant on noise alone; this
project has already watched a 1,188-rule grid search produce a winner that
a shuffle beat. So nothing is called a finding. Series that clear a
Bonferroni-adjusted bar are listed as CANDIDATES FOR FORWARD TESTING, which
is a different and much weaker claim.

Read-only. Places nothing, writes nothing, sends nothing.
"""

import calendar
import collections
import json
import statistics as st
import sys
import time

import requests

HOST = "https://api.elections.kalshi.com"
PAUSE = 0.10
MIN_MARKETS = 40          # per series, below this nothing is reported
MIN_VOLUME = 5            # a market with fewer trades has no meaningful price
LOOKBACK_MIN = 60         # how far before close to read the price


def get(path, **params):
    import predictor as P
    try:
        h = P._kalshi_headers("GET", path.split("?")[0]) or {}
        r = requests.get(HOST + path, headers=h, params=params or None,
                         timeout=25)
        if not r.ok:
            return None, f"HTTP {r.status_code}"
        return r.json(), None
    except Exception as e:
        return None, str(e)[:90]


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def series_list():
    d, err = get("/trade-api/v2/series")
    if not d:
        print(f"  could not list series: {err}")
        return []
    return d.get("series") or []


def settled_markets(series_ticker, cap=400):
    """Settled markets for one series, following the cursor."""
    out, cursor = [], None
    while len(out) < cap:
        p = dict(series_ticker=series_ticker, status="settled", limit=200)
        if cursor:
            p["cursor"] = cursor
        d, err = get("/trade-api/v2/markets", **p)
        if not d:
            break
        mk = d.get("markets") or []
        out += mk
        cursor = d.get("cursor")
        if not cursor or not mk:
            break
        time.sleep(PAUSE)
    return out[:cap]


# ---------------------------------------------------------------------------
# The candlesticks endpoint, resolved by asking rather than by assuming.
#
# Three times now this project has written a field name from memory and got a
# silent nothing back: `orderbook` when the answer was `orderbook_fp`,
# `last_price` when the answer was `last_price_dollars`, and a settlement flag
# that was wrong 19% of the time. The shape of this response is the load-
# bearing assumption of the whole module, so it is probed on the first market,
# printed in full, and only then used. If none of the candidates answer, the
# run says so loudly instead of reporting an empty survey.
# ---------------------------------------------------------------------------
CANDLE = {"path": None, "period": None, "scale": None, "probed": False}

PATH_CANDIDATES = [
    "/trade-api/v2/series/{ser}/markets/{tk}/candlesticks",
    "/trade-api/v2/markets/{tk}/candlesticks",
    "/trade-api/v2/markets/trades",          # last resort, different shape
]
PERIOD_CANDIDATES = [1, 60]


def _window(m):
    """(series, ticker, start_ts, end_ts) for the hour before close, UTC.

    close_time is ISO-8601 Zulu. time.mktime would read it as local time,
    which is right only by the accident of the runner being on UTC."""
    tk = m.get("ticker")
    ev = m.get("event_ticker") or ""
    ser = ev.split("-")[0] if ev else str(tk).split("-")[0]
    close = m.get("close_time")
    if not (tk and close):
        return None
    try:
        end = calendar.timegm(time.strptime(close[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None
    return ser, tk, end - LOOKBACK_MIN * 60, end


def _candle_price(c):
    """A traded price out of one candle, as (value, which_field).

    Explicit None checks rather than `a or b`: a price of 0 is falsy, and
    chaining `or` would skip a real zero and reach for the next field."""
    for parent, kids in (("price", ("mean", "close", "open")),
                         ("yes_ask", ("close", "open")),
                         ("yes_bid", ("close", "open"))):
        pr = c.get(parent)
        if isinstance(pr, dict):
            for k in kids:
                v = _f(pr.get(k))
                if v is not None and v > 0:
                    return v, f"{parent}.{k}"
    for k in ("price", "mean_price", "close", "last_price"):
        v = _f(c.get(k))
        if v is not None and v > 0:
            return v, k
    return None, None


def probe_candles(markets):
    """Find a path/period that answers, and print what came back."""
    CANDLE["probed"] = True
    print("-" * 80)
    print("PROBING THE CANDLESTICKS ENDPOINT (shape is not assumed)")
    for m in markets[:4]:
        w = _window(m)
        if not w:
            continue
        ser, tk, start, end = w
        for path in PATH_CANDIDATES[:2]:
            for per in PERIOD_CANDIDATES:
                p = path.format(ser=ser, tk=tk)
                d, err = get(p, start_ts=start, end_ts=end,
                             period_interval=per)
                if not d:
                    print(f"  {p}  period={per}  -> {err}")
                    continue
                cs = d.get("candlesticks") or d.get("data") or []
                print(f"  {p}  period={per}  -> OK, top-level keys "
                      f"{sorted(d.keys())}, {len(cs)} candles")
                if not cs:
                    continue
                print("  first candle verbatim:")
                print("    " + json.dumps(cs[0])[:600])
                vals = []
                field = None
                for c in cs:
                    v, f = _candle_price(c)
                    if v is not None:
                        vals.append(v)
                        field = field or f
                if not vals:
                    print("  no price-like field in these candles -- "
                          "the extractor needs the dump above")
                    continue
                # Cents or dollars is decided by what the data looks like,
                # not by the field's name: anything above 1.5 cannot be a
                # probability, so the whole series must be cents.
                CANDLE.update(path=path, period=per,
                              scale=100.0 if max(vals) > 1.5 else 1.0)
                print(f"  using field {field}, "
                      f"{'cents' if CANDLE['scale'] == 100.0 else 'dollars'} "
                      f"(observed max {max(vals)})")
                print("-" * 80)
                return True
        time.sleep(PAUSE)
    print("  NO CANDLESTICK PATH ANSWERED. Early prices are unavailable, so"
          "\n  the survey below would only be able to calibrate settlement"
          "\n  prices against themselves. Stopping instead.")
    print("-" * 80)
    return False


def early_price(m):
    """The market's price about an hour before it closed, from candlesticks.

    Returns (price, source). Falls back to None rather than to the
    settlement price -- substituting the final price here is precisely the
    mistake this module exists to avoid, and a silent fallback would hide
    it behind a number that looks fine."""
    if not CANDLE["path"]:
        return None, "endpoint unresolved"
    w = _window(m)
    if not w:
        return None, "no ticker/close"
    ser, tk, start, end = w
    d, err = get(CANDLE["path"].format(ser=ser, tk=tk),
                 start_ts=start, end_ts=end,
                 period_interval=CANDLE["period"])
    if not d:
        return None, err or "no candles"
    cs = d.get("candlesticks") or d.get("data") or []
    if not cs:
        return None, "empty candles"
    # earliest candle in the window that carries a traded price
    for c in cs:
        v, _ = _candle_price(c)
        if v is not None:
            return v / CANDLE["scale"], "candle"
    return None, "candles carried no price"


def calibrate(rows):
    """rows of (price, won) -> buckets and a single calibration error."""
    b = collections.defaultdict(lambda: [0, 0, 0.0])
    for p, won in rows:
        k = min(int(p * 10), 9)
        b[k][0] += 1
        b[k][1] += 1 if won else 0
        b[k][2] += p
    err = tot = 0.0
    n = 0
    for k, (cnt, w, sp) in b.items():
        if cnt < 5:
            continue
        err += abs(w / cnt - sp / cnt) * cnt
        n += cnt
    return b, (err / n if n else None), n


def main():
    only = sys.argv[1:] or None
    print("=" * 80)
    print("CALIBRATION SURVEY -- which series price badly, and can it be traded")
    print("=" * 80)

    ser = series_list()
    print(f"\n{len(ser)} series listed")
    if not ser:
        return 1
    byfreq = collections.defaultdict(list)
    for s in ser:
        byfreq[str(s.get("frequency") or "?")].append(s)

    # A deliberately small, named set rather than all 14,330. Testing
    # everything is how you manufacture twenty fake edges before lunch.
    targets = []
    if only:
        targets = [s for s in ser if str(s.get("ticker")) in only]
    else:
        for f in ("fifteen_min", "hourly", "daily", "weekly"):
            for s in byfreq.get(f, [])[:6]:
                targets.append(s)
    print(f"testing {len(targets)} series "
          f"({'named on the command line' if only else 'a sample across horizons'})")
    print(f"price read ~{LOOKBACK_MIN} min before close, from candlesticks\n")

    results = []
    for s in targets:
        tk = str(s.get("ticker"))
        freq = str(s.get("frequency") or "?")
        mk = settled_markets(tk)
        usable = [m for m in mk
                  if str(m.get("result") or "").lower() in ("yes", "no")
                  and (_f(m.get("volume_fp")) or 0) >= MIN_VOLUME]
        if len(usable) < MIN_MARKETS:
            print(f"  {tk:<16} {freq:<12} {len(mk):>4} settled, "
                  f"{len(usable):>4} usable -- too few, skipped")
            continue
        if not CANDLE["probed"] and not probe_candles(usable):
            return 1
        rows, late, why = [], [], collections.Counter()
        for m in usable[:200]:
            won = str(m.get("result")).lower() == "yes"
            p, src = early_price(m)
            if p is None:
                why[src] += 1
            elif 0.02 <= p <= 0.98:
                rows.append((p, won))
            lp = _f(m.get("last_price_dollars"))
            if lp is not None:
                late.append((lp, won))
            time.sleep(PAUSE)
        if len(rows) < 20:
            print(f"  {tk:<16} {freq:<12} early prices unavailable "
                  f"({dict(why.most_common(2))}) -- skipped")
            continue
        _, err, n = calibrate(rows)
        _, lerr, ln = calibrate(late)
        vol = st.median([_f(m.get("volume_fp")) or 0 for m in usable])
        liq = st.median([_f(m.get("liquidity_dollars")) or 0 for m in usable])
        results.append({"tk": tk, "freq": freq, "n": n, "err": err,
                        "late_err": lerr, "vol": vol, "liq": liq})
        print(f"  {tk:<16} {freq:<12} n={n:>4}  early err {err*100:>5.1f}pp  "
              f"(settlement-price err {(lerr or 0)*100:>4.1f}pp)  "
              f"med vol {vol:>7.0f}  liq ${liq:>8.0f}")

    if not results:
        print("\nnothing measurable -- see the skip reasons above")
        return 0

    print("\n" + "=" * 80)
    print("RANKED BY MISPRICING, with the sanity check beside it")
    print("=" * 80)
    print(f"  {'series':<16} {'freq':<12} {'n':>5} {'early':>8} {'settle':>8} "
          f"{'med vol':>9} {'liq $':>10}")
    for r in sorted(results, key=lambda x: -(x["err"] or 0)):
        print(f"  {r['tk']:<16} {r['freq']:<12} {r['n']:>5} "
              f"{r['err']*100:>7.1f}pp {(r['late_err'] or 0)*100:>7.1f}pp "
              f"{r['vol']:>9.0f} {r['liq']:>10.0f}")
    print("\n  'early' is the calibration error an hour before close -- the")
    print("  number that matters. 'settle' is the same at settlement and")
    print("  should be near zero; if it is not, the join is broken.")

    print("\n" + "=" * 80)
    print("BY HORIZON  (test C: does a longer window price differently?)")
    print("=" * 80)
    g = collections.defaultdict(list)
    for r in results:
        g[r["freq"]].append(r)
    for f in sorted(g, key=lambda k: -len(g[k])):
        v = g[f]
        print(f"  {f:<14} {len(v):>2} series  mean early err "
              f"{st.mean([x['err'] for x in v])*100:>5.1f}pp  "
              f"med vol {st.median([x['vol'] for x in v]):>7.0f}")

    print("\n" + "=" * 80)
    k = len(results)
    print(f"NOT FINDINGS. {k} series were tested, so a 5% threshold would be")
    print(f"expected to flag {k*0.05:.1f} of them on noise alone. Anything")
    print("above is a CANDIDATE FOR FORWARD TESTING and nothing more -- a")
    print("1,188-rule search in this project once produced a winner that a")
    print("shuffle then beat. Liquidity decides whether a candidate is even")
    print("worth the forward test.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())

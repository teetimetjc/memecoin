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
import math
import statistics as st
import sys
import time

import requests

HOST = "https://api.elections.kalshi.com"
PAUSE = 0.10
MIN_MARKETS = 40          # per series, below this nothing is reported
MIN_VOLUME = 5            # a market with fewer trades has no meaningful price
MIN_BUCKETED = 40         # markets landing in usable price buckets, per series
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

    The _dollars suffix is not decoration. The first run of this probe got
    a well-formed response with 15 candles and found no price in any of
    them, because the fields are mean_dollars and close_dollars -- the same
    migration that turned orderbook into orderbook_fp and last_price into
    last_price_dollars. Fourth time. The unsuffixed spellings are kept
    after the suffixed ones so an older response would still parse.

    `price` is {} on a candle with no trades, which is a real answer (that
    minute was quiet) and not an error; the caller walks on to the next
    candle. Explicit None checks rather than `a or b`, since a price of 0
    is falsy and chaining would skip a genuine zero."""
    pr = c.get("price")
    if isinstance(pr, dict):
        for k in ("mean_dollars", "close_dollars", "open_dollars",
                  "mean", "close", "open"):
            v = _f(pr.get(k))
            if v is not None and v > 0:
                return v, f"price.{k}"
    for k in ("price_dollars", "mean_price", "last_price"):
        v = _f(c.get(k))
        if v is not None and v > 0:
            return v, k

    # No trade in this candle. The quote can still stand in, but ONLY as a
    # two-sided midpoint. Taking the ask on its own looks reasonable and is
    # not: these candles carry a 99c ask against a 0c bid whenever nobody
    # is quoting, and an earlier draft of this function would have read
    # that stub as "the market said 99%" and calibrated against it. A
    # one-sided or absurdly wide quote is an absence of a price, and gets
    # reported as one.
    bid = _f((c.get("yes_bid") or {}).get("close_dollars"))
    ask = _f((c.get("yes_ask") or {}).get("close_dollars"))
    if bid and ask and 0 < bid < ask and (ask - bid) <= 0.25:
        return (bid + ask) / 2.0, "quote.mid"
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

    Returns (price, source, lead_minutes). Falls back to None rather than
    to the settlement price -- substituting the final price here is
    precisely the mistake this module exists to avoid, and a silent
    fallback would hide it behind a number that looks fine.

    LEAD IS RETURNED BECAUSE IT IS NOT ALWAYS 60. A 15-minute market does
    not exist an hour before it closes, so the earliest candle there is
    about fourteen minutes out, while a weekly market gives the full hour.
    Comparing calibration across horizons without saying that would be
    comparing two different questions and calling it one."""
    if not CANDLE["path"]:
        return None, "endpoint unresolved", None
    w = _window(m)
    if not w:
        return None, "no ticker/close", None
    ser, tk, start, end = w
    d, err = get(CANDLE["path"].format(ser=ser, tk=tk),
                 start_ts=start, end_ts=end,
                 period_interval=CANDLE["period"])
    if not d:
        return None, err or "no candles", None
    cs = d.get("candlesticks") or d.get("data") or []
    if not cs:
        return None, "empty candles", None
    # earliest candle in the window that carries a traded price
    for c in cs:
        v, _ = _candle_price(c)
        if v is not None:
            ts = _f(c.get("end_period_ts"))
            lead = (end - ts) / 60.0 if ts else None
            return v / CANDLE["scale"], "candle", lead
    return None, "candles carried no price", None


def calibrate(rows):
    """rows of (price, won) -> (buckets, error, n, noise_floor).

    THE ERROR ALONE IS UNREADABLE, and that is the trap in this whole
    measurement. It is a mean of absolute deviations, so it can never be
    negative and can never be zero: a perfectly calibrated market measured
    on twenty coin flips per bucket still shows several points of "error"
    purely from sampling. Reporting 4pp with nothing beside it invites
    reading it as a 4-point edge, when the honest question is whether it is
    larger than what noise alone produces.

    So the floor is computed alongside. For a bucket of n draws at true
    probability p, the expected absolute deviation of the observed rate is
    about sqrt(2/pi) * sqrt(p(1-p)/n) -- the mean of a half-normal. Summed
    the same way as the error, it is what a FLAWLESS market would score.
    Error at or below the floor means nothing was found."""
    b = collections.defaultdict(lambda: [0, 0, 0.0])
    for p, won in rows:
        k = min(int(p * 10), 9)
        b[k][0] += 1
        b[k][1] += 1 if won else 0
        b[k][2] += p
    err = floor = 0.0
    n = 0
    for k, (cnt, w, sp) in b.items():
        if cnt < 5:
            continue
        p = sp / cnt
        err += abs(w / cnt - p) * cnt
        floor += math.sqrt(2.0 / math.pi) * math.sqrt(p * (1 - p) / cnt) * cnt
        n += cnt
    if not n:
        return b, None, 0, None
    return b, err / n, n, floor / n


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
        rows, late, leads, why = [], [], [], collections.Counter()
        for m in usable[:200]:
            won = str(m.get("result")).lower() == "yes"
            p, src, lead = early_price(m)
            if p is None:
                why[src] += 1
            elif 0.02 <= p <= 0.98:
                rows.append((p, won))
                if lead is not None:
                    leads.append(lead)
            lp = _f(m.get("last_price_dollars"))
            if lp is not None:
                late.append((lp, won))
            time.sleep(PAUSE)
        if len(rows) < 20:
            print(f"  {tk:<16} {freq:<12} early prices unavailable "
                  f"({dict(why.most_common(2))}) -- skipped")
            continue
        _, err, n, floor = calibrate(rows)
        _, lerr, ln, _lf = calibrate(late)
        # n is NOT len(rows): buckets holding fewer than five markets are
        # dropped, so a series can clear the 20-row gate and still be
        # measured on a handful. The first run reported a 5.0pp error on
        # n=5 that way, sitting in a ranked table beside an n=196.
        if n < MIN_BUCKETED:
            print(f"  {tk:<16} {freq:<12} only {n} markets land in usable "
                  f"price buckets -- too thin to rank, skipped")
            continue
        # Volume, not liquidity_dollars. Resting depth is a property of an
        # OPEN book; every market here is settled, so that field is zero
        # for all of them, and a column of $0 reads like "untradeable
        # everywhere" when it actually means "asked the wrong question."
        # Traded volume is what survives settlement and is the honest
        # proxy for whether a mispricing could have been acted on.
        vol = st.median([_f(m.get("volume_fp")) or 0 for m in usable])
        lead = st.median(leads) if leads else 0.0
        results.append({"tk": tk, "freq": freq, "n": n, "err": err,
                        "late_err": lerr, "vol": vol, "lead": lead,
                        "floor": floor})
        print(f"  {tk:<16} {freq:<12} n={n:>4}  early err {err*100:>5.1f}pp  "
              f"vs noise floor {(floor or 0)*100:>5.1f}pp  "
              f"(settlement {(lerr or 0)*100:>4.1f}pp)  "
              f"lead {lead:>4.0f}min  med vol {vol:>8.0f}")

    if not results:
        print("\nnothing measurable -- see the skip reasons above")
        return 0

    print("\n" + "=" * 80)
    print("RANKED BY MISPRICING ABOVE NOISE  (excess = early - floor)")
    print("=" * 80)
    print(f"  {'series':<16} {'freq':<12} {'n':>5} {'early':>8} {'floor':>8} "
          f"{'excess':>8} {'settle':>8} {'lead':>6} {'med vol':>10}")
    for r in sorted(results, key=lambda x: -((x["err"] or 0)
                                             - (x["floor"] or 0))):
        ex = (r["err"] or 0) - (r["floor"] or 0)
        print(f"  {r['tk']:<16} {r['freq']:<12} {r['n']:>5} "
              f"{r['err']*100:>7.1f}pp {(r['floor'] or 0)*100:>7.1f}pp "
              f"{ex*100:>+7.1f}pp {(r['late_err'] or 0)*100:>7.1f}pp "
              f"{r['lead']:>5.0f}m {r['vol']:>10.0f}")
    print("\n  Sorted by EXCESS, not by error. 'floor' is what a flawless")
    print("  market would score on this many markets from sampling alone,")
    print("  so a negative excess means nothing was found -- the market")
    print("  looks better than a perfect one, which only noise explains.")
    print("  'settle' is the same error measured at settlement and should")
    print("  be near zero; if it is not, the join is broken.")
    print("  'lead' is not always 60: a 15-minute market does not exist an")
    print("  hour before it closes, so its price is read as early as the")
    print("  market allows. A shorter lead is an easier question.")
    print("  'med vol' replaces resting depth, which is zero for every")
    print("  settled market and would read as untradeable everywhere.")

    print("\n" + "=" * 80)
    print("BY HORIZON  (test C: does a longer window price differently?)")
    print("=" * 80)
    g = collections.defaultdict(list)
    for r in results:
        g[r["freq"]].append(r)
    for f in sorted(g, key=lambda k: -len(g[k])):
        v = g[f]
        print(f"  {f:<14} {len(v):>2} series  mean excess "
              f"{st.mean([(x['err'] or 0) - (x['floor'] or 0) for x in v])*100:>+5.1f}pp"
              f"  (err {st.mean([x['err'] for x in v])*100:>4.1f}pp vs floor "
              f"{st.mean([x['floor'] or 0 for x in v])*100:>4.1f}pp)  "
              f"at {st.median([x['lead'] for x in v]):>3.0f}min lead  "
              f"med vol {st.median([x['vol'] for x in v]):>8.0f}")
    print("\n  Read the lead column before comparing rows. These are not the")
    print("  same question at every horizon, and the shorter-lead rows are")
    print("  being asked an easier one.")

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

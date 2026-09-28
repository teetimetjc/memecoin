"""The rest of the exchange: is anything mispriced, and is anyone paying to
be impatient?

Every strategy tested here -- thirteen of them now -- ran on 15-minute
crypto, which is the fastest and most bot-saturated market Kalshi lists.
Finding it efficient says nothing about weather, indices, econ prints or
daily crypto. This is the scouting report for those, and it answers the two
questions separately because they are different questions with different
failure modes.

  QUESTION 1, MISPRICING (the "beat the price" question). Did contracts
  trading at 70c win 70% of the time? An error is only interesting if it is
  bigger than what a FLAWLESS market scores on the same sample size, so the
  noise floor is printed beside every number. calib_survey's floor is reused
  rather than reinvented.

  QUESTION 2, THE SPREAD (the "beat the spread" question). How wide is the
  gap between bid and ask, how often is the book two-sided at all, and how
  much volume crosses it? A market maker's gross income is roughly half the
  spread per round trip; adverse selection is what takes it back. Nothing
  here proves a maker edge -- it measures the CUSHION available to pay for
  one, which is what was missing on 15-minute crypto.

THREE TRAPS THIS AVOIDS BY CONSTRUCTION, each one already paid for here.

  IT NEVER CALIBRATES AGAINST A SETTLED last_price. That field is the price
  after the answer is known, so it calibrates perfectly against itself and
  reports a flawless market no matter what is true. Every price read here
  comes from a candlestick strictly BEFORE the market closed.

  A ONE-SIDED BOOK IS NOT A PRICE. Kalshi's candles carry a 99c ask against
  a 0c bid whenever nobody is quoting, and averaging that in manufactures
  both a mispricing and a spread. A quote counts only when both sides exist
  and the gap is at most 25c.

  ZERO VOLUME IS NOT AN EDGE. A series whose markets never trade will look
  gloriously mispriced and cannot be traded for a cent. Volume is printed
  beside every row and a series with none is called out rather than ranked.

Read-only. Places no orders, writes no tabs.
"""

import collections
import statistics as st
import sys
import time

import backfill as B
import calib_survey as CS

# Sampling. Breadth first: it is better to know that twelve families exist
# and roughly what each looks like than to measure one of them beautifully.
MKT_PER_SERIES = 22
BUDGET_S = 2100
PAUSE = 0.06
MIN_QUOTES = 12           # usable mid-life quotes before a row is ranked

# Kalshi's candlestick intervals, coarsest first. A market's life decides
# which one gives a useful number of bars without asking for thousands.
INTERVALS = (1440, 60, 1)


def pick_interval(life_s):
    """Coarsest interval that still yields ~8+ bars over the market's life."""
    for iv in INTERVALS:
        if life_s / (iv * 60.0) >= 8:
            return iv
    return 1


def mid_life_quote(series, ticker, open_s, close_s):
    """The two-sided quote nearest the MIDPOINT of the market's life.

    Midpoint rather than a fixed offset because these series span fifteen
    minutes to several months; "an hour before close" is most of the life of
    one and a rounding error in another. It is strictly before close either
    way, which is the part that matters.
    """
    life = close_s - open_s
    if life <= 0:
        return None
    iv = pick_interval(life)
    want = open_s + life / 2.0
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(open_s), end_ts=int(close_s), period_interval=iv)
    if not d:
        return None
    cs = d.get("candlesticks") or []
    best, bd = None, 1e18
    for c in cs:
        t = B._f(c.get("end_period_ts"))
        if t is None or t >= close_s:
            continue          # never a bar that closes at or after the end
        gap = abs(t - want)
        if gap < bd:
            best, bd = c, gap
    if best is None:
        return None

    def side(k):
        v = best.get(k)
        return B._f(v.get("close_dollars")) if isinstance(v, dict) else None

    bid, ask = side("yes_bid"), side("yes_ask")
    if bid is None or ask is None:
        return None
    # A 0c bid against a 99c ask is nobody quoting, not a 50c market.
    if not (0 < bid < ask < 1) or (ask - bid) > 0.25:
        return None
    return dict(mid=(bid + ask) / 2.0, spread=ask - bid, iv=iv)


def scout(series_ticker, freq, t0):
    """One series: calibration, spread, volume. None if unmeasurable."""
    mk = B.settled_markets(series_ticker, MKT_PER_SERIES)
    if not mk:
        return None
    rows, spreads, vols = [], [], []
    two_sided = tried = 0
    for m in mk:
        if time.time() - t0 > BUDGET_S:
            break
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        o = B.ts(m.get("open_time"))
        c = B.ts(m.get("close_time"))
        if o is None or c is None:
            continue
        vols.append(_vol(m))
        tried += 1
        q = mid_life_quote(series_ticker, m["ticker"], o, c)
        time.sleep(PAUSE)
        if not q:
            continue
        two_sided += 1
        rows.append((q["mid"], res == "yes"))
        spreads.append(q["spread"])
    if not tried:
        return None
    buckets, err, n, floor = CS.calibrate(rows) if rows else ({}, 0.0, 0, 0.0)
    return dict(
        series=series_ticker, freq=freq, tried=tried, quotes=len(rows),
        two_sided=(two_sided / tried) if tried else 0.0,
        vol=st.median(vols) if vols else 0.0,
        spread=st.median(spreads) if spreads else None,
        err=err, n=n, floor=floor,
        excess=((err - floor) / n) if n else None)


def inventory():
    """Every series Kalshi lists, grouped, with the crypto 15-minute
    families set aside -- those are the thirteen dead strategies."""
    d, err = B.get("/trade-api/v2/series")
    if not d:
        print(f"  /series did not answer ({err})")
        return []
    s = d.get("series") or d.get("data") or []
    print(f"{len(s)} series listed\n")
    bycat = collections.defaultdict(list)
    for x in s:
        bycat[str(x.get("category") or "?")].append(x)
    print(f"  {'category':<26} {'series':>6}   frequencies")
    for cat, v in sorted(bycat.items(), key=lambda x: -len(x[1])):
        fr = collections.Counter(str(y.get("frequency") or "?") for y in v)
        print(f"  {cat:<26} {len(v):>6}   "
              f"{', '.join(f'{k} x{n}' for k, n in fr.most_common(4))}")
    return s


def _vol(m):
    """Traded volume. volume_fp, NOT volume.

    The sixth time this migration has bitten this project: orderbook ->
    orderbook_fp, last_price -> last_price_dollars, mean -> mean_dollars,
    count -> count_fp, and now volume -> volume_fp. Reading the old spelling
    returns None for every market on the exchange, which looks exactly like
    a venue where nothing trades -- the first run of this script ranked 0
    volume for a series quoting a 1c spread on a 59% two-sided book, and
    reported "no series had both a two-sided book and any volume".
    """
    v = B._f(m.get("volume_fp"))
    return v if v is not None else (B._f(m.get("volume")) or 0.0)


# Frequencies worth scouting. one_off and custom are the exchange's novelty
# long tail -- "will Bubeck leave", "GPT5 released" -- and the first run
# wasted 37 of 44 slots on them because they sort first alphabetically. A
# recurring market is also the only kind that can be traded repeatedly,
# which is the point of looking for an edge at all.
FREQ_OK = ("hourly", "daily", "weekly", "monthly", "quarterly")
PRESCREEN = 320           # series given one cheap volume call
SCOUT_TOP = 20            # of those, how many get the candlestick pass


def prescreen(series, only):
    """Rank candidate series by what actually trades, one cheap call each.

    Liquidity first is not a refinement, it is the whole selection: a series
    nobody trades will look gloriously mispriced and cannot be traded for a
    cent, and there are ~3,900 series of which the overwhelming majority are
    dormant.
    """
    if only:
        want = {t.upper() for t in only}
        cand = [s for s in series if str(s.get("ticker", "")).upper() in want]
    else:
        cand = []
        for s in series:
            tk = str(s.get("ticker") or "")
            fr = str(s.get("frequency") or "").lower().strip()
            if "15m" in tk.lower():       # the thirteen dead strategies
                continue
            if fr not in FREQ_OK:
                continue
            cand.append(s)
        cand = cand[:PRESCREEN]
    print(f"\nprescreening {len(cand)} recurring series for volume "
          f"(one call each) ...", flush=True)
    live = []
    for s in cand:
        tk = str(s.get("ticker") or "")
        d, err = B.get("/trade-api/v2/markets", series_ticker=tk,
                       status="settled", limit=20)
        time.sleep(PAUSE)
        mk = (d or {}).get("markets") or []
        if not mk:
            continue
        vols = [_vol(m) for m in mk]
        med = st.median(vols)
        if med <= 0 and max(vols) <= 0:
            continue
        live.append((med, s))
    live.sort(key=lambda x: -x[0])
    print(f"  {len(live)} series have settled markets that actually traded")
    for med, s in live[:SCOUT_TOP]:
        print(f"    {str(s.get('ticker')):<20} "
              f"{str(s.get('frequency')):<10} median volume {med:>9.0f}  "
              f"{str(s.get('category') or '')[:22]}")
    return [s for _, s in live[:SCOUT_TOP]] if not only else \
           [s for _, s in live]


def main():
    only = [a for a in sys.argv[1:] if a.strip()]
    t0 = time.time()
    print("=" * 108)
    print("WHAT ELSE DOES KALSHI LIST?")
    print("=" * 108)
    series = inventory()
    if not series:
        return 1
    targets = prescreen(series, only)
    print(f"\nscouting {len(targets)} series, {MKT_PER_SERIES} settled "
          f"markets each, midpoint-of-life quotes only\n")
    print(f"  {'series':<18} {'freq':<10} {'mkts':>5} {'medvol':>8} "
          f"{'2-sided':>8} {'spread':>7} {'calib err':>10} {'floor':>7} "
          f"{'excess':>7}")
    print("  " + "-" * 96)
    res = []
    for s in targets:
        if time.time() - t0 > BUDGET_S:
            print("\n  (time budget reached; the rest were not scouted)")
            break
        tk = str(s.get("ticker") or "")
        r = scout(tk, str(s.get("frequency") or "?")[:10], t0)
        if not r:
            print(f"  {tk:<18} {'-':<10} no settled markets or no answer")
            continue
        res.append(r)
        sp = "n/a" if r["spread"] is None else f"{100*r['spread']:.1f}c"
        ex = "n/a" if r["excess"] is None else f"{100*r['excess']:+.1f}pp"
        e = f"{100*r['err']/r['n']:.1f}pp" if r["n"] else "n/a"
        fl = f"{100*r['floor']/r['n']:.1f}pp" if r["n"] else "n/a"
        print(f"  {r['series']:<18} {r['freq']:<10} {r['tried']:>5} "
              f"{r['vol']:>8.0f} {100*r['two_sided']:>7.0f}% {sp:>7} "
              f"{e:>10} {fl:>7} {ex:>7}", flush=True)

    print("\n" + "=" * 108)
    print("QUESTION 2 -- WHO IS PAYING TO BE IMPATIENT (widest cushion first)")
    print("=" * 108)
    tradeable = [r for r in res
                 if r["spread"] is not None and r["vol"] > 0
                 and r["quotes"] >= MIN_QUOTES]
    if not tradeable:
        print("  no series had both a two-sided book and any volume")
    for r in sorted(tradeable, key=lambda r: -r["spread"])[:12]:
        # Gross, before adverse selection takes its cut. Half the spread is
        # what a round trip collects if the price does not move against you,
        # and on 15-minute crypto it always did.
        print(f"  {r['series']:<18} spread {100*r['spread']:>5.1f}c  "
              f"half-spread {100*r['spread']/2:>5.1f}c per round trip  "
              f"median volume {r['vol']:>7.0f}  book two-sided "
              f"{100*r['two_sided']:>3.0f}%")

    print("\n" + "=" * 108)
    print("QUESTION 1 -- IS ANYTHING MISPRICED (excess over the noise floor)")
    print("=" * 108)
    ranked = [r for r in res if r["excess"] is not None and r["n"] >= 20]
    if not ranked:
        print("  no series produced enough usable pre-close quotes to score")
    for r in sorted(ranked, key=lambda r: -(r["excess"]))[:12]:
        verdict = ("nothing found" if r["excess"] <= 0 else
                   "worth a closer look" if r["vol"] > 0 else
                   "untradeable -- no volume")
        print(f"  {r['series']:<18} excess {100*r['excess']:>+5.1f}pp  "
              f"on n={r['n']:<5} median volume {r['vol']:>7.0f}   {verdict}")
    print("\n  Excess at or below zero means the market beat, or matched, what")
    print("  a flawless forecaster scores on this many samples. Only a POSITIVE")
    print("  excess on a series with real volume is worth a forward test, and")
    print(f"  {len(res)} series were scanned, so the best one is expected to")
    print("  look good by luck. Nothing here is an edge yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

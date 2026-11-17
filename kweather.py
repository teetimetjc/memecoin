"""Kalshi temperature markets against the NWS forecast. Places nothing.

THE IDEA, AND WHY IT IS DIFFERENT FROM EVERYTHING ELSE HERE. Every strategy
this project has tested needed us to predict better than the market: the
price of Bitcoin in fifteen minutes, which memecoin survives, which trader
stays hot. All five came back at or below zero. Temperature markets settle
on a PUBLISHED NUMBER -- the official high at a named station -- and the US
National Weather Service publishes a free forecast of that number. So for
once there is a knowable quantity to price against rather than a price to
out-guess.

THE HONEST PROBLEM, STATED BEFORE ANY DATA. The forecast is public. Everyone
in those markets can read the same bulletin, so the price probably already
reflects it. For this to work we would have to turn forecasts into
probabilities better than people already doing exactly that. My prior is
that it fails, and I would rather write that here than discover my own
enthusiasm later.

WHAT THIS FILE DOES FIRST: NOTHING BUT LOOK. Kalshi's temperature series,
their strike structure, and the NWS forecast fields are all unknown to me,
and this project has burned days on code written against assumed field
names -- amount_cents read as amount_dollars, orderbook_fp read as
orderbook, usdc_size read as usdcSize. So mode=probe prints what both APIs
actually return and designs nothing.

THE TEST IT BUILDS TOWARD, pre-registered now so it cannot drift:

  RULE: buy the temperature bucket that CONTAINS the NWS forecast high,
        when its ask is below a threshold.
  GRADE: hit rate minus (mean price + fee), the same break-even arithmetic
         papercheck uses, because a high hit rate at a high price is how
         this account already lost money once.

No probability model is fitted in v1. A distribution over buckets would
need an assumed forecast error, and choosing that error after seeing the
prices is how a measurement becomes a story.

READ-ONLY. It places no order and needs no wallet.
"""
import collections
import json
import os
import time

import backfill as B

MODE = (os.environ.get("KWEATHER_MODE") or "probe").lower()
# A generic client name. The NWS asks automated clients to identify
# themselves; it does not need, and will not get, anything about the person
# running this.
UA = "kalshi-weather-research/1.0"

# Cities to try, with coordinates for the NWS gridpoint lookup. Kalshi's
# own tickers are discovered rather than guessed -- reckon showed a KXHIGHNY
# in the account's history, so the naming is at least partly known, but the
# full set is not.
CITIES = {
    "NY": (40.7790, -73.9692),      # Central Park
    "CHI": (41.9603, -87.9316),     # O'Hare
    "LAX": (33.9381, -118.3889),    # LAX
    "MIA": (25.7905, -80.3164),     # Miami Intl
    "DEN": (39.8466, -104.6562),    # Denver Intl
    "AUS": (30.1830, -97.6799),     # Austin
    "PHIL": (39.8700, -75.2300),    # Philadelphia Intl
}


def nws(path_or_url, tries=3):
    import requests
    url = (path_or_url if path_or_url.startswith("http")
           else f"https://api.weather.gov{path_or_url}")
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=30, headers={
                "User-Agent": UA, "accept": "application/geo+json"})
            if r.ok:
                return r.json(), None
            last = f"HTTP {r.status_code}"
        except Exception as e:
            last = str(e)[:90]
        time.sleep(1.5 * (i + 1))
    return None, last


def probe():
    print("=" * 104)
    print("PROBE -- what do these two APIs actually return?")
    print("=" * 104)
    print("  Nothing is designed here. This project has lost days to code")
    print("  written against assumed field names, so the fields get looked")
    print("  at first.\n")

    # ------------------------------------------------- 1. Kalshi's series
    d, err = B.get("/trade-api/v2/series")
    ser = [str(s.get("ticker") or "")
           for s in ((d or {}).get("series") or [])]
    if not ser:
        print(f"  NO SERIES RETURNED ({err}). Nothing can be probed.")
        return 1
    # NO MORE KEYWORD HEURISTICS. Round one matched "LOW" and found
    # KXADMITLOWGROUP (college admissions). Round two matched "HIGH" and
    # found KXBRINFHIGH (Brazil inflation, "Above 5.25%"). Both are
    # perfectly good markets and neither is a temperature. The city series
    # are named plainly, so they are NAMED here and anything not found is
    # reported as not found rather than silently replaced by a near-miss.
    WANT = ("HIGHNY", "HIGHCHI", "HIGHMIA", "HIGHAUS", "HIGHUS",
            "KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHAUS",
            "KXHIGHDEN", "KXDENHIGH", "KXHIGHPHIL", "KXHIGHLAX")
    have = set(ser)
    hits = [t for t in WANT if t in have]
    missing = [t for t in WANT if t not in have]
    print(f"  {len(ser):,} series on the exchange")
    print(f"  named temperature series present: {hits}")
    print(f"  named but NOT on the exchange:    {missing}")
    for t in hits[:40]:
        print(f"    {t}")
    if len(hits) > 40:
        print(f"    ... and {len(hits) - 40} more")

    # --------------------------------- 2. one series' open market structure
    import openmk
    for cand in hits:
        try:
            mk = openmk.open_markets([cand])
        except Exception as e:
            print(f"\n  {cand}: open_markets failed ({str(e)[:60]})")
            continue
        if not mk:
            continue
        print(f"\n{'-' * 104}")
        print(f"OPEN MARKETS IN {cand}  ({len(mk)} of them)")
        print("-" * 104)
        print(f"  field names on one market:")
        print(f"    {sorted(mk[0].keys())}")
        for m in mk[:8]:
            print(f"\n    ticker      {m.get('ticker')}")
            print(f"    title       {str(m.get('title'))[:80]}")
            print(f"    subtitle    {str(m.get('subtitle'))[:80]}")
            # FIELD NAMES CONFIRMED FROM THE PROBE'S OWN KEY DUMP, not
            # assumed: the quotes are *_dollars and the sizes are *_fp.
            # Reading yes_bid / volume / open_interest returned None on
            # every market, which is the same _fp / _dollars trap that has
            # already cost this project days on volume_fp, orderbook_fp and
            # amount_cents.
            print(f"    strike      floor={m.get('floor_strike')} "
                  f"cap={m.get('cap_strike')} "
                  f"type={m.get('strike_type')} "
                  f"custom={m.get('custom_strike')}")
            print(f"    quotes      bid={m.get('yes_bid_dollars')} "
                  f"ask={m.get('yes_ask_dollars')} "
                  f"bidsz={m.get('yes_bid_size_fp')} "
                  f"asksz={m.get('yes_ask_size_fp')}")
            print(f"    activity    vol={m.get('volume_fp')} "
                  f"vol24={m.get('volume_24h_fp')} "
                  f"oi={m.get('open_interest_fp')}")
            print(f"    subtitles   yes={m.get('yes_sub_title')!r} "
                  f"no={m.get('no_sub_title')!r}")
            print(f"    rules       "
                  f"{str(m.get('rules_primary'))[:150]}")
            print(f"    closes      {m.get('close_time')}")
        break
    else:
        print("\n  NO WEATHER SERIES HAD OPEN MARKETS, so the structure")
        print("  could not be read. That is not evidence against the idea.")

    # ------------------------------------------------ 3. the NWS forecast
    print(f"\n{'-' * 104}")
    print("NWS FORECAST FIELDS")
    print("-" * 104)
    lat, lon = CITIES["NY"]
    pts, e1 = nws(f"/points/{lat},{lon}")
    if not pts:
        print(f"  gridpoint lookup failed: {e1}")
        return 1
    props = (pts or {}).get("properties") or {}
    print(f"  grid {props.get('gridId')} "
          f"{props.get('gridX')},{props.get('gridY')}")
    furl = props.get("forecast")
    print(f"  forecast url: {furl}")
    fc, e2 = nws(furl) if furl else (None, "no url")
    if not fc:
        print(f"  forecast fetch failed: {e2}")
        return 1
    periods = ((fc.get("properties") or {}).get("periods") or [])
    print(f"  {len(periods)} period(s); updated "
          f"{(fc.get('properties') or {}).get('updated')}")
    for pd in periods[:4]:
        print(f"\n    name        {pd.get('name')}")
        print(f"    isDaytime   {pd.get('isDaytime')}")
        print(f"    temperature {pd.get('temperature')} "
              f"{pd.get('temperatureUnit')}")
        print(f"    start/end   {pd.get('startTime')} .. {pd.get('endTime')}")
    print(f"\n  field names on one period:")
    print(f"    {sorted(periods[0].keys()) if periods else 'none'}")

    print(f"\n{'=' * 104}")
    print("  WHAT TO READ OFF THIS. Whether Kalshi's temperature markets")
    print("  exist and are quoted, how their buckets are expressed")
    print("  (floor_strike / cap_strike), and whether the NWS daytime high")
    print("  for the matching date is readable. Only once all three are")
    print("  confirmed is the collector worth writing -- and the rule it")
    print("  grades is already fixed in this file's docstring.")
    print("=" * 104)
    return 0



# The NWS station that corresponds to Kalshi's settlement site. CLINYC is
# the Central Park climate site; KNYC is its NWS station id.
STATION = {"KXHIGHNY": "KNYC", "HIGHNY": "KNYC"}


def day_of(ticker):
    """2026-11-16 from KXHIGHNY-26NOV16-T52, or None."""
    import re
    m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})-", ticker.upper())
    if not m:
        return None
    mons = dict(JAN=1, FEB=2, MAR=3, APR=4, MAY=5, JUN=6, JUL=7, AUG=8,
                SEP=9, OCT=10, NOV=11, DEC=12)
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    if mon not in mons:
        return None
    return f"20{yy}-{mons[mon]:02d}-{int(dd):02d}"


def nws_max_f(station, day):
    """Highest observed temperature in F at `station` on local `day`."""
    d, err = nws(f"/stations/{station}/observations"
                 f"?start={day}T00:00:00-05:00&end={day}T23:59:59-05:00")
    if not d:
        return None, err
    best = None
    for feat in (d.get("features") or []):
        t = ((feat.get("properties") or {}).get("temperature") or {})
        v, unit = t.get("value"), str(t.get("unitCode") or "")
        if v is None:
            continue
        # unitCode is wmoUnit:degC on this endpoint; convert rather than
        # assume, because assuming units is how a 5C reading becomes 5F.
        fv = (v * 9.0 / 5.0 + 32.0) if "degC" in unit else float(v)
        if best is None or fv > best:
            best = fv
    return best, (None if best is not None else "no readings")


def basis_check():
    """Does the free NWS observation agree with Kalshi's settlement?

    THE PREMISE THIS TESTS. Kalshi settles these on the maximum recorded
    "according to The Weather Company", NOT the NWS. The whole plan was to
    price against the free NWS number, so if the two providers disagree by
    a degree or more with any regularity the plan is dead -- the buckets are
    one degree wide, so a one-degree basis is a whole bucket of error.
    """
    print("=" * 104)
    print("BASIS CHECK -- does the free NWS number agree with Kalshi's")
    print("settlement source (The Weather Company)?")
    print("=" * 104)
    series = os.environ.get("KWEATHER_SERIES") or "KXHIGHNY"
    station = STATION.get(series, "KNYC")
    want = int(os.environ.get("KWEATHER_DAYS") or 25)
    print(f"  series {series}, NWS station {station}\n")

    mk = B.settled_markets(series, 600)
    print(f"  {len(mk)} settled market(s) pulled")
    by_day = collections.defaultdict(list)
    for m in mk:
        day = day_of(str(m.get("ticker") or ""))
        if day:
            by_day[day].append(m)
    days = sorted(by_day, reverse=True)[:want]
    print(f"  {len(by_day)} distinct day(s); checking the latest "
          f"{len(days)}\n")

    print(f"  {'date':<12}{'kalshi settled range':>26}{'NWS max F':>11}"
          f"{'agree?':>9}")
    agree = disagree = unknown = 0
    diffs = []
    for day in days:
        yes = []
        for m in by_day[day]:
            if str(m.get("result") or "").lower() != "yes":
                continue
            yes.append(m)
        if not yes:
            unknown += 1
            continue
        # The YES bucket pins the settled value to a range.
        lo = hi = None
        for m in yes:
            fl, cp = f(m.get("floor_strike")), f(m.get("cap_strike"))
            ty = str(m.get("strike_type") or "").lower()
            if ty == "less" and cp is not None:
                lo, hi = (lo if lo is not None else -999), cp - 1
            elif ty == "greater" and fl is not None:
                lo, hi = fl + 1, (hi if hi is not None else 999)
            elif fl is not None and cp is not None:
                lo, hi = fl, cp
        obs, oerr = nws_max_f(station, day)
        if lo is None and hi is None:
            unknown += 1
            continue
        rng = (f"{'' if lo is None or lo < -900 else int(lo)}"
               f"..{'' if hi is None or hi > 900 else int(hi)}")
        if obs is None:
            print(f"  {day:<12}{rng:>26}{'--':>11}{oerr or 'no obs':>9}")
            unknown += 1
            continue
        ok = ((lo is None or lo < -900 or obs >= lo - 0.5)
              and (hi is None or hi > 900 or obs <= hi + 0.5))
        if ok:
            agree += 1
        else:
            disagree += 1
            mid = hi if (lo is None or lo < -900) else (
                lo if (hi is None or hi > 900) else (lo + hi) / 2.0)
            diffs.append(obs - mid)
        print(f"  {day:<12}{rng:>26}{obs:>11.1f}"
              f"{('yes' if ok else 'NO'):>9}")

    tot = agree + disagree
    print(f"\n  agree {agree}, disagree {disagree}, unusable {unknown}")
    if tot:
        print(f"  agreement rate {100.0*agree/tot:.1f}%")
    if diffs:
        diffs.sort()
        print(f"  on disagreements, NWS minus the Kalshi range midpoint: "
              f"median {diffs[len(diffs)//2]:+.1f}F, "
              f"min {diffs[0]:+.1f}, max {diffs[-1]:+.1f}")

    print(f"\n{'=' * 104}")
    if not tot:
        print("  NOTHING COMPARABLE. No day had both a YES bucket and an NWS")
        print("  reading, so the basis is unmeasured -- not small.")
    elif 100.0 * agree / tot >= 90.0:
        print("  THE BASIS IS SMALL. NWS and the settlement source agree on")
        print("  the bucket almost always, so pricing against the free NWS")
        print("  forecast is defensible and the collector is worth writing.")
    else:
        print("  THE BASIS IS TOO BIG. The buckets are one degree wide and")
        print("  the free number disagrees with the settlement source often")
        print("  enough that any 'edge' measured against NWS would mostly")
        print("  be provider disagreement. The plan as designed is dead.")
    print("=" * 104)
    return 0


def main():
    if MODE == "probe":
        return probe()
    if MODE == "basis":
        return basis_check()
    print(f"  mode '{MODE}' is not built yet. Run the probe first; the")
    print("  collector is written against what it finds, not against")
    print("  guesses about field names.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

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
    # THE FIRST PASS MATCHED "LOW" AND LANDED ON KXADMITLOWGROUP -- college
    # admissions rates, 70 unquoted markets closing in 2028. Temperature
    # series are named plainly: HIGHNY, HIGHCHI, HIGHMIA, HIGHAUS, HIGHUS,
    # KXDENHIGH, AVGTEMP. So match those, and exclude the words that pulled
    # in contempt votes, blowouts and hot dogs.
    BAD = ("ADMIT", "CONTEMPT", "BLOWOUT", "HOTDOG", "COLDPLAY", "BRAIN",
           "SNOW", "HOT100", "CASELOW", "TARIFF", "GRAIN", "COLDBREW")
    hits = sorted(t for t in ser
                  if ("HIGH" in t.upper() or "TEMP" in t.upper())
                  and not any(b in t.upper() for b in BAD))
    print(f"  {len(ser):,} series on the exchange, "
          f"{len(hits)} that look like temperature series")
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


def main():
    if MODE == "probe":
        return probe()
    print(f"  mode '{MODE}' is not built yet. Run the probe first; the")
    print("  collector is written against what it finds, not against")
    print("  guesses about field names.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

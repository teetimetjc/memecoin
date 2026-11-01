"""Sports line-gap scanner, phase 1: LOOK FIRST. One snapshot, no matching.

WHAT THIS RUN IS FOR, and why it deliberately does less than the plan says.

The plan is to match Kalshi contracts against a sharp book's de-vigged line
and log the gaps. Matching is the part that decides whether every later
number is real -- same game, same side, same market, same settlement rules
-- and it is being written against two APIs whose actual response shapes
nobody here has looked at yet. Writing a matcher against an imagined shape
is how a log fills up with confident, wrong rows.

So this run answers the questions a matcher needs answered first:

  Is the key there, and does it work?        A missing key must fail loudly,
                                             not produce an empty slate that
                                             reads as "no games today".
  How much quota did that cost?              The free tier is 500 credits a
                                             month and the API meters per
                                             market per region per call, so
                                             the remaining-credits header is
                                             printed on every run.
  IS THE SHARP BOOK ACTUALLY THERE?          Pinnacle is the reference the
                                             whole idea rests on. If it is
                                             absent, comparing Kalshi to
                                             FanDuel mostly measures
                                             FanDuel's margin and shading,
                                             not a Kalshi mispricing. This
                                             says so rather than quietly
                                             using a retail book.
  How stale is each book's line?             Every bookmaker carries a
                                             last_update. A gap computed
                                             against a line from eleven
                                             minutes ago is not a gap. The
                                             age is printed per book.
  What do the two sides actually look like?  Events and prices from both,
                                             printed side by side, so the
                                             matching rules can be written
                                             against real tickers and real
                                             team names.

NOTHING IS LOGGED AND NOTHING IS MATCHED in this mode. A gap number
produced by guessed matching would be worse than no number, because it
would look like a measurement.

THE DE-VIG IS APPLIED ANYWAY, to every two-way market it finds, because the
distribution is the control: most books against most Kalshi prices should
show small differences. If everything shows a huge edge, the arithmetic or
the matching is broken, and that is worth seeing before building anything
on top of it.

Read-only. Places no order, writes no row, changes no setting.
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests

import collect15 as C
import oddsmath as M

ODDS_HOST = "https://api.the-odds-api.com/v4"

# ONE SPORT, ONE MARKET. Credits are metered per market per region per call,
# so every addition here multiplies the monthly burn.
SPORT = os.environ.get("ODDS_SPORT", "americanfootball_nfl").strip()
MARKETS = "h2h"
# Pinnacle is not in the 'us' region on this API; it sits with the
# international books. Both are requested so the sharp reference and the
# retail books arrive together -- at two credits a call instead of one,
# which is the right trade while the question is "do gaps exist at all".
REGIONS = os.environ.get("ODDS_REGIONS", "us,eu").strip()
SHARP = [b.strip().lower() for b in
         (os.environ.get("ODDS_SHARP") or "pinnacle").split(",") if b.strip()]
MAX_EVENTS = int(os.environ.get("LINEGAP_MAX_EVENTS") or 8)
# A line older than this is not a quote, it is a memory.
STALE_S = int(os.environ.get("ODDS_STALE_S") or 300)


def key_or_die():
    k = (os.environ.get("ODDS_API_KEY") or "").strip()
    if not k:
        print("  ODDS_API_KEY is not set.")
        print("  Add it as a repository secret named ODDS_API_KEY and pass it")
        print("  through in the workflow. Nothing was fetched and no quota")
        print("  was spent. This is a hard stop rather than an empty result,")
        print("  because an empty slate is indistinguishable from a quiet day.")
        raise SystemExit(2)
    return k


def fetch_odds(key):
    """One call. Returns (events, quota_headers) or exits loudly."""
    url = f"{ODDS_HOST}/sports/{SPORT}/odds"
    params = dict(apiKey=key, regions=REGIONS, markets=MARKETS,
                  oddsFormat="american")
    try:
        r = requests.get(url, params=params, timeout=25)
    except Exception as e:
        print(f"  the odds request failed outright: {str(e)[:120]}")
        raise SystemExit(1)

    quota = {k: r.headers.get(k) for k in
             ("x-requests-remaining", "x-requests-used", "x-requests-last")}
    if r.status_code == 401:
        print("  HTTP 401 -- the key was rejected. Check the secret's value.")
        print(f"  quota headers: {quota}")
        raise SystemExit(1)
    if r.status_code == 422:
        print(f"  HTTP 422 -- bad parameters. sport={SPORT!r} "
              f"regions={REGIONS!r} markets={MARKETS!r}")
        print(f"  body: {r.text[:200]}")
        raise SystemExit(1)
    if not r.ok:
        print(f"  HTTP {r.status_code}: {r.text[:200]}")
        print(f"  quota headers: {quota}")
        raise SystemExit(1)
    return r.json(), quota


def age_s(iso):
    try:
        t = datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")
        return (datetime.now(timezone.utc)
                - t.replace(tzinfo=timezone.utc)).total_seconds()
    except Exception:
        return None


def main():
    print("=" * 100)
    print("LINE GAP -- PHASE 1 PROBE. Looks at both sides, matches nothing, "
          "logs nothing.")
    print(f"  sport={SPORT}  markets={MARKETS}  regions={REGIONS}  "
          f"sharp reference={'/'.join(SHARP)}")
    print("=" * 100)

    key = key_or_die()
    events, quota = fetch_odds(key)

    print(f"\n  QUOTA  used so far {quota.get('x-requests-used')}   "
          f"remaining {quota.get('x-requests-remaining')}   "
          f"this call cost {quota.get('x-requests-last')}")
    print("  (the free tier is 500 a month, metered per market per region "
          "per call)")
    print(f"\n  {len(events)} upcoming {SPORT} event(s) returned")
    if not events:
        print("  No events. Out of season, or the sport key is wrong --")
        print("  not evidence about gaps either way.")
        return 0

    # WHICH BOOKS ARE ACTUALLY HERE. The whole premise rests on this.
    seen = {}
    for ev in events:
        for b in ev.get("bookmakers") or []:
            seen.setdefault(b.get("key"), 0)
            seen[b["key"]] += 1
    print(f"\n  bookmakers present ({len(seen)}): "
          + ", ".join(sorted(seen)) or "none")
    missing = [s for s in SHARP if s not in seen]
    if missing:
        print(f"\n  *** THE SHARP REFERENCE IS MISSING: {', '.join(missing)}")
        print("  *** Every remaining number would be Kalshi against a RETAIL")
        print("  *** book, which mostly measures that book's margin and its")
        print("  *** shading toward public money -- not a Kalshi mispricing.")
        print("  *** Phase 2 should not be built on this until it is sorted:")
        print("  *** check the plan's region coverage, or name a different")
        print("  *** sharp book in ODDS_SHARP.")
    else:
        print(f"  sharp reference present: {', '.join(SHARP)}")

    print("\n" + "-" * 100)
    print("PER EVENT: the sharp line, de-vigged three ways, and how stale "
          "each book is")
    print("-" * 100)

    shown = 0
    spreads = []
    for ev in events:
        if shown >= MAX_EVENTS:
            break
        books = {b.get("key"): b for b in (ev.get("bookmakers") or [])}
        ref = next((books[s] for s in SHARP if s in books), None)
        if ref is None:
            continue
        mk = next((m for m in (ref.get("markets") or [])
                   if m.get("key") == "h2h"), None)
        if not mk or len(mk.get("outcomes") or []) != 2:
            continue
        outs = mk["outcomes"]
        try:
            raws = [M.american_to_prob(o["price"]) for o in outs]
        except Exception as e:
            print(f"  {ev.get('home_team')} -- unreadable price: {e}")
            continue

        shown += 1
        a = age_s(mk.get("last_update") or ref.get("last_update"))
        stale = "" if (a is None or a <= STALE_S) else "  <- STALE"
        print(f"\n  {ev.get('away_team')} @ {ev.get('home_team')}"
              f"   commences {str(ev.get('commence_time'))[:16]}")
        print(f"    id {ev.get('id')}")
        print(f"    {ref.get('key'):<12} line {outs[0]['price']:>6} / "
              f"{outs[1]['price']:>6}   vig {100*(sum(raws)-1):.2f} pts"
              f"   age {('%.0fs' % a) if a is not None else '?'}{stale}")
        allm = M.devig_all(raws)
        for i, o in enumerate(outs):
            lo, hi, sp = M.fair_consensus(raws, i)
            spreads.append(sp)
            print(f"      {o['name'][:26]:<26} "
                  + "  ".join(f"{n[:4]} {100*allm[n][i]:5.2f}%"
                              for n in ("multiplicative", "shin", "power"))
                  + f"   spread {100*sp:.2f} pts   conservative "
                    f"{100*lo:.2f}%")
        # The retail books beside it, so the margin difference is visible.
        for bk, b in sorted(books.items()):
            if bk in SHARP:
                continue
            m2 = next((m for m in (b.get("markets") or [])
                       if m.get("key") == "h2h"), None)
            if not m2 or len(m2.get("outcomes") or []) != 2:
                continue
            try:
                r2 = [M.american_to_prob(o["price"]) for o in m2["outcomes"]]
            except Exception:
                continue
            a2 = age_s(m2.get("last_update") or b.get("last_update"))
            print(f"    {bk:<12} line {m2['outcomes'][0]['price']:>6} / "
                  f"{m2['outcomes'][1]['price']:>6}   "
                  f"vig {100*(sum(r2)-1):.2f} pts"
                  f"   age {('%.0fs' % a2) if a2 is not None else '?'}")

    if spreads:
        spreads.sort()
        mid = spreads[len(spreads) // 2]
        print("\n" + "-" * 100)
        print(f"  DE-VIG DISAGREEMENT across {len(spreads)} sides: "
              f"median {100*mid:.2f} points, worst {100*spreads[-1]:.2f}")
        print("  Compare that against the 3-point edge threshold before "
              "trusting any gap:")
        print("  if the methods disagree by a point, a 3-point gap is partly "
              "a choice of method.")

    print("\n" + "-" * 100)
    print("THE KALSHI SIDE -- what sports series exist to match against")
    print("-" * 100)
    d, err = C.get("/trade-api/v2/series", limit=200)
    if err:
        print(f"  could not list series: {err}")
    else:
        names = sorted({s.get("ticker", "") for s in (d or {}).get("series", [])
                        if s.get("ticker")})
        hits = [n for n in names if any(
            k in n.upper() for k in ("NFL", "NBA", "GAME", "SPORT"))]
        print(f"  {len(names)} series visible; candidates for this sport:")
        print("   " + (", ".join(hits[:40]) if hits else
                       "none matched NFL/NBA/GAME/SPORT -- "
                       "the series naming needs a look by hand"))

    print("\n" + "=" * 100)
    print("  WHAT HAPPENS NEXT, and what this run deliberately did not do.")
    print()
    print("  It matched nothing. Matching is what decides whether every")
    print("  later number is real -- same game, same side, same settlement")
    print("  rules -- and it should be written against the shapes printed")
    print("  above, not against an imagined response.")
    print()
    print("  Read the Kalshi candidates and the event names together, then")
    print("  phase 2 writes the matcher: exact-match where possible, log the")
    print("  uncertain ones rather than use them, and log EVERY matched")
    print("  market with its edge, not only the big ones -- the distribution")
    print("  is the only control that can tell a working pipeline from a")
    print("  broken one.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

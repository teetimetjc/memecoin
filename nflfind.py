"""Which Kalshi series carries NFL game winners? Found, not guessed.

WHY THIS IS ITS OWN STEP. The probe listed 15,581 series and a keyword
filter on "GAME" returned GAMEAWARDS, KXAPPSTOREIPADGAME and
KXALBUMRELEASENBAYB -- plus KXAFLGAME, which is Australian football and
would have looked exactly right to a matcher built on name similarity.
Picking a ticker by eye from that list is how the line-gap log would fill
with confident rows about the wrong sport.

SO THE EVIDENCE IS THE EVENT, NOT THE NAME. A series only counts here if
its actual open markets name real NFL teams and close around real kickoff
times. That is checkable, and a series that passes it is the right one
whatever it happens to be called.

THREE PASSES, each narrowing on different evidence:

  1. Series whose TITLE mentions the NFL or football. Titles are prose and
     far more honest than tickers, but they are still just words.
  2. Series whose OPEN MARKETS mention two NFL team names. This is the
     real test: thirty-two team names are specific enough that a false
     positive is unlikely, and a series about app-store rankings cannot
     fake it.
  3. For whatever survives, the actual markets printed in full -- ticker,
     title, close time, bid and ask -- so they can be read against the
     Odds API event list by hand before a single automated match exists.

IT ALSO PRINTS THE RAW FIELD NAMES of the first series and market object
it sees. The matcher will address these by key, and guessing a key is the
same error in a smaller costume.

Costs no Odds API quota: this is the Kalshi side only.

Read-only.
"""
import collections
import os
import time

import collect15 as C

# The thirty-two, by the name a market title would use. Matching on two
# distinct names in one title is the test -- one name could be a futures
# market ("will the Lions make the playoffs"), two means a game.
TEAMS = [
    "Cardinals", "Falcons", "Ravens", "Bills", "Panthers", "Bears",
    "Bengals", "Browns", "Cowboys", "Broncos", "Lions", "Packers",
    "Texans", "Colts", "Jaguars", "Chiefs", "Raiders", "Chargers",
    "Rams", "Dolphins", "Vikings", "Patriots", "Saints", "Giants",
    "Jets", "Eagles", "Steelers", "49ers", "Seahawks", "Buccaneers",
    "Titans", "Commanders",
]
# CITY NAMES TOO, BECAUSE THE SECOND RUN STILL CONFIRMED NOTHING. It probed
# KXNFLGAME -- 60 open markets, exactly the series wanted -- and scored it
# "0 naming a team". Nicknames were the whole test, and a Kalshi game market
# may well read "Will Detroit win?" rather than "Will the Lions win?". A test
# that can only see nicknames reports a clean zero on the right series, which
# is the same failure as the first run in a new place: the read returned
# something plausible instead of failing.
CITIES = [
    "Arizona", "Atlanta", "Baltimore", "Buffalo", "Carolina", "Chicago",
    "Cincinnati", "Cleveland", "Dallas", "Denver", "Detroit", "Green Bay",
    "Houston", "Indianapolis", "Jacksonville", "Kansas City", "Las Vegas",
    "Los Angeles", "Miami", "Minnesota", "New England", "New Orleans",
    "New York", "Philadelphia", "Pittsburgh", "San Francisco", "Seattle",
    "Tampa Bay", "Tennessee", "Washington",
]
TITLE_HINTS = ("NFL", "FOOTBALL")
MAX_SERIES_PROBE = int(os.environ.get("NFLFIND_PROBE") or 200)

# RANKING, BECAUSE 629 SERIES MATCHED THE WORD FILTER AND THE FIRST RUN
# PROBED 25 OF THEM IN ARBITRARY ORDER -- so it sampled props and futures
# (next touchdown scorer, season receptions, exact wins Miami) and never
# reached a game-winner market at all. Ordering by plausibility is not a
# shortcut round the evidence: every candidate still has to prove itself on
# its markets. It only decides what gets asked first.
GOOD = ("winner", "game", "to win", "moneyline")
BAD = ("quarter", "1q", "2h", "half", "draft", "award", "mvp", "rookie",
       "player of", "season", "exact wins", "rank", "specials", "pack",
       "location", "next td", "conversion", "reception", "rushing",
       "passing", "yards", "churn", "qualifier", "playoff", "inflation",
       "gas price", "earnings", "alcohol", "redzone", "ads", "college",
       "ncaa")


def score(ticker, title):
    t = f"{ticker} {title}".lower()
    s = 0
    for g in GOOD:
        if g in t:
            s += 3
    for b in BAD:
        if b in t:
            s -= 4
    return s


def teams_in(text):
    t = (text or "").lower()
    return sorted({n for n in (TEAMS + CITIES) if n.lower() in t})


def main():
    print("=" * 100)
    print("FINDING THE NFL SERIES ON KALSHI -- by what its markets say, "
          "not by its name")
    print("=" * 100)

    d, err = C.get("/trade-api/v2/series", limit=1000)
    if err:
        print(f"  could not list series: {err}")
        return 1
    series = (d or {}).get("series") or []
    print(f"\n  {len(series)} series returned")
    if series:
        print(f"  field names on a series object: "
              f"{sorted(series[0].keys())}")

    # PASS 1 -- titles that mention the sport.
    cands = []
    for s in series:
        tk = str(s.get("ticker") or "")
        ti = str(s.get("title") or "")
        blob = f"{tk} {ti}".upper()
        if any(h in blob for h in TITLE_HINTS):
            cands.append((tk, ti))
    # Most promising first. 629 matched the word filter and only a couple of
    # hundred get probed, so the order decides what is reached at all.
    cands.sort(key=lambda c: (-score(c[0], c[1]), c[0]))
    print(f"\n{'-' * 100}")
    print(f"PASS 1 -- {len(cands)} series mention NFL or football, ranked by "
          "how much the title sounds like a game winner")
    print("-" * 100)
    for tk, ti in cands[:25]:
        print(f"  {score(tk, ti):>3}  {tk:<28} {ti[:58]}")
    if not cands:
        print("  none -- the titles do not use those words, so pass 2 has to")
        print("  do the work on its own.")

    # PASS 2 -- the real test: open markets naming two NFL teams.
    print(f"\n{'-' * 100}")
    print("PASS 2 -- which of those actually have open markets naming TWO "
          "NFL teams")
    print("-" * 100)
    confirmed = []
    shape_shown = False
    for tk, ti in cands[:MAX_SERIES_PROBE]:
        md, merr = C.get("/trade-api/v2/markets", series_ticker=tk, limit=60)
        time.sleep(0.15)
        if merr:
            print(f"  {tk:<28} could not read markets ({merr})")
            continue
        mks = (md or {}).get("markets") or []
        if mks and not shape_shown:
            print(f"  field names on a market object: "
                  f"{sorted(mks[0].keys())}\n")
            shape_shown = True
        # ONE TEAM NAME IS THE TEST, NOT TWO. The first version demanded
        # two and confirmed nothing, because a Kalshi game market reads
        # "Will the Lions win?" with yes_sub_title "Lions" -- one name per
        # market, with both teams carried at the EVENT level. Requiring two
        # rejected every real game market while accepting nothing, which is
        # the quietest kind of wrong answer: a clean empty result.
        hits = 0
        example = None
        events = set()
        for m in mks:
            names = teams_in(f"{m.get('title', '')} "
                             f"{m.get('yes_sub_title', '')} "
                             f"{m.get('no_sub_title', '')}")
            if names:
                hits += 1
                events.add(m.get("event_ticker"))
                example = example or (m, names)
        flag = "  <- NAMES NFL TEAMS" if hits else ""
        if hits or len(mks):
            print(f"  {tk:<28} {len(mks):>3} markets, {hits:>3} naming a "
                  f"team, {len(events):>3} event(s){flag}")
            # A SAMPLE TITLE ON EVERY SERIES, hits or not. The last run
            # reported "0 naming a team" on KXNFLGAME, the series actually
            # wanted, and gave nothing to tell a wrong series from a wrong
            # test. One real title makes that difference visible.
            if mks:
                print(f"       e.g. {str(mks[0].get('title'))[:62]!r}  "
                      f"yes={str(mks[0].get('yes_sub_title'))[:24]!r}")
        if hits:
            confirmed.append((tk, ti, hits, example))

    if not confirmed:
        print("\n  Nothing confirmed. Either the NFL series is named without")
        print("  'NFL' or 'football', or its markets are closed right now.")
        print("  Next move is to widen PASS 1 rather than guess a ticker.")
        return 1

    # PASS 3 -- print the survivors in full, for hand-checking.
    print(f"\n{'-' * 100}")
    print("PASS 3 -- the confirmed series, in full, to read against the "
          "Odds API events")
    print("-" * 100)
    for tk, ti, hits, example in sorted(confirmed, key=lambda x: -x[2]):
        print(f"\n  SERIES {tk}   {ti[:70]}")
        print(f"    {hits} open market(s) naming two NFL teams")
        md, _ = C.get("/trade-api/v2/markets", series_ticker=tk, limit=40)
        shown = 0
        for m in ((md or {}).get("markets") or []):
            names = teams_in(f"{m.get('title', '')} {m.get('subtitle', '')} "
                             f"{m.get('yes_sub_title', '')}")
            # ONE NAME IS ENOUGH HERE TOO. Pass 3 still demanded two, so even
            # the series pass 2 flagged printed no markets at all -- the
            # detail that the whole run exists to produce.
            if not names or shown >= 6:
                continue
            shown += 1
            bid, ask = m.get("yes_bid"), m.get("yes_ask")
            print(f"      {str(m.get('ticker'))[:46]:<46} "
                  f"close {str(m.get('close_time'))[:16]}")
            print(f"        event   {m.get('event_ticker')}")
            print(f"        title   {str(m.get('title'))[:76]}")
            if m.get("yes_sub_title"):
                print(f"        yes is  {str(m.get('yes_sub_title'))[:70]}")
            # DEPTH AT THE TOP OF BOOK, which is the question that killed
            # T-3 and oil. A gap with two contracts behind it is not
            # tradeable however wide it looks.
            print(f"        teams   {', '.join(names)}      status "
                  f"{m.get('status')}")
            print(f"        book    bid {m.get('yes_bid_dollars')} "
                  f"(size {m.get('yes_bid_size_fp')})   "
                  f"ask {m.get('yes_ask_dollars')} "
                  f"(size {m.get('yes_ask_size_fp')})")
            if shown == 1 and m.get("rules_primary"):
                # THE SETTLEMENT RULES, printed once in full. Matching the
                # teams and getting the rules wrong is the single most
                # likely way to log gaps that look real and are not --
                # overtime, postponement, and what counts as a result.
                print(f"\n        RULES (read these against the "
                      f"sportsbook's):")
                print(f"        {str(m.get('rules_primary'))[:600]}")
                if m.get("rules_secondary"):
                    print(f"        {str(m.get('rules_secondary'))[:300]}")
                print()

    print("\n" + "=" * 100)
    print("  HOW TO USE THIS. Read the tickers and close times above against")
    print("  the Odds API event list from the probe: same two teams, same")
    print("  kickoff. Where those agree, the matcher has a key it can trust.")
    print()
    print("  AND THE THING TO CHECK BY HAND BEFORE ANY OF IT IS AUTOMATED:")
    print("  what the Kalshi market actually settles on. 'Yes' must mean the")
    print("  same event as the sportsbook's side -- including overtime, and")
    print("  including what happens if the game is postponed. A matcher that")
    print("  gets the teams right and the rules wrong produces gaps that")
    print("  look real and are not, which is the single most likely way for")
    print("  this project to waste a month.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

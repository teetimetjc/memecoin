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
TITLE_HINTS = ("NFL", "FOOTBALL")
MAX_SERIES_PROBE = int(os.environ.get("NFLFIND_PROBE") or 25)


def teams_in(text):
    t = (text or "")
    return sorted({n for n in TEAMS if n.lower() in t.lower()})


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
    print(f"\n{'-' * 100}")
    print(f"PASS 1 -- {len(cands)} series mention NFL or football in ticker "
          "or title")
    print("-" * 100)
    for tk, ti in cands[:40]:
        print(f"  {tk:<28} {ti[:64]}")
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
        hits = 0
        example = None
        for m in mks:
            names = teams_in(f"{m.get('title', '')} {m.get('subtitle', '')} "
                             f"{m.get('yes_sub_title', '')}")
            if len(names) >= 2:
                hits += 1
                example = example or (m, names)
        flag = "  <- NFL GAMES" if hits else ""
        print(f"  {tk:<28} {len(mks):>3} markets, {hits:>3} naming two "
              f"teams{flag}")
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
            if len(names) < 2 or shown >= 6:
                continue
            shown += 1
            bid, ask = m.get("yes_bid"), m.get("yes_ask")
            print(f"      {str(m.get('ticker'))[:46]:<46} "
                  f"close {str(m.get('close_time'))[:16]}")
            print(f"        title   {str(m.get('title'))[:76]}")
            if m.get("yes_sub_title"):
                print(f"        yes is  {str(m.get('yes_sub_title'))[:70]}")
            print(f"        teams   {', '.join(names)}"
                  f"      yes bid {bid}  ask {ask}  status {m.get('status')}")

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

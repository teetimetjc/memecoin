"""Read ONE Kalshi series' markets in full. No ranking, no slicing, no filter.

WHY THIS EXISTS. nflfind.py ranks 629 title-matched series and probes the
top N. Three runs in a row it never printed KXNFLGAME -- the series with 60
open markets and the obvious name for the thing wanted -- because the
ranking put it outside the slice and the team-name filter decided which
markets were worth showing. Each run came back clean and said nothing.

THAT IS THE SAME BUG AS THE OTHER FIFTY: a read that returns something
plausible rather than failing. A heuristic deciding what gets looked at is
fine for discovery and useless for verification, because when the heuristic
is the thing that is wrong it cannot report itself.

SO THIS ASKS FOR ONE TICKER AND PRINTS WHAT COMES BACK. Every market, every
field the matcher will later address by key, the settlement rules in full,
and the depth at the top of book. No scoring, no team list, no "naming a
team" test. If the series is empty, that is a real answer. If it is full of
something other than game winners, that is also a real answer, and either
one is worth more than a third clean zero.

WHAT TO READ IT FOR, in the order these things can kill the project:

  rules_primary        Does Yes mean "wins the game", and what happens on a
                       TIE? An NFL moneyline PUSHES on a tie; a Kalshi
                       "will X win" resolves NO. Those are different
                       quantities and the difference is systematic.
                       The eFootball series pays $0.50 on a draw, which is
                       a third behaviour again -- so this cannot be assumed.
  close_time           Must line up with the sportsbook's kickoff, or the
                       two sides are not quoting the same event.
  yes_sub_title        The join key on the Kalshi side. City, nickname, or
                       something else entirely decides how matching works.
  event_ticker         Both sides of one game share it. That is what makes
                       a two-sided check possible.
  ask/bid size         A gap with nothing behind it is not tradeable. This
                       is what killed T-3 and oil, both times after the
                       quote looked clean.

Read-only. Places no order, writes no row, spends no Odds API quota.
"""
import json
import os

import collect15 as C

TICKER = (os.environ.get("NFLMARKET_TICKER") or "KXNFLGAME").strip()
LIMIT = int(os.environ.get("NFLMARKET_LIMIT") or 200)
# How many markets to print in full. The rest are listed one line each, so
# the count is always honest even when the detail is truncated.
FULL = int(os.environ.get("NFLMARKET_FULL") or 6)


def show(m, full):
    print(f"\n  {str(m.get('ticker'))[:60]}")
    print(f"    event        {m.get('event_ticker')}")
    print(f"    title        {str(m.get('title'))[:84]}")
    print(f"    yes_sub      {m.get('yes_sub_title')!r}")
    print(f"    no_sub       {m.get('no_sub_title')!r}")
    print(f"    status       {m.get('status')}   market_type "
          f"{m.get('market_type')}   strike {m.get('strike_type')}")
    print(f"    close        {m.get('close_time')}")
    print(f"    expiration   {m.get('expiration_time')}")
    print(f"    occurrence   {m.get('occurrence_datetime')}")
    # DEPTH, not just price. The number that decides whether any of this is
    # tradeable, and the one the last two ideas got wrong.
    print(f"    yes bid      {m.get('yes_bid_dollars')} "
          f"(size {m.get('yes_bid_size_fp')})")
    print(f"    yes ask      {m.get('yes_ask_dollars')} "
          f"(size {m.get('yes_ask_size_fp')})")
    print(f"    volume       24h {m.get('volume_24h_fp')}   "
          f"total {m.get('volume_fp')}   OI {m.get('open_interest_fp')}")
    if not full:
        return
    print(f"\n    RULES PRIMARY -- read for: does Yes mean 'wins', and what")
    print(f"    happens on a TIE and on a postponement?")
    print(f"      {str(m.get('rules_primary') or '(none)')[:1200]}")
    if m.get("rules_secondary"):
        print(f"    RULES SECONDARY")
        print(f"      {str(m.get('rules_secondary'))[:900]}")


def main():
    print("=" * 100)
    print(f"ONE SERIES, IN FULL: {TICKER}")
    print("  No ranking and no filtering. Whatever the API returns is what")
    print("  prints, including nothing.")
    print("=" * 100)

    d, err = C.get("/trade-api/v2/markets", series_ticker=TICKER,
                   limit=min(LIMIT, 1000))
    if err:
        print(f"\n  THE READ FAILED: {err}")
        print("  That is a real answer and not an empty series. Nothing")
        print("  below should be taken as evidence about this ticker.")
        return 1

    mks = (d or {}).get("markets") or []
    print(f"\n  {len(mks)} market(s) returned for series_ticker={TICKER!r}")
    if not mks:
        print("  EMPTY. Either the ticker is wrong or the series has no")
        print("  markets right now. Check the ticker against the series list")
        print("  before concluding anything about NFL coverage on Kalshi.")
        return 1

    print(f"  field names on a market object:")
    print(f"    {sorted(mks[0].keys())}")

    # The whole point: show real markets, chosen by nothing.
    print(f"\n{'-' * 100}")
    print(f"THE FIRST {min(FULL, len(mks))} MARKETS IN FULL")
    print("-" * 100)
    for m in mks[:FULL]:
        show(m, full=True)

    if len(mks) > FULL:
        print(f"\n{'-' * 100}")
        print(f"THE REMAINING {len(mks) - FULL}, one line each, so the count "
              "stays honest")
        print("-" * 100)
        for m in mks[FULL:]:
            print(f"  {str(m.get('ticker'))[:44]:<44} "
                  f"{str(m.get('yes_sub_title'))[:18]:<18} "
                  f"{str(m.get('status')):<10} "
                  f"close {str(m.get('close_time'))[:16]}  "
                  f"ask {m.get('yes_ask_dollars')}")

    # EVENT STRUCTURE. Two sides of one game share an event_ticker, and that
    # is what makes a both-sides sanity check possible: the two Yes prices
    # should sum to roughly 1 plus the spread. If they do not, the series is
    # not a two-sided game winner whatever its title says.
    by_event = {}
    for m in mks:
        by_event.setdefault(m.get("event_ticker"), []).append(m)
    print(f"\n{'-' * 100}")
    print(f"EVENT STRUCTURE -- {len(by_event)} event(s). For a two-sided game")
    print("winner the two Yes asks should sum to about 1 plus the spread;")
    print("anything else means this is not the market it looks like.")
    print("-" * 100)
    for ev, ms in list(by_event.items())[:12]:
        asks = [m.get("yes_ask_dollars") for m in ms]
        subs = [str(m.get("yes_sub_title")) for m in ms]
        tot = None
        try:
            tot = sum(float(a) for a in asks if a is not None)
        except Exception:
            pass
        print(f"  {str(ev)[:40]:<40} {len(ms)} side(s)  "
              f"{', '.join(s[:14] for s in subs[:4])}"
              + (f"   yes asks sum {tot:.2f}" if tot is not None else ""))

    print(f"\n{'-' * 100}")
    print("RAW JSON OF THE FIRST MARKET, so the matcher is written against")
    print("the real shape and not a remembered one")
    print("-" * 100)
    print(json.dumps(mks[0], indent=2, default=str)[:3000])

    print("\n" + "=" * 100)
    print("  WHAT STILL HAS TO BE TRUE BEFORE ANY GAP IS LOGGED:")
    print()
    print("  1. Yes means the team WINS THE GAME -- check rules_primary.")
    print("  2. The tie is handled explicitly. A sportsbook moneyline")
    print("     PUSHES on a tie, so its de-vigged number is P(win | no tie),")
    print("     while a Kalshi 'will X win' resolves NO on a tie. Those are")
    print("     different quantities. Ties are only ~0.2-0.4% of games, but")
    print("     the edge threshold is 3 points and the de-vig methods")
    print("     already disagree by 0.6, so an uncorrected tie biases EVERY")
    print("     row the same direction. It gets subtracted, not ignored.")
    print("  3. close_time matches the sportsbook's kickoff.")
    print("  4. There is real size at the ask. A 4-point gap with 11")
    print("     contracts behind it is what killed T-3 and oil.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

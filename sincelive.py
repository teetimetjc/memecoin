"""What would the wider rule have made, had it started when the money did?

A fair counterfactual needs the same window, the same stake and the same
arithmetic, so both rules are priced over markets closing after the moment the
narrow slice went live, at $4 a bet, from the T-9 quotes the live tab recorded.

THREE THINGS THIS IS NOT, each of which would flatter the answer if ignored.

  IT IS PAPER. Every bet is costed at the quoted ask (or one minus the bid for
  NO), because that is all the tab stores. The real record's fills came in
  about 0.3c better than quote on average, but with a spread either way -- one
  filled 5c better, another 0.6c worse. So the paper figure is the honest
  baseline and the live one is not directly comparable to it; the live record
  is printed beside it rather than merged into it.

  IT ASSUMES EVERY QUALIFYING BET WAS TAKEN. The real bot only bets while a
  session is running, and there was an hour between sessions with no bot at
  all, so the paper narrow slice will show MORE bets than were actually
  placed. That difference is coverage, not edge, and it is reported.

  IT IS NOT A TEST OF ANYTHING. A window chosen because it is the window the
  money happened to run in is not a sample anyone pre-registered. One day of
  either rule is far too short to separate an edge from none -- the live
  record needs nearer a thousand settled bets -- so this answers "what would
  the account have done" and nothing larger.

Read-only.
"""

import collections
import sys

import score as S

# The moment the narrow slice went live with real money.
START = "2026-10-07T15:49:00Z"
STAKE = 4.0

RULES = (
    ("narrow slice   90-96c YES", (0.90, 0.96), ("YES",)),
    ("expensive fav  80-96c Y/N", (0.80, 0.96), ("YES", "NO")),
)


def window(rows):
    cut = S.ts(START)
    return [m for m in rows if S.ts(m["ct"]) and S.ts(m["ct"]) > cut]


def priced(M, band, sides):
    old = S.STAKE
    try:
        S.STAKE = STAKE
        return S.bets(M, band, sides)
    finally:
        S.STAKE = old


def tally(label, bs, note=""):
    n = len(bs)
    if not n:
        print(f"  {label:<28} no qualifying bets")
        return None
    won = sum(1 for b in bs if b[3])
    profit = sum(b[0] for b in bs)
    # Cash actually committed, not the nominal stake: contracts are whole.
    cost = 0.0
    for v, ct, px, w in bs:
        c = int(STAKE / px)
        cost += c * px + S.fee(c, px)
    px = sum(b[2] for b in bs) / n
    c = int(STAKE / px)
    need = (c * px + S.fee(c, px)) / c
    roi = 100.0 * profit / cost if cost else 0.0
    print(f"  {label:<28} {n:>4} bets  won {won:>4}/{n:<4} "
          f"({100.0*won/n:>5.1f}% vs {100*need:.1f}%)  "
          f"staked ${cost:>8,.2f}  net ${profit:>+8.2f}  {roi:>+6.2f}%{note}")
    return dict(n=n, won=won, profit=profit, cost=cost, roi=roi)


def main():
    M = S.load()                      # post-freeze M15, the five majors
    W = window(M)
    print("=" * 112)
    print(f"SINCE THE MONEY WENT LIVE ({START}) -- both rules at ${STAKE:.0f} a bet")
    print("=" * 112)
    print(f"  {len(W)} graded markets with a T-{S.ENTRY} quote in the window\n")

    res = {}
    for label, band, sides in RULES:
        res[label] = tally(label, priced(W, band, sides))

    a = res.get("narrow slice   90-96c YES")
    b = res.get("expensive fav  80-96c Y/N")
    if a and b:
        print()
        print(f"  {'the difference':<28} {b['n']-a['n']:>4} more bets, "
              f"${b['profit']-a['profit']:+.2f} more profit, "
              f"${b['cost']-a['cost']:,.2f} more staked")

    print("\n" + "=" * 112)
    print("WHAT ACTUALLY HAPPENED, for comparison -- NOT the same measurement")
    print("=" * 112)
    print("  The live record is 15 bets, 15 won, $55.17 staked, +$4.83 net,")
    print("  +8.75% on stake. It differs from the paper narrow slice above in")
    print("  two ways that pull opposite directions: real fills came in better")
    print("  than the quote, which helps, and the bot missed windows while no")
    print("  session was running, which costs bets. Neither difference is edge.")
    print("=" * 112)
    print("  One day is not evidence for either rule. The frozen specs decide")
    print("  that, and the replication failure already argues against the wider")
    print("  one on 13,669 bets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

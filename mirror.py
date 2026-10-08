"""If favourites lose where the rule was never fitted, does the other side win?

others.py found the expensive favourite losing -$0.096 a bet across 13,669
bets in thirteen fifteen-minute series nobody looked at when the rule was
built, at t = -2.7. That is the most statistically significant result this
project has produced, and it points DOWN. A losing trade is a claim about
mispricing, so the obvious question is whether the mirror of it wins.

THIS IS NOT MINING, AND THE DISTINCTION MATTERS. No threshold is searched.
The markets, the band and the entry are taken unchanged from a rule that was
frozen before any of these series were looked at; the only thing that changes
is which side is bought. There is exactly one hypothesis and one test of it.

WHY IT MIGHT STILL BE NOTHING. Kalshi runs ONE book, so a market with a YES
ask at 85c has a NO ask at (100 - yes bid), which is not 15c but 15c plus the
spread. Buying the other side is not free money even when the first side
loses: BOTH sides can lose, and that is the normal case in any market whose
spread exceeds its mispricing. So the comparison that decides this is not
"does NO beat zero" but "does NO beat zero after paying the spread it has to
cross to get on". That is what the numbers below are.

THREE OUTCOMES, ALL WORTH HAVING.

  THE MIRROR WINS by more than noise -> a real, mechanism-shaped finding on
  markets nothing was fitted to, and the first candidate in this project that
  did not come out of a search. It would still need freezing and forward
  testing before any money went near it.

  BOTH SIDES LOSE -> the spread explains the favourite's loss entirely, there
  is no mispricing to trade, and the replication failure stops being evidence
  of anything except that these markets cost more to trade. That is a cheap,
  clean closure.

  THE MIRROR LOSES BY MORE -> favourites are the better side even where they
  lose money, which would be odd and worth understanding before anything else.

Read-only. Prices every bet at $4 with the verified fee, same as everything
else here.
"""

import collections
import sys

import score as S

STAKE = 4.0
BAND = (0.80, 0.96)          # the expensive favourite's band, unchanged
ENTRY = S.ENTRY
MIN_BETS = 400


def load_all(tab):
    """Every graded fifteen-minute row with a T-9 quote, series kept."""
    import predictor as P
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(tab).get_all_values()
    except Exception as e:
        print(f"  no {tab} tab ({str(e)[:60]})")
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        res = str(c(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        b, a = S._f(c(r, f"bid{ENTRY}")), S._f(c(r, f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ser=str(c(r, "Series")), ct=str(c(r, "Close Time")),
                        bid=b, ask=a, yes=(res == "yes")))
    return out


def bet(px, won):
    """One $4 bet at `px`, returning its profit. Whole contracts, real fee."""
    c = int(STAKE / px)
    if c < 1:
        return None, 0.0
    cost = c * px + S.fee(c, px)
    return ((c if won else 0) - cost), cost


def both_sides(rows):
    """For markets where the FAVOURITE qualifies, price both sides.

    Selection is on the favourite's price, exactly as the frozen rule selects,
    and then each side is priced at what it would actually cost. The NO side
    is 1 - bid, not 1 - ask: crossing to buy NO pays the spread, which is the
    whole reason the mirror is not automatically the inverse.
    """
    fav, mir = [], []
    for m in rows:
        # YES is the favourite when its ask falls in the band.
        if BAND[0] <= m["ask"] < BAND[1]:
            v, c = bet(m["ask"], m["yes"])
            if v is not None:
                fav.append((v, m["ct"], m["ask"], m["yes"], c))
            nopx = 1.0 - m["bid"]
            v, c = bet(nopx, not m["yes"])
            if v is not None:
                mir.append((v, m["ct"], nopx, not m["yes"], c))
        # NO is the favourite when ITS price falls in the band.
        nofav = 1.0 - m["bid"]
        if BAND[0] <= nofav < BAND[1]:
            v, c = bet(nofav, not m["yes"])
            if v is not None:
                fav.append((v, m["ct"], nofav, not m["yes"], c))
            v, c = bet(m["ask"], m["yes"])
            if v is not None:
                mir.append((v, m["ct"], m["ask"], m["yes"], c))
    return fav, mir


def tally(label, bs):
    n = len(bs)
    if not n:
        print(f"  {label:<34} no bets")
        return None
    profit = sum(b[0] for b in bs)
    cost = sum(b[4] for b in bs)
    won = sum(1 for b in bs if b[3])
    g = collections.defaultdict(list)
    for b in bs:
        g[b[1]].append(b[0])
    se = S.cse(g, n)
    t = (profit / n / se) if se else 0.0
    roi = 100.0 * profit / cost if cost else 0.0
    print(f"  {label:<34} {n:>6} bets  won {100.0*won/n:>5.1f}%  "
          f"${profit/n:>+7.3f}/bet  ${profit:>+9.2f}  "
          f"staked ${cost:>10,.0f}  {roi:>+6.2f}%  t={t:>+5.1f}")
    return dict(n=n, per=profit / n, roi=roi, t=t, profit=profit)


def main():
    rows = load_all("M15H")
    if not rows:
        print("no history")
        return 1
    unfit = [r for r in rows if r["ser"] not in S.MAJORS]
    fit = [r for r in rows if r["ser"] in S.MAJORS]

    print("\n" + "=" * 118)
    print("THE MIRROR TRADE -- if the favourite loses here, does the other "
          "side win?")
    print("=" * 118)
    print(f"  band {100*BAND[0]:.0f}-{100*BAND[1]:.0f}c at T-{ENTRY}, "
          f"${STAKE:.0f} a bet, unchanged from the frozen rule.")
    print("  Selection is on the favourite's price; the mirror is the same")
    print("  market bought from the other side, paying the spread to get on.\n")

    for label, data in (("NEVER FITTED -- the 13 other series", unfit),
                        ("THE FIVE THE RULE WAS BUILT ON", fit)):
        if not data:
            continue
        print("-" * 118)
        print(f"  {label}   ({len(data):,} graded markets)")
        print("-" * 118)
        f, m = both_sides(data)
        a = tally("buy the favourite", f)
        b = tally("buy the OTHER side (the mirror)", m)
        if a and b:
            print(f"  {'both sides together':<34} "
                  f"${a['per'] + b['per']:>+7.3f}/bet  <- the round-trip cost "
                  "of this market")
            print()
            if b["per"] > 0 and b["t"] > 2 and a["per"] < 0:
                print("  THE MIRROR WINS. The favourite's loss is a real")
                print("  mispricing rather than spread, and the other side")
                print("  clears the cost of getting on. Freeze it before")
                print("  betting it: this is one hypothesis, not a result.")
            elif a["per"] < 0 and b["per"] < 0:
                print("  BOTH SIDES LOSE, which is the ordinary case: the")
                print("  spread exceeds whatever mispricing exists, so the")
                print("  favourite's loss is the cost of trading rather than")
                print("  evidence about price. Nothing to trade here, and the")
                print("  replication failure means less than it appeared to.")
            else:
                print("  Mixed. Read the two rows rather than a verdict.")
        print()

    print("=" * 118)
    print("  Whatever this says, it changes nothing about the two frozen")
    print("  specs, which score on their own pre-registered terms.")
    print("=" * 118)
    return 0


if __name__ == "__main__":
    sys.exit(main())

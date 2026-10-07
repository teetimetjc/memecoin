"""Does the frozen rule work on the OTHER fifteen-minute markets?

This is the most informative test available to this project right now, and it
is informative for a reason that has nothing to do with sample size.

Everything that has failed here failed by mining: sixteen leads were found by
searching the same five crypto series for a configuration that looked
profitable, and a search over enough cuts finds one in noise. Applying an
ALREADY-FROZEN rule to markets it was never fitted to is the opposite
procedure. The rule cannot have been tuned to gold or oil, because nobody
looked at gold or oil when choosing 90-96c and T-9. So whatever it does there
is a prediction being checked, not a pattern being found.

AND THE MECHANISM MAKES A PREDICTION. The one reason to believe the expensive
favourite at all is favourite-longshot bias -- bettors systematically overpay
for longshots, so the heavy favourite on the other side of that trade is
underpriced. That is a claim about how people bet, not about Bitcoin. If it is
true it should appear in any thin binary market with a heavy favourite,
including WTI and natural gas. If the edge lives only in five crypto series
and vanishes everywhere else, the mechanism story is wrong and what remains is
a pattern in five correlated instruments, which is a much weaker thing to bet
on than 11,546 bets suggests.

So both outcomes are worth having:

  IT REPLICATES -> the mechanism is real, the rule has somewhere to grow, and
  the frozen spec's eventual verdict means more than it would alone.

  IT DOES NOT -> the edge is specific to crypto fifteen-minute markets, which
  is a reason to trust it LESS on crypto too, not merely a reason to skip
  gold. Five coins moving together is closer to one bet than to five.

THE SERIES ARE NOT SCORED AGAINST A CHOSEN THRESHOLD AND THEN RANKED. Every
series with enough bets is printed, in a fixed order, with no selection. Ranking
by profit and reporting the top of the list is how a mining result gets
presented as a discovery, and this file exists to avoid exactly that.

Read-only.
"""

import collections
import sys

import predictor as P
import score as S

HIST = "M15H"
LIVE = "M15"
ENTRY = S.ENTRY
MIN_BETS = 150          # below this a series says nothing either way


def load_all(tab):
    """Every graded fifteen-minute row with a T-9 quote, series kept.

    S.load_hist filters to the five majors by design, since that is what the
    specs cover. This deliberately does not, because the whole question is what
    happens outside them.
    """
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(tab).get_all_values()
    except Exception as e:
        print(f"  no {tab} tab ({str(e)[:50]})")
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


def score_series(rows, band, sides):
    """The frozen rule's record on one series: n, $/bet, win vs break-even."""
    bs = S.bets(rows, band, sides)
    n = len(bs)
    if not n:
        return None
    profit = sum(b[0] for b in bs)
    win = sum(1 for b in bs if b[3]) / n
    px = sum(b[2] for b in bs) / n
    c = int(S.STAKE / px)
    need = (c * px + S.fee(c, px)) / c
    g = collections.defaultdict(list)
    for b in bs:
        g[b[1]].append(b[0])
    se = S.cse(g, n)
    t = (profit / n / se) if se else 0.0
    return dict(n=n, per=profit / n, total=profit, win=win, need=need, t=t)


def report(label, rows, band, sides):
    byser = collections.defaultdict(list)
    for r in rows:
        byser[r["ser"]].append(r)
    majors = [s for s in sorted(byser) if s in S.MAJORS]
    others = [s for s in sorted(byser) if s not in S.MAJORS]

    print("\n" + "=" * 100)
    print(f"{label}   rule: {100*band[0]:.0f}-{100*band[1]:.0f}c "
          f"{'/'.join(sides)} at T-{ENTRY}")
    print("=" * 100)
    print(f"  {len(byser)} series present, {len(majors)} of them the five the")
    print(f"  rule was built on, {len(others)} it was never fitted to.\n")
    print(f"  {'series':<16} {'rows':>7} {'bets':>6} {'$/bet':>9} "
          f"{'total':>10} {'won':>7} {'needs':>7} {'t':>6}")
    print("  " + "-" * 82)

    def line(s, tag):
        res = score_series(byser[s], band, sides)
        if res is None:
            print(f"  {s:<16} {len(byser[s]):>7} {'0':>6}   no qualifying bets")
            return None
        flag = "" if res["n"] >= MIN_BETS else "  (too few to read)"
        print(f"  {s:<16} {len(byser[s]):>7} {res['n']:>6} "
              f"${res['per']:>+8.3f} ${res['total']:>+9.2f} "
              f"{100*res['win']:>6.1f}% {100*res['need']:>6.1f}% "
              f"{res['t']:>+6.1f}{flag}")
        return res

    print("  -- the five the rule was built on " + "-" * 48)
    for s in majors:
        line(s, "major")
    if others:
        print("  -- never fitted: a prediction, not a search " + "-" * 38)
        pooled = []
        for s in others:
            r = line(s, "other")
            if r and r["n"] >= MIN_BETS:
                pooled.append(s)
        # Pooled across the unfitted series, because each alone is thin and
        # the question is about the mechanism rather than about gold.
        if pooled:
            allrows = [r for s in pooled for r in byser[s]]
            res = score_series(allrows, band, sides)
            print("  " + "-" * 82)
            print(f"  {'POOLED (unfitted)':<16} {len(allrows):>7} {res['n']:>6} "
                  f"${res['per']:>+8.3f} ${res['total']:>+9.2f} "
                  f"{100*res['win']:>6.1f}% {100*res['need']:>6.1f}% "
                  f"{res['t']:>+6.1f}")
            print()
            if res["n"] < MIN_BETS:
                print("  Not enough unfitted bets yet to say anything. That is a")
                print("  data-collection answer, not a result.")
            elif res["per"] > 0 and res["t"] > 2:
                print("  REPLICATES. The rule was never fitted to these markets")
                print("  and makes money there anyway, which is the strongest")
                print("  evidence in this project for the mechanism being real.")
            elif res["per"] > 0:
                print("  Positive but not significant. Suggestive, not evidence;")
                print("  more of these series is the cheapest way to resolve it.")
            else:
                print("  DOES NOT REPLICATE. The edge does not appear where the")
                print("  rule was not fitted, which argues the mechanism story")
                print("  is wrong -- and that is a reason to trust the crypto")
                print("  result LESS, not merely to skip these markets.")
    else:
        print("  No non-major fifteen-minute series has graded rows with a")
        print(f"  T-{ENTRY} quote yet, so the question cannot be answered from")
        print("  what is collected. backfill.py already lists WTI, GOLD and")
        print("  NATGAS as liquid; collecting them is the next step.")


def main():
    for tab in (HIST, LIVE):
        rows = load_all(tab)
        print(f"\n{tab}: {len(rows):,} graded rows with a T-{ENTRY} quote")
        if not rows:
            continue
        for name, band, sides in (
                ("NARROW SLICE (live now)", (0.90, 0.96), ("YES",)),
                ("EXPENSIVE FAVOURITE", (0.80, 0.96), ("YES", "NO"))):
            report(f"{tab} -- {name}", rows, band, sides)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Is the expensive-favourite edge real, or an artefact of backfilled quotes?

The rule was found in M15H, whose quotes are RECONSTRUCTED from candlesticks.
It is scored forward on M15, whose quotes are READ LIVE off the book. Those
two were validated against each other on 2026-09-26 and did not agree
closely: mean difference -0.08c, but a median absolute difference of 3.0c and
37.6% of quotes differing by more than 5c.

A 3c disagreement in the entry price is fatal to a rule that is defined by a
price band and lives on a 1.7 point edge. And the numbers now disagree in
exactly the way that would predict:

    M15H, 11,546 bets, Jul 19 - Sep 26     won 89.7% vs 88.0% needed   +1.89%
    M15,     722 bets, Sep 26 - Oct 5      won 85.2% vs 87.5% needed   -2.59%

Two explanations fit. Either the market changed in late September, or the
edge was never in the market and is an artefact of how backfilled quotes are
built. THIS FILE SEPARATES THEM, by scoring the same rule on the same
tickers over the SAME DATES from both tabs.

  If both tabs agree over the overlap, the tabs are consistent and the gap
  window is telling us the market changed or the edge was noise.

  If M15H is positive and M15 is negative on the SAME markets, the edge is a
  measurement artefact and the frozen spec is dead on arrival -- no amount of
  forward data would rescue it, because forward data is M15.

It also prints the quote difference itself, per ticker, at T-9: how often the
two tabs disagree about the price, by how much, and whether the disagreement
has a direction. A bias toward a LOWER ask in M15H would create exactly this
illusion, because a cheaper entry on an unchanged outcome is free profit.

Read-only.
"""

import collections
import statistics as st
import sys

import predictor as P
from score import ENTRY, MAJORS, STAKE, _f, bets, fee, ts

BAND = (0.80, 0.96)
SIDES = ("YES", "NO")


def load(tab):
    """{ticker: row} for majors with a two-sided T-9 quote and a result."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(tab).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c("Close Time"))
        if t is None:
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out[str(r[0])] = dict(ct=str(c("Close Time")), t=t, bid=b, ask=a,
                              yes=(res == "yes"))
    print(f"  {tab}: {len(out)} usable majors")
    return out


def tally(bs, label):
    n = len(bs)
    if not n:
        print(f"    {label:<34} no bets")
        return
    risked = STAKE * n
    profit = sum(b[0] for b in bs)
    win = sum(1 for b in bs if b[3]) / n
    px = st.mean([b[2] for b in bs])
    c = int(STAKE / px)
    need = (c * px + fee(c, px)) / c
    print(f"    {label:<34} {n:>5} bets  won {100*win:>5.1f}% vs "
          f"{100*need:>5.1f}%  ${profit:>+9.2f}  {100*profit/risked:>+6.2f}%")


def main():
    H = load("M15H")
    L = load("M15")
    both = sorted(set(H) & set(L))
    print(f"\n  tickers present in BOTH tabs: {len(both)}")
    if not both:
        print("  No overlap, so the two cannot be compared on equal terms.")
        print("  That alone is a finding: the forward test would then be")
        print("  measuring a different instrument from the one searched.")
        return 0

    lo = min(H[t]["t"] for t in both)
    hi = max(H[t]["t"] for t in both)
    import time as _t
    print(f"  overlap spans {_t.strftime('%Y-%m-%d', _t.gmtime(lo))} to "
          f"{_t.strftime('%Y-%m-%d', _t.gmtime(hi))}")

    print("\n" + "=" * 104)
    print("THE SAME MARKETS, SCORED FROM EACH TAB")
    print("=" * 104)
    tally(bets([H[t] for t in both], BAND, SIDES), "from M15H (backfilled)")
    tally(bets([L[t] for t in both], BAND, SIDES), "from M15 (live)")

    print("\n" + "=" * 104)
    print("THE QUOTE DIFFERENCE AT T-9, on those same markets")
    print("=" * 104)
    dask = [H[t]["ask"] - L[t]["ask"] for t in both]
    dbid = [H[t]["bid"] - L[t]["bid"] for t in both]
    same = sum(1 for t in both
               if abs(H[t]["ask"] - L[t]["ask"]) < 0.005
               and abs(H[t]["bid"] - L[t]["bid"]) < 0.005)
    print(f"  identical quotes            {same}/{len(both)} "
          f"({100.0*same/len(both):.0f}%)")
    print(f"  ask: mean diff {100*st.mean(dask):+.2f}c   "
          f"median {100*st.median(dask):+.2f}c   "
          f"median abs {100*st.median([abs(x) for x in dask]):.2f}c")
    print(f"  bid: mean diff {100*st.mean(dbid):+.2f}c   "
          f"median {100*st.median(dbid):+.2f}c   "
          f"median abs {100*st.median([abs(x) for x in dbid]):.2f}c")
    cheaper = sum(1 for x in dask if x < -0.005)
    pricier = sum(1 for x in dask if x > 0.005)
    print(f"  M15H ask CHEAPER than live  {cheaper}/{len(both)} "
          f"({100.0*cheaper/len(both):.0f}%)")
    print(f"  M15H ask PRICIER than live  {pricier}/{len(both)} "
          f"({100.0*pricier/len(both):.0f}%)")
    print("  A systematic bias toward a cheaper backfilled ask would create")
    print("  this edge out of nothing: a lower entry on an unchanged outcome")
    print("  is free profit that no trader could have taken.")

    # How many markets each tab admits into the band at all -- a band rule is
    # only as good as agreement about which markets are IN it.
    inH = sum(1 for t in both if BAND[0] <= H[t]["ask"] < BAND[1])
    inL = sum(1 for t in both if BAND[0] <= L[t]["ask"] < BAND[1])
    agree = sum(1 for t in both
                if (BAND[0] <= H[t]["ask"] < BAND[1])
                == (BAND[0] <= L[t]["ask"] < BAND[1]))
    print(f"\n  YES side inside {100*BAND[0]:.0f}-{100*BAND[1]:.0f}c: "
          f"M15H says {inH}, M15 says {inL}, "
          f"they agree on {agree}/{len(both)} markets "
          f"({100.0*agree/len(both):.0f}%)")
    print("\n" + "=" * 104)
    print("If the two tabs disagree about the SCORE on identical markets, the")
    print("edge is in the data pipeline and not in the market, and no quantity")
    print("of forward data can rescue the spec -- forward data is M15.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())

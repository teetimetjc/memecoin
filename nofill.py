"""The orders that were accepted and filled nothing: saved money, or lost it?

65 of 229 order attempts came back "accepted but filled nothing (book moved
away)". An IOC order takes whatever the book holds at or under its limit and
cancels the rest; filling zero means there was nothing at that price by the
time it arrived.

THE QUESTION IS NOT WHETHER THEY WERE CHEAP. It is whether those markets
settled YES. A no-fill on a market that went on to settle YES is forgone
profit; a no-fill on one that settled NO is a loss avoided.

THE COUNTERFACTUAL IS NOT FREE, AND IS LABELLED RATHER THAN HIDDEN. These
orders could NOT be filled at their limit -- that is the definition of what
happened. Assuming a fill at the limit therefore describes a trade that was
not available. It is still the right bound to compute, because it is the
BEST case for having bet: if even the optimistic version loses money, not
filling was good. Read it as a ceiling on what was missed, never as missed
profit.

Scored on Kalshi's own settlement, never a spot feed. Read-only.
"""
import collections
import statistics as st
import tally as T
import live


def market_result(ticker):
    d = T.get(f"/markets/{ticker}")
    m = (d or {}).get("market") or {}
    return str(m.get("result") or "").lower()


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    nf, placed = [], []
    for r in rows[1:]:
        if not r or not r[0] or r[0] < T.START:
            continue
        st_ = str(c(r, "Status")).strip()
        det = str(c(r, "Detail")).strip()
        rec = dict(ts=r[0], sym=str(c(r, "Symbol")), side=str(c(r, "Side")),
                   tk=str(c(r, "Ticker")), entry=T._f(c(r, "Entry ¢")),
                   limit=T._f(c(r, "Limit ¢")))
        if st_ == "SKIPPED" and "filled nothing" in det:
            nf.append(rec)
        elif st_ == "PLACED":
            placed.append(rec)

    print("=" * 90)
    print("ORDERS ACCEPTED THAT FILLED NOTHING")
    print("=" * 90)
    print(f"  {len(nf)} no-fill vs {len(placed)} filled\n")

    print("  WHAT THEY LOOK LIKE vs filled orders:")
    for lab, g in (("filled", placed), ("no fill", nf)):
        e = [x["entry"] for x in g if x["entry"]]
        print(f"    {lab:<8} n={len(g):<4} mean entry {st.mean(e):.2f}c  "
              f"median {st.median(e):.1f}c  "
              f"90-92c: {100*sum(1 for v in e if 90<=v<92)/len(e):.0f}%  "
              f"94c+: {100*sum(1 for v in e if v>=94)/len(e):.0f}%")
    print()
    for lab, g in (("filled", placed), ("no fill", nf)):
        cc = collections.Counter(x["sym"] for x in g)
        tot = sum(cc.values())
        print(f"    {lab:<8} by coin: " +
              "  ".join(f"{k} {100*v/tot:.0f}%" for k, v in cc.most_common()))
    print()
    for lab, g in (("filled", placed), ("no fill", nf)):
        cc = collections.Counter(x["side"] for x in g)
        print(f"    {lab:<8} by side: {dict(cc)}")

    print("\n  HOW THEY SETTLED (Kalshi settlement, not spot):")
    res = collections.Counter()
    rows_out = []
    for x in nf:
        r = market_result(x["tk"])
        res[r or "unknown"] += 1
        rows_out.append((x, r))
    print(f"    {dict(res)}")

    good = [(x, r) for x, r in rows_out if r in ("yes", "no")]
    if not good:
        print("    no settled markets readable -- cannot score")
        return 0

    up = [(x, r) for x, r in good if x["side"] == "UP"]
    won = sum(1 for x, r in up if r == "yes")
    n = len(up)
    print(f"\n  UP/YES no-fills: {n} settled, {won} would have WON "
          f"({100*won/n:.1f}%)")
    lim = st.mean([x["limit"] for x, _ in up if x["limit"]])
    print(f"    mean limit {lim:.2f}c -> break-even about {lim:.1f}% "
          "before fees")
    # Per $4 bet at the limit, fee 0.07*n*p*(1-p) on entry.
    tot = 0.0
    for x, r in up:
        L = (x["limit"] or 0) / 100.0
        if L <= 0:
            continue
        k = int(4.0 / L)
        fee = 0.07 * k * L * (1 - L)
        tot += (k * 1.0 - k * L - fee) if r == "yes" else (-k * L - fee)
    print(f"\n  BEST-CASE counterfactual, every one filled at its limit at $4:")
    print(f"    net ${tot:+.2f} over {n} bets  (${tot/n:+.3f} per bet)")
    print()
    print("  A fill at the limit was NOT available -- that is why these are")
    print("  no-fills. This is a ceiling on what was missed, not a measurement")
    print("  of it. If it is negative, not filling was unambiguously good.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What each bet actually PAID against what it was quoted. Read-only.

TWO QUESTIONS, ONE MEASUREMENT.

  WAS $6 CUT TOO EARLY? The $6 era ran 67 bets at a -4.32 gap and -$17.35
  while $4 and $5 ran +4.37 and +4.10. Those eras are also calendar periods,
  so "the stake was too big" and "that fortnight was bad" both fit the
  numbers. There is one mechanism that would genuinely make a larger stake
  worse rather than merely unluckier: a bigger order eating deeper into the
  book and filling at a worse average price. That is checkable here, and if
  slippage is flat across the eras, size did NOT cause the $6 loss and the
  cut was a reaction to variance.

  WHERE IS THE UNUSED PROFIT? The measured edge is +1.85 points on a ~92.3c
  break-even, about +1.4% ROI. At that price a ONE CENT better average fill
  is worth roughly 1.1 points of ROI -- comparable to the entire edge. So
  execution is not a detail next to sizing and venue; it is the same order of
  magnitude as the thing being measured, and unlike the edge it is under our
  control.

HOW SLIPPAGE IS DEFINED HERE. For each order: the volume-weighted price
actually paid, minus the Entry quote the scan saw. Positive means paid MORE
than quoted. Both come from different sources on purpose -- the quote from
the sheet, the fill from Kalshi's own fills endpoint -- so this is not a
column compared against itself.

THE COST COLUMN IS NOT USED. live.py writes contracts times the LIMIT, which
is the quote plus a slip buffer, so it overstates spend and would show
fictitious slippage of about one cent on every bet. tally.actual_costs reads
the fills, pages through them with the cursor, and REFUSES any fill set whose
contract count disagrees with the sheet -- a partial fill sum is wrong and
believable, which is worse than missing.

WHAT WOULD SHOW A REAL SIZE PROBLEM: slippage rising with contract count, or
with stake era, beyond the odd cent. What would show the opposite: flat
slippage, which means the $6 era lost on outcomes rather than on execution.

AND WHAT THIS CANNOT SHOW. The orders are IOC at a limit, so an order that
could not fill at the limit simply did not fill -- those appear as skips, not
as slippage, and the cost of a missed fill is an opportunity that this file
cannot price. Average slippage across FILLED orders is therefore the
optimistic half of execution quality.

Read-only.
"""
import collections
import statistics as st

import tally as T

RESTART = "2026-10-14 00:00"


def main():
    bets = T.rows_since(T.START)
    fills = T.actual_costs()

    rows = []
    no_fill = 0
    for b in bets:
        f = fills.get(b["oid"])
        if not (f and f.get("qty") and sum(f["qty"]) and b["entry"]):
            no_fill += 1
            continue
        q = sum(f["qty"])
        vwap_c = 100.0 * sum(p * n for p, n in zip(f["px"], f["qty"])) / q
        rows.append(dict(ts=b["ts"], stake=str(T.stake_of(b) or "?"),
                         entry=b["entry"], vwap=vwap_c,
                         slip=vwap_c - b["entry"],
                         contracts=int(b["contracts"] or 0),
                         levels=len(f["qty"]),
                         side=str(b.get("side") or "").upper()))

    print("=" * 104)
    print("WHAT WAS PAID vs WHAT WAS QUOTED")
    print("  slippage = volume-weighted fill price minus the Entry quote, "
          "in cents. Positive = paid more.")
    print("=" * 104)
    print(f"  {len(rows)} orders with a verified fill"
          + (f", {no_fill} without one (skipped)" if no_fill else ""))
    if len(rows) < 30:
        print("  too few to say anything")
        return 0
    allslip = [r["slip"] for r in rows]
    print(f"  overall: mean {st.mean(allslip):+.3f}c, median "
          f"{st.median(allslip):+.3f}c")
    print()

    def block(title, key, order=None):
        print("-" * 104)
        print(title)
        print("-" * 104)
        g = collections.defaultdict(list)
        for r in rows:
            g[key(r)].append(r)
        keys = order or sorted(g)
        print(f"  {'group':<16} {'n':>5} {'mean slip':>11} {'median':>9} "
              f"{'worst':>8} {'>=2c':>7} {'levels':>8}")
        for k in keys:
            if k not in g:
                continue
            gg = g[k]
            s = [x["slip"] for x in gg]
            bad = 100.0 * sum(1 for x in s if x >= 2.0) / len(s)
            lv = st.mean([x["levels"] for x in gg])
            print(f"  {str(k):<16} {len(gg):>5} {st.mean(s):>+10.3f}c "
                  f"{st.median(s):>+8.3f}c {max(s):>+7.2f}c {bad:>6.1f}% "
                  f"{lv:>8.2f}")
        print()
        return g

    block("BY STAKE ERA -- the $6 question. These are calendar periods too.",
          lambda r: "$" + r["stake"], ["$2", "$4", "$5", "$6", "$7", "$3"])

    block("BY ORDER SIZE -- the mechanism that would make size itself bad",
          lambda r: (f"{r['contracts']} contract"
                     + ("s" if r["contracts"] != 1 else "")))

    block("BY QUOTED PRICE -- is the top of the book thinner?",
          lambda r: (f"{int(r['entry'] // 2 * 2)}-"
                     f"{int(r['entry'] // 2 * 2) + 2}c"))

    print("=" * 104)
    print("  WHAT IT MEANS FOR THE TWO QUESTIONS")
    print()
    # The $6 question, answered from the numbers rather than asserted.
    by_era = collections.defaultdict(list)
    for r in rows:
        by_era[r["stake"]].append(r["slip"])
    e5 = by_era.get("5") or []
    e6 = by_era.get("6") or []
    if len(e5) >= 20 and len(e6) >= 20:
        d = st.mean(e6) - st.mean(e5)
        print(f"  $6 filled {d:+.3f}c vs $5 on average "
              f"({len(e6)} and {len(e5)} orders).")
        if abs(d) < 0.5:
            print("  THAT IS NOT A SIZE PROBLEM. The $6 era paid essentially")
            print("  the same prices as the $5 era, so its -4.32 gap came")
            print("  from OUTCOMES, not execution -- which means it was a bad")
            print("  fortnight, and cutting the stake was a reaction to")
            print("  variance rather than to a mechanism. It also means")
            print("  going back to $6 would not reintroduce a fill problem;")
            print("  it would just scale an edge that is still unproven.")
        else:
            print("  That is a real execution difference and it belongs in")
            print("  any decision about going back up.")
    else:
        print("  Not enough orders in both the $5 and $6 eras to compare.")
    print()

    # The profit-lever question, priced.
    mean_slip = st.mean(allslip)
    mean_entry = st.mean([r["entry"] for r in rows])
    print(f"  Average fill is {mean_slip:+.3f}c against an average quote of "
          f"{mean_entry:.1f}c.")
    if mean_entry:
        pts = 100.0 * abs(mean_slip) / mean_entry
        print(f"  Each cent of slippage is worth about "
              f"{100.0 / mean_entry:.2f} points of ROI at this price, so the "
              f"current average")
        print(f"  {'costs' if mean_slip > 0 else 'GAINS'} roughly {pts:.2f} "
              f"points -- against a measured edge of about 1.4 points.")
        print()
        if mean_slip <= 0.1:
            print("  SO THERE IS LITTLE LEFT HERE. Fills are already at or")
            print("  better than the quote, which means execution is not the")
            print("  hidden lever and the IOC-at-a-limit design is doing its")
            print("  job. The remaining honest ways to earn more are more")
            print("  WINDOWS at the same rule, not better prices on the")
            print("  windows already taken.")
        else:
            print("  SO THIS IS THE LEVER. Recovering most of that slippage")
            print("  is worth as much as the entire measured edge, and unlike")
            print("  the edge it does not need 600 more bets to confirm --")
            print("  a fill price is observable on every single order.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

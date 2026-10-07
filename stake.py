"""The same rule at $4, $10 and $20 a bet: profit, and the chance of ruin.

Profit does NOT simply scale with stake, and the reason is contract rounding.
Contracts are whole numbers -- `int(stake/price)` -- so at 88c a $4 bet buys
4 contracts for $3.55 and the remaining 45c sits idle, while a $20 bet buys 22
for $19.52 and wastes 48c of a much larger base. The small stake therefore
deploys a smaller FRACTION of itself and earns a lower return per dollar
committed, which is invisible if one just multiplies the $10 figure by 0.4.

The rounding drag is measured here rather than argued about, because it runs
the opposite way to the ruin risk and the two have to be read together:

  BIGGER STAKES earn more per bet and waste proportionally less to rounding,
  but lose a given bankroll faster when the bad patch lands.

  SMALLER STAKES survive almost anything and earn less, and give up a little
  extra to rounding on top.

WHAT IS HELD FIXED. The bets themselves -- same markets, same 80-96c band,
same T-9 entry, same outcomes. Only the dollar size changes. Nothing here
re-searches anything, so none of this can manufacture an edge that the frozen
specs have not yet confirmed.

THE EDGE IS ASSUMED, NOT PROVEN. Every profit figure below is the historical
win rate applied at a different size. That win rate came from the data the
rule was selected in, so these are the numbers IF the edge is real -- which
is the open question the frozen specs settle around Oct 16, not something
this file addresses. A ruin percentage computed on a rule with no real edge
understates the risk badly: with zero edge, ruin is certain given enough
time, at every stake in the table.

Read-only.
"""

import collections
import random
import sys

import score as S
import capital as K

STAKES = (4.0, 10.0, 20.0)
BANKROLLS = (100.0, 250.0, 500.0, 1000.0)
WINDOWS_PER_DAY = 96


class at_stake:
    """Hold score.STAKE at `stake` for everything computed inside the block.

    THE FIRST VERSION OF THIS FILE RESTORED THE GLOBAL TOO EARLY and got a
    wrong answer quietly, which is this project's signature failure. S.bets
    was called with the stake set, so profit per bet came out right -- but
    capital.cost_of and capital.ruin ALSO read S.STAKE, and they ran after
    the restore. Every cash, return and ruin figure was therefore computed
    at $10 whatever stake was being priced: the "cash used" column printed
    $9.54 on all three rows and "idle" came out at -138%, which is what
    exposed it. Nothing raised an exception.

    So the swap now spans the whole per-stake computation rather than the
    one call that obviously needs it. Passing a stake argument down through
    score.py would be cleaner still, but that file is what the frozen specs
    are scored by and it does not get edited for a convenience.
    """

    def __init__(self, stake):
        self.stake = stake

    def __enter__(self):
        self.old = S.STAKE
        S.STAKE = self.stake
        return self

    def __exit__(self, *exc):
        S.STAKE = self.old
        return False


def deployed(bs):
    """Cash actually committed, which is below `stake * n` after rounding.

    Must be called inside an `at_stake` block: cost_of re-derives the
    contract count from score.STAKE.
    """
    return sum(K.cost_of(px) for _, _, px, _ in bs)


def table(rule, H):
    print("\n" + "=" * 100)
    print(f"{rule['name'].upper()}   "
          f"({100*rule['band'][0]:.0f}-{100*rule['band'][1]:.0f}c, "
          f"{'/'.join(rule['sides'])}, T-{S.ENTRY})")
    print("=" * 100)

    rows = []
    for stake in STAKES:
        # One block per stake, covering the bets AND every capital figure
        # derived from them, including the ruin sweep further down.
        with at_stake(stake):
            bs = S.bets(H, rule["band"], rule["sides"])
            if not bs:
                continue
            n = len(bs)
            dep = deployed(bs)
            profit = sum(b[0] for b in bs)
            avg_px = sum(b[2] for b in bs) / n
            ruins = {b: K.ruin(bs, stakes=(b,), trials=2000)[0]["bust_pct"]
                     for b in BANKROLLS}
        rows.append(dict(stake=stake, n=n, dep=dep, profit=profit,
                         nominal=stake * n, bs=bs, avg_px=avg_px,
                         ruins=ruins))

    print("\n  PER BET -- same markets, same outcomes, different size")
    print(f"    {'stake':>7} {'contracts':>10} {'cash used':>11} "
          f"{'idle':>7} {'$/bet':>9} {'% of cash':>10}")
    for r in rows:
        avg = r["dep"] / r["n"]
        c = int(r["stake"] / r["avg_px"])
        idle = 100.0 * (1.0 - avg / r["stake"])
        print(f"    ${r['stake']:>6,.0f} {c:>10} ${avg:>10,.2f} "
              f"{idle:>6.1f}% ${r['profit']/r['n']:>+8.3f} "
              f"{100*r['profit']/r['dep']:>+9.2f}%")

    print("\n  OVER THE WHOLE HISTORY "
          f"({rows[0]['n']:,} bets, about six weeks)")
    print(f"    {'stake':>7} {'cash used':>13} {'profit':>12} "
          f"{'return':>9} {'per day':>10} {'per month':>11}")
    days = len({b[1][:10] for b in rows[0]["bs"]})
    for r in rows:
        per_day = r["profit"] / days
        print(f"    ${r['stake']:>6,.0f} ${r['dep']:>12,.0f} "
              f"${r['profit']:>+11,.0f} "
              f"{100*r['profit']/r['dep']:>+8.2f}% "
              f"${per_day:>+9,.2f} ${30*per_day:>+10,.2f}")
    print(f"    (profit spread over the {days} days the history covers)")

    print("\n  CHANCE OF GOING BROKE, by starting balance")
    print("    Whole windows resampled 2,000 times: same bets and win rate,")
    print("    only the ORDER they arrive in changes.")
    hdr = "".join(f"{'$%,.0f' % b:>10}" for b in BANKROLLS)
    print(f"    {'stake':>7} {hdr}")
    for r in rows:
        cells = "".join(f"{r['ruins'][b]:>9.0f}%" for b in BANKROLLS)
        print(f"    ${r['stake']:>6,.0f} {cells}")

    print("\n  WHAT $100 BUYS YOU AT EACH SIZE")
    for r in rows:
        per_day = r["profit"] / days
        print(f"    ${r['stake']:>5,.0f}/bet   "
              f"${per_day:>+7,.2f}/day   "
              f"{r['ruins'][100.0]:>3.0f}% chance of losing the $100")


def main():
    H = S.load_hist()
    if not H:
        print("no history to price")
        return 0
    print("\n" + "=" * 100)
    print("THE SAME RULE AT THREE DIFFERENT BET SIZES")
    print("=" * 100)
    print("  Profit scales almost with stake; survival does not scale at all.")
    print("  Contracts are whole numbers, so a small bet leaves part of its")
    print("  stake idle and earns slightly less per dollar committed -- that")
    print("  drag is measured in the first table, not assumed away.")
    print()
    print("  EVERY PROFIT FIGURE ASSUMES THE EDGE IS REAL. It is the")
    print("  historical win rate re-sized, and that history is the data the")
    print("  rule was found in. Whether the edge survives is what the frozen")
    print("  specs decide around Oct 16; nothing here speaks to it.")
    for r in S.RULES:
        table(r, H)
    print("\n" + "=" * 100)
    print("With no real edge, every ruin figure above is an understatement:")
    print("ruin becomes certain at every stake, given enough time.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

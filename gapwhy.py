"""Why did the expensive favourite stop working after 26 September?

Three explanations fit the break, and they call for different actions:

  IT WAS ALWAYS NOISE. t=+4.2 on mined data is weaker than it looks, and the
  recent window is the first honest look.

  THE EDGE DECAYED. A known bias in a market attracting more automated
  attention is exactly the sort of thing that gets arbitraged away.

  THE SAMPLE IS BIASED, NOT THE MARKET. This one is specific and testable,
  and it is the reason this file exists. The gap window (26 Sep - 5 Oct)
  coincides exactly with the collector's schedule failure: coverage ran 50%,
  then 17%, then 30% on 28-30 September before the cron was fixed. Those
  losses were not spread evenly -- they were whole blocks of hours, six and
  seven hours dark at a time. A rule whose edge varies by hour, sampled
  through a comb that removes particular hours, will show a different number
  for reasons that have nothing to do with the market.

SO THIS BREAKS THE WINDOW DOWN three ways -- by day, by hour of day, and by
series -- and prints the same breakdown for the ten days of history ENDING at
the break, so the two can be read against each other. A loss concentrated in
the badly-covered days is a sampling artefact. A loss spread evenly across
every day and hour is the market.

It also prints each day's coverage, in close-times collected out of the 96
that exist, so the reader can see which rows were thin when they judge them.

Read-only.
"""

import collections
import statistics as st
import sys
import time

import predictor as P
from score import ENTRY, MAJORS, STAKE, _f, bets, fee, ts

BAND = (0.80, 0.96)
SIDES = ("YES", "NO")
BREAK = "2026-09-26T23:30:00Z"      # last close time in the searched history
HOURS = ((0, 6), (6, 12), (12, 18), (18, 24))


def load(tab, lo=None, hi=None):
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(tab).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    out, allct = [], collections.defaultdict(set)
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        ctr = str(c("Close Time"))
        t = ts(ctr)
        if t is None:
            continue
        allct[ctr[:10]].add(ctr)
        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        if lo is not None and t <= lo:
            continue
        if hi is not None and t > hi:
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ct=ctr, t=t, bid=b, ask=a, yes=(res == "yes"),
                        day=ctr[:10],
                        hour=int(((t // 3600) - 5) % 24),
                        ser=str(c("Series"))))
    return out, allct


def row(bs, label, cover=None):
    n = len(bs)
    if not n:
        print(f"    {label:<16} {'-':>6}")
        return
    bb = bets(bs, BAND, SIDES)
    if not bb:
        print(f"    {label:<16} {n:>6} markets, no qualifying bets")
        return
    k = len(bb)
    profit = sum(b[0] for b in bb)
    win = sum(1 for b in bb if b[3]) / k
    px = st.mean([b[2] for b in bb])
    c = int(STAKE / px)
    need = (c * px + fee(c, px)) / c
    cov = f"{cover:>3.0f}%" if cover is not None else "   -"
    flag = " <<" if (win - need) < -0.01 else ""
    print(f"    {label:<16} {k:>6} bets  won {100*win:>5.1f}% vs "
          f"{100*need:>5.1f}%  ${profit:>+8.2f}  "
          f"{100*profit/(STAKE*k):>+6.2f}%  cover {cov}{flag}")


def main():
    cut = ts(BREAK)
    after, ctA = load("M15", lo=cut)
    before, ctB = load("M15H", lo=cut - 10 * 86400, hi=cut)
    print(f"  after the break (M15):   {len(after)} markets")
    print(f"  ten days before (M15H):  {len(before)} markets")

    print("\n" + "=" * 104)
    print("DAY BY DAY AFTER THE BREAK, with the collector's coverage beside it")
    print("=" * 104)
    byday = collections.defaultdict(list)
    for m in after:
        byday[m["day"]].append(m)
    for d in sorted(byday):
        poss = 96
        got = len(ctA.get(d, ()))
        row(byday[d], d, cover=100.0 * got / poss)
    print("    (<< marks a day whose win rate fell more than a point below "
          "its break-even)")

    print("\n" + "=" * 104)
    print("THE TEN DAYS BEFORE THE BREAK, same rule, from the searched history")
    print("=" * 104)
    byday2 = collections.defaultdict(list)
    for m in before:
        byday2[m["day"]].append(m)
    for d in sorted(byday2):
        row(byday2[d], d)

    print("\n" + "=" * 104)
    print("BY HOUR OF DAY (CT) -- is the loss in the hours the collector lost?")
    print("=" * 104)
    for lo, hi in HOURS:
        a = [m for m in after if lo <= m["hour"] < hi]
        b = [m for m in before if lo <= m["hour"] < hi]
        print(f"  {lo:02d}-{hi:02d}h CT")
        row(b, "  before")
        row(a, "  after")

    print("\n" + "=" * 104)
    print("BY SERIES -- did one coin break, or all five?")
    print("=" * 104)
    for s in MAJORS:
        a = [m for m in after if m["ser"] == s]
        b = [m for m in before if m["ser"] == s]
        print(f"  {s}")
        row(b, "  before")
        row(a, "  after")

    print("\n" + "=" * 104)
    print("A loss concentrated in the thin days, or in one hour band, or in")
    print("one coin, is a sampling artefact and the rule is not dead. A loss")
    print("spread evenly across every day, hour and coin is the market, and")
    print("the rule is.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())

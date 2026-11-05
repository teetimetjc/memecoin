"""The live T-9 record by STAKE. Which number is the real one?

TWO READS OF THE SAME STRATEGY DISAGREE, and the January decision rests on
which is right:

    all stakes      n 660   won 92.73%   net  -$7.33   ROI -0.23%
    $4/$5 only      n 504   won 94.64%   net +$58.92   ROI +2.72%

156 bets separate them and account for a $66 swing. Stake scales an edge;
it cannot create or destroy one, so a clean $4/$5 slice of the same
strategy should land near the pooled figure. It does not.

TWO CANDIDATE EXPLANATIONS, and they have opposite consequences:

  AN ERA. The stake went $4 -> $7 on 2026-10-10 and has moved since. If the
  excluded bets are simply an earlier period, then this is a date cut
  wearing a stake label, and the honest headline is the pooled -0.23%.

  THE LADDER. ladder.py shrinks the stake after three losses in a row and
  holds it down until three wins. Its own note explains why that works:
  "losses cluster, because bets sharing a close window resolve the same way
  in 23 of 24 cases." If reduced-stake bets are concentrated in bad
  stretches, then STAKE IS SET BY RECENT OUTCOMES, filtering on stake
  partly filters on outcome, and the +2.72% is selection rather than a
  finding. That would be the more serious answer, because it means any cut
  of this record by stake is contaminated.

So this prints what is actually there: every stake bucket with its own
record, each bucket's date span, and how its bets are tagged. An era shows
up as buckets that do not overlap in time. A ladder effect shows up as
small stakes scattered across the whole period and losing.

NO VERDICT IS FITTED. If the buckets are too thin to separate the two
stories, it says so.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import statistics as st

import coincut as C
import tally as T


def main():
    print("=" * 100)
    print("THE LIVE T-9 RECORD BY STAKE -- is the $4/$5 cut a clean slice?")
    print("=" * 100)

    bets, _ = C.load()
    kept, why = C.keep(bets)
    print(f"\n  {len(bets)} row(s) -> {len(kept)} after the T-9 coin filter")

    settled = [b for b in kept if b.get("rev") is not None and b.get("cost")]
    print(f"  {len(settled)} settled with a cost and a result")

    by = collections.defaultdict(list)
    for b in settled:
        s = T.stake_of(b)
        by[s if s is not None else 0].append(b)

    print("\n" + "-" * 100)
    print(f"  {'stake':>7}{'n':>6}{'won %':>9}{'net $':>11}{'ROI %':>9}"
          f"   {'first':<12}{'last':<12}  tags")
    print("-" * 100)
    tot_n = tot_net = 0.0
    for s in sorted(by):
        g = by[s]
        n = len(g)
        won = 100.0 * sum(1 for b in g if (b["rev"] or 0) > 0) / n
        staked = sum(b["cost"] or 0 for b in g)
        net = sum(b["rev"] or 0 for b in g) - staked
        roi = 100.0 * net / staked if staked else 0.0
        dates = sorted(T.ct_parts(b.get("ts") or "")[0] for b in g)
        tags = collections.Counter((b.get("strategy") or "(blank)").lower()
                                   for b in g)
        tagstr = ", ".join(f"{k}:{v}" for k, v in tags.most_common(3))
        tot_n += n
        tot_net += net
        print(f"  ${s:>5}{n:>6}{won:>8.1f}%{net:>+11.2f}{roi:>+9.2f}"
              f"   {dates[0]:<12}{dates[-1]:<12}  {tagstr}")
    print("-" * 100)
    print(f"  {'all':>6}{tot_n:>6.0f}{'':>9}{tot_net:>+11.2f}")

    # The decisive question: are the EXCLUDED bets a separate era, or spread
    # across the same days as the kept ones?
    keep_s = {4, 5}
    inc = [b for b in settled if T.stake_of(b) in keep_s]
    exc = [b for b in settled if T.stake_of(b) not in keep_s]
    print("\n" + "=" * 100)
    print("ERA OR LADDER?")
    print("=" * 100)
    if not exc:
        print("  Nothing is excluded at this stake filter; the two reads")
        print("  cannot differ for this reason.")
        return 0

    di = sorted({T.ct_parts(b.get("ts") or "")[0] for b in inc})
    de = sorted({T.ct_parts(b.get("ts") or "")[0] for b in exc})
    overlap = sorted(set(di) & set(de))
    print(f"  $4/$5 bets      n {len(inc):>4}   {di[0]} .. {di[-1]}"
          f"   ({len(di)} days)")
    print(f"  everything else n {len(exc):>4}   {de[0]} .. {de[-1]}"
          f"   ({len(de)} days)")
    print(f"  days carrying BOTH: {len(overlap)}")
    ns = sum(b["rev"] or 0 for b in exc) - sum(b["cost"] or 0 for b in exc)
    es = sum(b["cost"] or 0 for b in exc)
    print(f"  the excluded bets: net ${ns:+.2f}   "
          f"ROI {100.0 * ns / es if es else 0:+.2f}%")
    print()
    if len(overlap) <= 1:
        print("  AN ERA. The two groups barely share a day, so the $4/$5")
        print("  filter is a DATE cut wearing a stake label. It compares two")
        print("  periods, not two bet sizes, and the honest headline for the")
        print("  whole strategy stays the pooled figure.")
    elif len(overlap) >= 0.5 * len(di):
        print("  INTERLEAVED. The two groups run through the same days, so")
        print("  this is not an era. Stake is then being set WITHIN the same")
        print("  conditions -- the ladder is the only thing that does that --")
        print("  and a stake filter is partly an outcome filter. Treat the")
        print("  $4/$5 figure as selected, not measured.")
    else:
        print("  MIXED, and this sample cannot separate the two stories.")
        print("  No verdict is offered. The pooled figure remains the one")
        print("  with no selection question hanging over it.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

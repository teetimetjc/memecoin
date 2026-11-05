"""The LIVE T-9 record by hour of day. $4 and $5 bets only.

THE QUESTION IS FAIR AND THE METHOD IS A TRAP, so the method is fixed here
before any number is read.

Twenty-four hours is twenty-four chances to find a pattern. With ~600 bets
that is about 25 per hour, and at a 92.6% win rate the expected number of
losses in a cell is under two. A couple of unlucky bets turns an hour deep
red; a couple of lucky ones turns it green. SOMETHING WILL ALWAYS LOOK
STRIKING, and reporting the worst cell as a finding is how this project
would talk itself into a rule that costs money.

hourtest.py already asked this of the BACKTEST history, where each hour has
hundreds of observations. This asks it of the live record, which is what
was requested, and which is thin. Both facts get printed.

SO THREE RULES, PRE-REGISTERED:

  1. CHRONOLOGICAL BOTH-HALVES. Every hour is split at the median date of
     the kept bets. An hour counts as a lead only if the sign of its ROI is
     the same in both halves. A sign that flips is noise, and this project
     has watched that happen to drift, to coins, to the NO side and to T-6.

  2. ADJACENCY. Hours are not independent -- 2am and 3am share conditions.
     A real effect is a BLOCK of neighbouring hours leaning the same way.
     One red hour between two green ones is a thin cell, not a mechanism.
     Neighbours are printed beside each hour so this is visible.

  3. A MINIMUM CELL. Fewer than MIN_N bets in an hour and no verdict is
     offered at all, in either direction.

AND THE BAR: the pooled record is about -0.5% with an interval that
straddles zero. Against that, an hour needs to be far enough out to clear
noise, not merely the worst of twenty-four. The expected spread of the
worst cell under pure chance is printed so the eye has something honest to
compare against.

CENTRAL TIME, because that is the clock the operator lives on. tally's
ct_parts handles the CST/CDT change rather than a fixed offset: the record
now spans the November switch, and an hour-wrong bucket would smear every
cell into its neighbour.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import os
import statistics as st

import coincut as C
import tally as T

START = os.environ.get("HOURCUT_START") or C.START
# $4 and $5 only, as asked. T-3 ran at $3 and is excluded by coincut's own
# filter anyway; this makes the stake condition explicit rather than
# implied, so the population is stated and not inferred.
STAKES = tuple(float(x) for x in
               (os.environ.get("HOURCUT_STAKES") or "4,5").split(","))
MIN_N = int(os.environ.get("HOURCUT_MIN_N") or 20)


def hour_of(b):
    """Hour of the bet in Central, 0-23, or None."""
    ts = b.get("ts") or b.get("time") or b.get("Timestamp")
    if not ts:
        return None
    _, hhmm = T.ct_parts(ts)
    s = str(hhmm).strip().lower()
    try:
        if s.endswith("am") or s.endswith("pm"):
            hh = int(s.split(":")[0])
            if s.endswith("pm") and hh != 12:
                hh += 12
            if s.endswith("am") and hh == 12:
                hh = 0
            return hh
        return int(s.split(":")[0])
    except Exception:
        return None


def roi(group):
    staked = sum(b["cost"] or 0 for b in group)
    if not staked:
        return None
    return 100.0 * (sum(b["rev"] or 0 for b in group) - staked) / staked


def main():
    print("=" * 104)
    print(f"THE LIVE T-9 RECORD BY HOUR (Central), stakes "
          f"{'/'.join(f'${s:.0f}' for s in STAKES)}, since {START} UTC")
    print("  Pre-registered: both-halves sign agreement, adjacency, and a "
          f"{MIN_N}-bet minimum cell.")
    print("=" * 104)

    bets, _ = C.load()
    if not bets:
        print("\n  no bets read.")
        return 1
    kept, why = C.keep(bets)
    print(f"\n  {len(bets)} row(s) -> {len(kept)} after the T-9 coin filter")
    for k, v in why.most_common():
        print(f"    {v:>4}  {k}")

    sized = [b for b in kept
             if any(abs(T.stake_of(b) - s) < 0.01 for s in STAKES)]
    print(f"  {len(sized)} at {'/'.join(f'${s:.0f}' for s in STAKES)}")
    settled = [b for b in sized if b.get("rev") is not None and b.get("cost")]
    print(f"  {len(settled)} settled with a cost and a result -- the sample "
          f"every number below is computed on")
    if len(settled) < MIN_N * 2:
        print("\n  TOO FEW TO CUT BY HOUR. Stopping rather than printing 24")
        print("  cells of three bets each, which would be noise with a table "
              "around it.")
        return 0

    pooled = roi(settled)
    wins = sum(1 for b in settled if (b["rev"] or 0) > 0)
    w = wins / len(settled)
    print(f"\n  POOLED: n {len(settled)}   won {100*w:.2f}%   "
          f"ROI {pooled:+.2f}%   -- this is the line every hour is "
          f"measured against")

    # The honest yardstick: how extreme the worst of 24 thin cells looks
    # when nothing at all is going on.
    per = len(settled) / 24.0
    if per >= 2:
        sd_win = math.sqrt(max(w * (1 - w), 0.0) / per) * 100.0
        print(f"  with ~{per:.0f} bets an hour, one cell's win rate has a "
              f"standard error of {sd_win:.1f} points;")
        print(f"  across 24 cells the WORST looks about "
              f"{2.0 * sd_win:.0f} points below the mean by chance alone.")

    by_hour = collections.defaultdict(list)
    for b in settled:
        h = hour_of(b)
        if h is not None:
            by_hour[h].append(b)

    dates = sorted(T.ct_parts(b.get("ts") or "")[0] for b in settled)
    mid = dates[len(dates) // 2]
    print(f"  chronological split at {mid}")

    print("\n" + "-" * 104)
    print(f"  {'hour':<8}{'n':>5}{'won %':>9}{'net $':>10}{'ROI %':>9}"
          f"{'1st half':>11}{'2nd half':>11}   verdict")
    print("-" * 104)
    leads = []
    for h in range(24):
        g = by_hour.get(h) or []
        label = f"{h:02d}:00"
        if not g:
            print(f"  {label:<8}{0:>5}{'--':>9}{'--':>10}{'--':>9}"
                  f"{'--':>11}{'--':>11}")
            continue
        n = len(g)
        won = 100.0 * sum(1 for b in g if (b["rev"] or 0) > 0) / n
        staked = sum(b["cost"] or 0 for b in g)
        net = sum(b["rev"] or 0 for b in g) - staked
        r = roi(g)
        a = [b for b in g if T.ct_parts(b.get("ts") or "")[0] <= mid]
        z = [b for b in g if T.ct_parts(b.get("ts") or "")[0] > mid]
        ra, rz = roi(a), roi(z)
        fa = f"{ra:+.1f}" if ra is not None else "--"
        fz = f"{rz:+.1f}" if rz is not None else "--"
        verdict = ""
        if n < MIN_N:
            verdict = "thin -- no verdict"
        elif ra is None or rz is None:
            verdict = "one half empty"
        elif (ra < 0) == (rz < 0):
            verdict = "sign holds in both halves"
            leads.append((h, r, n, ra, rz))
        else:
            verdict = "sign FLIPS -- noise"
        print(f"  {label:<8}{n:>5}{won:>8.1f}%{net:>+10.2f}{r:>+9.2f}"
              f"{fa:>11}{fz:>11}   {verdict}")

    print("\n" + "=" * 104)
    if not leads:
        print("  NO HOUR SURVIVES. Every cell with enough bets flips sign")
        print("  between the two halves, which is what a record with no")
        print("  hour-of-day effect looks like. The worst-looking hour in")
        print("  the table above is the worst of twenty-four draws, not a")
        print("  finding, and trading on it would be fitting noise.")
    else:
        print("  SURVIVED BOTH HALVES:")
        for h, r, n, ra, rz in sorted(leads, key=lambda x: x[1]):
            nb = [x[0] for x in leads if abs(x[0] - h) == 1]
            adj = (f"neighbours {nb} lean the same way"
                   if nb else "NO neighbouring hour agrees -- isolated cell")
            print(f"    {h:02d}:00  n {n}  ROI {r:+.2f}%  "
                  f"({ra:+.1f} / {rz:+.1f})   {adj}")
        print()
        print("  AN ISOLATED HOUR IS STILL PROBABLY NOISE. Hours either side")
        print("  share the same conditions, so a real effect shows up as a")
        print("  block. A lone survivor among twenty-four cells is what")
        print("  chance produces, and surviving a two-way split is a weak")
        print("  filter when the halves are this thin.")
    print()
    print("  WHATEVER THIS SHOWS, IT DOES NOT LICENCE A RULE. Cutting hours")
    print("  from a strategy whose pooled edge is already indistinguishable")
    print("  from zero removes bets, not losses, and shrinks the sample that")
    print("  the January decision depends on.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

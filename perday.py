"""Bets per day, split by stake -- and why that split cannot mean what it looks like.

THE QUESTION: how many bets a day at $4 against how many at $5.

THE ANSWER IS WORTH HAVING and the obvious reading of it is wrong, so both
are printed together rather than leaving the table to be read alone.

STAKE DOES NOT DECIDE HOW MANY BETS GET PLACED. The selector picks signals
on price band and entry timing and caps them at FAVLIVE_MAX_PER_WINDOW;
stake only decides how many contracts each signal buys, through
int(stake / price). Change $5 to $4 and the same signals fire the same
number of times for fewer contracts each. So a difference in bets per day
between the two is a difference between two PERIODS, not between two sizes:
different markets, different config, different outages.

WHAT THE PERIODS ACTUALLY DIFFERED IN, which the daily rows below make
visible: the 14 Oct restart, the quiet-hours window that suppressed 04:00-
11:59 UTC until 15 Oct, the ~7-hour halt on the 409 bug, the ~4.5-hour
silent idle on the status="open" read, and the sessions that cancelled each
other on 29 Oct. Every one of those shows up as a thin day, and none of them
has anything to do with stake.

THE ONE REAL CHANNEL FROM STAKE TO COUNT, stated so the claim above is not
overstated: the funding check halts a session when the balance cannot cover
a window, so at a low balance a larger stake can halt sooner and place
fewer bets. That is a bankroll effect rather than a selector effect, and it
would show as a run of empty days at the end of a period, not as a steady
lower rate.

SO THE DAILY ROWS ARE PRINTED, not just the averages. An average over a
period containing a 7-hour outage is a statement about the outage.

Read-only. Counts only real PLACED bets -- tally.rows_since filters on
Status == 'PLACED', so paper rows in the Paper Bets tab cannot reach this.
"""
import collections
import time

import tally as T

START = "2026-10-07 15:49"


def day_of(ts):
    """Calendar day in Chicago time, matching the dashboard's Date CT."""
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
        return time.strftime("%Y-%m-%d", time.gmtime(t - 5 * 3600))
    except Exception:
        return ts[:10]


def main():
    bets = T.rows_since(START)
    if not bets:
        print("  no bets in the record")
        return 1

    by_stake = collections.defaultdict(lambda: collections.Counter())
    for b in bets:
        s = str(T.stake_of(b) or "?")
        by_stake[s][day_of(b["ts"])] += 1

    print("=" * 96)
    print("BETS PER DAY, BY STAKE")
    print(f"  {len(bets)} placed bets since {START} UTC")
    print("=" * 96)

    def sort_key(s):
        try:
            return (0, float(s))
        except ValueError:
            return (1, 0.0)

    print(f"\n  {'stake':>6} {'bets':>6} {'days':>6} {'per day':>8} "
          f"{'busiest':>8} {'thinnest':>9}   span")
    print("  " + "-" * 88)
    for s in sorted(by_stake, key=sort_key):
        days = by_stake[s]
        n = sum(days.values())
        d = len(days)
        counts = sorted(days.values())
        print(f"  ${s:>5} {n:>6} {d:>6} {n/d:>8.1f} {counts[-1]:>8} "
              f"{counts[0]:>9}   {min(days)} .. {max(days)}")

    print(f"\n{'-' * 96}")
    print("EVERY DAY, so an average over an outage is not mistaken for a rate")
    print("-" * 96)
    for s in sorted(by_stake, key=sort_key):
        days = by_stake[s]
        n = sum(days.values())
        print(f"\n  ${s} -- {n} bets over {len(days)} day(s), "
              f"{n/len(days):.1f}/day")
        for day in sorted(days):
            c = days[day]
            bar = "#" * min(c, 40)
            print(f"    {day}  {c:>3}  {bar}")

    print(f"\n{'=' * 96}")
    print("  HOW TO READ IT, and the trap in reading it any other way.")
    print()
    print("  STAKE DOES NOT CHANGE HOW MANY BETS FIRE. The selector picks on")
    print("  price band and entry timing and caps per window; stake only")
    print("  sets contracts = int(stake / price). The same signals fire the")
    print("  same number of times at $4 and at $5.")
    print()
    print("  So any difference above is between PERIODS, not between sizes --")
    print("  different markets and different outages. The daily rows show")
    print("  which: a day at 2 bets is a session that was down, not a market")
    print("  that went quiet, and it drags an average it has no business in.")
    print()
    print("  THE ONE EXCEPTION, so the claim is not overstated: the funding")
    print("  check halts a session that cannot cover a window, so at a low")
    print("  balance a larger stake can stop sooner. That shows up as empty")
    print("  days at the END of a period, not as a steadily lower rate.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

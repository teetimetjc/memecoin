"""Summarise the ARB tab: how often, how big, how long, how much.

arb.py records one row per strike-pair contradiction as it disappears. This
answers the three questions that decide whether the finding is a business or a
curiosity, and it answers them in the order that matters -- rate first, because
a large edge once a fortnight is not a strategy; then SIZE, because a 36-cent
edge on five contracts is $1.80; then DURATION, because a crossing that lives
two seconds cannot be taken by anything that has to place two orders.

ROWS BEFORE THE FIX ARE EXCLUDED. The first run of arb.py paired strikes across
different events -- "gold above 4143.99 settling at 06:00" against "gold above
4145.99 settling at 07:00", which are different bets -- and wrote about a
hundred rows that mean nothing. The cutoff below is the moment that was fixed.
Counting them would inflate the rate by roughly fifty times, which is exactly
how the finding looked before the bug was found.

WHAT THIS STILL CANNOT TELL YOU. Whether the two legs could actually be filled.
Both prices were quoted at one instant by one read, and the resting size is
recorded, but placing two orders takes time and a one-sided fill leaves a naked
directional position -- the opposite of the trade's purpose. Everything below is
an upper bound on an attempt, not a record of money.

Read-only.
"""

import collections
import statistics as st
import sys

import predictor as P

SHEET = "ARB"
# arb.py began pairing within one event at this moment; everything earlier
# compared markets with different settlement times.
CUTOFF = "2026-10-06 00:21:00 UTC"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(SHEET).get_all_values()
    except Exception as e:
        print(f"no {SHEET} tab yet ({str(e)[:60]})")
        return 0
    if len(rows) < 2:
        print(f"{SHEET} exists but holds no rows yet")
        return 0
    h = {k: i for i, k in enumerate(rows[0])}

    def col(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    keep, dropped = [], 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        if str(r[0]) < CUTOFF:
            dropped += 1
            continue
        keep.append(r)
    print(f"  {SHEET}: {len(keep)} valid rows "
          f"({dropped} pre-fix rows excluded)")
    if not keep:
        print("\n  Nothing recorded since the fix. At the rate ladder.py")
        print("  implies -- 44 crossings across weeks of six-moment sampling,")
        print("  so roughly one a day -- a short window of zero is the")
        print("  expected result and not evidence of absence.")
        return 0

    days = collections.Counter(str(r[0])[:10] for r in keep)
    nets = [_f(col(r, "Net c per contract")) for r in keep]
    nets = [x for x in nets if x is not None]
    takes = [_f(col(r, "Takeable")) for r in keep]
    takes = [x for x in takes if x is not None and x > 0]
    secs = [_f(col(r, "Seen s")) for r in keep]
    secs = [x for x in secs if x is not None]
    locked = [_f(col(r, "Locked $")) for r in keep]
    locked = [x for x in locked if x is not None]
    byser = collections.Counter(str(col(r, "Series")) for r in keep)

    print("\n" + "=" * 92)
    print("RATE -- how often does the ladder contradict itself?")
    print("=" * 92)
    for d in sorted(days):
        print(f"  {d}   {days[d]:>4} crossings")
    print(f"  across {len(days)} day(s): "
          f"{len(keep)/max(1,len(days)):.1f} per day")
    print(f"  by series: "
          + ", ".join(f"{k} {v}" for k, v in byser.most_common(8)))

    print("\n" + "=" * 92)
    print("SIZE -- a 36c edge on five contracts is $1.80")
    print("=" * 92)
    if takes:
        print(f"  takeable contracts   median {st.median(takes):.0f}   "
              f"min {min(takes):.0f}   max {max(takes):.0f}")
    else:
        print("  no size recorded on any crossing")
    if nets:
        print(f"  net cents/contract   median {st.median(nets):+.2f}   "
              f"max {max(nets):+.2f}")
    if locked:
        tot = sum(x for x in locked if x > 0)
        print(f"  locked $ per crossing median "
              f"${st.median([x for x in locked if x > 0] or [0]):.2f}   "
              f"max ${max(locked):.2f}")
        print(f"  TOTAL if every one were filled once: ${tot:.2f} "
              f"over {len(days)} day(s)  (${tot/max(1,len(days)):.2f}/day)")

    print("\n" + "=" * 92)
    print("DURATION -- could two orders have been placed in time?")
    print("=" * 92)
    if secs:
        print(f"  lifetime seconds   median {st.median(secs):.0f}   "
              f"min {min(secs):.0f}   max {max(secs):.0f}")
        quick = sum(1 for x in secs if x <= 20)
        slow = sum(1 for x in secs if x >= 60)
        print(f"  gone within one poll (<=20s)  {quick}/{len(secs)}")
        print(f"  lasted a minute or more       {slow}/{len(secs)}")
        print("  A 20s floor is the poll interval, not the true lifetime: a")
        print("  crossing seen once may have lived a second or nineteen.")

    print("\n" + "=" * 92)
    print("Everything above is an UPPER BOUND on an attempt. Two legs means")
    print("two orders, and a one-sided fill leaves a naked directional")
    print("position -- the opposite of the point of the trade.")
    print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())

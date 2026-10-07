"""How many bets, and how many phone notifications, per day?

I told the owner to expect about two bets a day. That came from dividing bets
by FIRING windows (2,259 / 1,144 = 2.0) and then reading the answer as a daily
rate, which it is not -- it is the number of bets in a window that produces
any. The daily rate is bets divided by DAYS, and for the narrow slice that is
nearer thirty. Being out by a factor of sixteen on "how often will my phone
buzz" is worth a file that computes it rather than a second guess.

The two quantities are different and both matter:

  BETS PER DAY is money at risk and the rate the edge accumulates at.
  NOTIFICATIONS PER DAY is how often the phone buzzes, which is per WINDOW,
  not per bet -- favlive calls alert.send once with every signal in the window
  -- and is then cut by alert.py's quiet hours, which suppress anything outside
  07:00-22:00 local.

stake.py's TURNOVER block has the same mistake and is corrected alongside this:
it multiplied bets-per-firing-window by all 96 daily windows, as though every
window fired. Only about a sixth of them do, so that line overstated the daily
rate roughly six-fold.

Read-only.
"""

import collections
import sys
import time

import score as S

QUIET_START, QUIET_END = 7, 22          # alert.py's defaults, local hours
CT_OFFSET = -5                          # America/Chicago vs UTC, CDT


def local_hour(ct):
    """The local hour of a close time, for the quiet-hours count.

    Fixed offset rather than a tz database lookup: this is a rate estimate and
    an hour of DST slippage changes nothing it is used for. Stated so the
    approximation is visible instead of implied.
    """
    try:
        h = int(str(ct)[11:13])
    except (ValueError, IndexError):
        return None
    return (h + CT_OFFSET) % 24


def main():
    H = S.load_hist()
    if not H:
        print("no history")
        return 1

    all_days = {str(m["ct"])[:10] for m in H}
    print("\n" + "=" * 88)
    print("HOW OFTEN -- bets and notifications per day")
    print("=" * 88)
    print(f"  history covers {len(all_days)} distinct days, "
          f"{len({m['ct'] for m in H}):,} close times with a T-{S.ENTRY} quote")

    for r in S.RULES:
        bs = S.bets(H, r["band"], r["sides"])
        if not bs:
            continue
        days = sorted({b[1][:10] for b in bs})
        windows = {b[1] for b in bs}
        per_window = collections.Counter(b[1] for b in bs)
        awake = [w for w in windows
                 if (lh := local_hour(w)) is not None
                 and QUIET_START <= lh < QUIET_END]

        n = len(bs)
        nd = max(1, len(days))
        print("\n" + "-" * 88)
        print(f"  {r['name'].upper()}  "
              f"({100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c, "
              f"{'/'.join(r['sides'])})")
        print("-" * 88)
        print(f"    {n:,} bets over {len(days)} days "
              f"in {len(windows):,} distinct windows")
        print(f"    bets per day                 {n/nd:>6.1f}")
        print(f"    windows that fire, per day   {len(windows)/nd:>6.1f}"
              f"   ({100.0*len(windows)/(nd*96):.0f}% of the 96 daily windows)")
        print(f"    bets per firing window       {n/max(1,len(windows)):>6.1f}"
              f"   <- the number I misread as a daily rate")
        print()
        print(f"    NOTIFICATIONS: one per firing window, so {len(windows)/nd:.1f}"
              f"/day before quiet hours,")
        print(f"    and {len(awake)/nd:>4.1f}/day after suppressing anything "
              f"outside {QUIET_START:02d}:00-{QUIET_END:02d}:00 local.")
        gap = (QUIET_END - QUIET_START) * 60.0 / max(1e-9, len(awake) / nd)
        print(f"    That is roughly one every {gap:.0f} minutes "
              f"during waking hours.")
        busiest = max(per_window.values())
        print(f"    Busiest single window in the history: {busiest} bets.")

    print("\n" + "=" * 88)
    print("Bets per day is money at risk. Notifications per day is how often")
    print("the phone buzzes, and the two differ because a window with three")
    print("signals sends one message.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    sys.exit(main())

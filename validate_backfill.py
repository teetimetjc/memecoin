"""Do the backfilled rows agree with the ones collected live?

M15H was rebuilt from candlesticks; M15 was read off the book as it
happened. Where the two cover the same market they should agree, and if
they do not then 43,122 historical rows are measuring something subtly
different from the 2,300 forward ones -- which would quietly invalidate
every comparison between them.

TWO THINGS ARE CHECKED AND THEY FAIL DIFFERENTLY.

  The RESULT must match exactly. Both come from Kalshi's own settlement, so
  any disagreement is a join bug, not a data quality question.

  The QUOTES need not match exactly, and expecting them to would be naive.
  A candle reports the quote at the CLOSE of its minute; the live collector
  read the book at whatever second inside that minute its poll landed. So
  the question is not "are they identical" but "is the difference small and
  unbiased". A systematic offset would mean the two datasets sit at
  different points in the price path and cannot be pooled.

Read-only.
"""

import collections
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def load(sh, name):
    rows = sh.worksheet(name).get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        out[r[0]] = dict(result=str(c("Result")).lower().strip(),
                         close=str(c("Close Time")),
                         q={o: (_f(c(f"bid{o}")), _f(c(f"ask{o}")))
                            for o in OFFSETS})
    return out


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    live = load(sh, "M15")
    hist = load(sh, "M15H")
    both = sorted(set(live) & set(hist))
    print("=" * 74)
    print("BACKFILL VALIDATION")
    print("=" * 74)
    print(f"  live rows {len(live)}   backfilled rows {len(hist)}"
          f"   overlapping tickers {len(both)}")
    if not both:
        print("\n  NO OVERLAP -- the two tabs cover different markets, so this")
        print("  cannot be checked. That is itself a finding: without a shared")
        print("  market the datasets are unverifiable against each other.")
        return 0

    # 1. settlement must be identical
    same = sum(1 for t in both if live[t]["result"] == hist[t]["result"])
    print(f"\n  settlement agrees: {same}/{len(both)}"
          f"  ({100*same/len(both):.1f}%)")
    if same != len(both):
        for t in both:
            if live[t]["result"] != hist[t]["result"]:
                print(f"    MISMATCH {t}: live={live[t]['result']}"
                      f" hist={hist[t]['result']}")
                break

    # 2. quotes: how far apart, and is the gap one-sided?
    print(f"\n  {'offset':<9}{'pairs':>7}{'exact':>8}{'med |d|':>9}"
          f"{'mean d':>9}{'p90 |d|':>9}")
    alld = []
    for o in OFFSETS:
        d = []
        for t in both:
            for k in (0, 1):
                a, b = live[t]["q"][o][k], hist[t]["q"][o][k]
                if a is None or b is None:
                    continue
                d.append(b - a)
        if not d:
            print(f"  T-{o:<7}{0:>7}   no overlapping quotes")
            continue
        alld += d
        ex = sum(1 for x in d if abs(x) < 1e-9)
        ad = sorted(abs(x) for x in d)
        print(f"  T-{o:<7}{len(d):>7}{100*ex/len(d):>7.0f}%"
              f"{100*st.median(ad):>8.2f}c{100*st.mean(d):>+8.2f}c"
              f"{100*ad[int(.9*len(ad))]:>8.2f}c")
    if alld:
        ex = sum(1 for x in alld if abs(x) < 1e-9)
        ad = sorted(abs(x) for x in alld)
        m = st.mean(alld)
        print(f"\n  ALL      {len(alld):>7}{100*ex/len(alld):>7.0f}%"
              f"{100*st.median(ad):>8.2f}c{100*m:>+8.2f}c"
              f"{100*ad[int(.9*len(ad))]:>8.2f}c")
        print(f"\n  'mean d' is the signed gap, backfill minus live. Near zero")
        print(f"  means the two sit at the same point in the price path. A")
        print(f"  one-sided gap would mean they do not, and that pooling them")
        print(f"  compares different moments rather than different markets.")
        big = sum(1 for x in ad if x > 0.05)
        print(f"\n  quotes more than 5c apart: {big} ({100*big/len(ad):.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

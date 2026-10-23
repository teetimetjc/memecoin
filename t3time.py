"""Is the T-3 edge recent-only, or did two samples just cover two periods?

THE CONTRADICTION THIS EXISTS TO SETTLE. Two measurements of the SAME cell --
the 0.90-0.94 band at T-3 on the five coins -- disagree:

    leftover.py   1,200 markets/series   295 in band   +3.20%   halves +2.80% / +3.66%
    t3grid.py     2,500 markets/series   680 in band   +1.17%   halves -0.87% / +3.53%
    t3low.py      2,500 markets/series   680 in band   +1.17%   halves -0.87% / +3.53%

The two deeper runs agree with each other exactly, so this is not a bug in
one of them. And in t3grid EVERY band, all twelve, came out negative in the
older half and positive in the newer one. Twelve cells do not line up like
that by chance.

TWO EXPLANATIONS, VERY DIFFERENT CONSEQUENCES.

  THE EDGE IS RECENT. If the mispricing appeared a few weeks ago, then
  "holds in both halves" was never satisfied on a long sample, the T-3
  finding is a statement about a short recent window, and the live session
  is trading something that may already be gone. Every both-halves verdict
  in this project becomes suspect, because they all split whatever window
  the run happened to pull.

  THE SAMPLES COVER DIFFERENT PERIODS. 1,200 markets per series reaches back
  less far than 2,500. If the shallower pull simply did not include the older
  weeks, then leftover's "both halves positive" split a recent window in two
  and never saw the older period at all -- the two runs would be measuring
  different things and neither would be wrong.

THE SECOND IS CHECKABLE DIRECTLY and it is checked FIRST here: pull 2,500 per
series, then print the date range of the most recent 1,200 against the date
range of all 2,500. If 1,200 covers only the recent weeks, that alone
explains the disagreement.

THEN THE REAL TEST: the band scored week by week, in calendar order. A
half-split hides shape -- it can only ever say "earlier" and "later". Weeks
show whether the edge appeared, faded, wobbled, or was always there.

WHAT WOULD MEAN WHAT. A clean step up at some date is a regime change and
the recent weeks are the relevant sample. A noisy sequence straddling zero
with no trend is just variance, and the half-split signs were an artifact of
where the midpoint fell. Steady positive weeks throughout would mean the
deeper run's negative older half came from something else again, and that
would need chasing.

NO CONTROL CELL HERE, deliberately: this file is not judging a candidate
against a known value, it is measuring the known value's own stability. The
thing being questioned is the reference.

Read-only.
"""
import collections
import statistics as st
import sys
import time

import backfill as B

OFFSET = 3
BAND = (0.90, 0.94)
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
DEEP = 2500          # what t3grid/t3low pulled
SHALLOW = 1200       # what leftover pulled
MIN_WEEK = 25        # below this a week is printed but gets no verdict
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 16000


def ask_at(cs, close_ts, off):
    want = close_ts - off * 60
    best = None
    for cd in cs or []:
        t = B._f(cd.get("end_period_ts"))
        if t is None or t > want:
            continue

        def g(k):
            v = cd.get(k)
            return (B._f(v.get("close_dollars")) if isinstance(v, dict)
                    else None)
        a, b = g("yes_ask"), g("yes_bid")
        if a is None or b is None or not (0 < b <= a < 1):
            continue
        if best is None or t > best[0]:
            best = (t, a)
    return best[1] if best else None


def score(group):
    """(n, conv%, needed%, net ROI%) -- needed is price plus fee."""
    if not group:
        return None
    n = len(group)
    conv = 100.0 * sum(1 for _p, won in group if won) / n
    price = 100.0 * st.mean([p for p, _ in group])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in group])
    needed = price + fee
    return n, conv, needed, 100.0 * (conv - needed) / needed


def main():
    print("=" * 110)
    print(f"IS THE T-{OFFSET} EDGE RECENT? The "
          f"{BAND[0]:.2f}-{BAND[1]:.2f} band, week by week")
    print("=" * 110)
    started = time.time()
    end = started + BUDGET

    # rank = position in each series' own recency order, so "the most recent
    # 1,200" can be reconstructed exactly as leftover.py would have seen it.
    rows = []
    for i, ser in enumerate(SERIES):
        left = end - time.time()
        if left <= 30:
            print(f"  [budget gone; unmeasured: {', '.join(SERIES[i:])}]")
            break
        share = time.time() + max(30.0, left / (len(SERIES) - i))
        try:
            mk = B.settled_markets(ser, DEEP)
        except Exception as e:
            print(f"  {ser:<12} could not list ({str(e)[:45]})")
            continue
        got = 0
        for rank, m in enumerate(mk):
            if time.time() > min(share, end):
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            a = ask_at(cs, close, OFFSET)
            if a is not None:
                rows.append(dict(px=a, won=res == "yes", ts=close,
                                 rank=rank, ser=ser))
                got += 1
        print(f"  {ser:<12} {len(mk):>5} settled, {got:>5} readable")

    if len(rows) < 500:
        print(f"\n  only {len(rows)} readable markets -- too few. "
              "Nothing claimed.")
        return 0

    def day(t):
        return time.strftime("%Y-%m-%d", time.gmtime(t))

    print(f"\n  {len(rows)} markets readable at T-{OFFSET}\n")

    # ---- FIRST: do the two pull depths even cover the same period? -------
    print("=" * 110)
    print("STEP 1 -- DO THE TWO PULL DEPTHS COVER THE SAME DATES?")
    print("=" * 110)
    deep_ts = [r["ts"] for r in rows]
    shal_ts = [r["ts"] for r in rows if r["rank"] < SHALLOW]
    print(f"  all {DEEP}/series ({len(deep_ts)} readable): "
          f"{day(min(deep_ts))} -> {day(max(deep_ts))}")
    if shal_ts:
        print(f"  most recent {SHALLOW}/series ({len(shal_ts)} readable): "
              f"{day(min(shal_ts))} -> {day(max(shal_ts))}")
        gap_days = (min(shal_ts) - min(deep_ts)) / 86400.0
        print(f"\n  the shallower pull misses the oldest {gap_days:.1f} days")
        if gap_days >= 5:
            print("  THAT IS ENOUGH TO EXPLAIN THE DISAGREEMENT on its own:")
            print("  leftover.py never saw those days, so its 'both halves")
            print("  positive' split a shorter, more recent window in two.")
            print("  The two runs measured different periods; neither is a bug.")
        else:
            print("  The two pulls cover nearly the same period, so the")
            print("  disagreement is NOT explained by pull depth and the")
            print("  week-by-week table below has to account for it.")
    print()

    # Scored both ways, to show the effect rather than assert it.
    in_band = [(r["px"], r["won"]) for r in rows
               if BAND[0] <= r["px"] < BAND[1]]
    in_band_shal = [(r["px"], r["won"]) for r in rows
                    if BAND[0] <= r["px"] < BAND[1] and r["rank"] < SHALLOW]
    print("  the same band scored over each pull depth:")
    for lbl, g in ((f"all {DEEP}/series", in_band),
                   (f"recent {SHALLOW}/series", in_band_shal)):
        s = score(g)
        if s:
            print(f"    {lbl:<24} n={s[0]:<5} converts {s[1]:.2f}%  "
                  f"needs {s[2]:.2f}%  net {s[3]:+.2f}%")
    print()

    # ---- THEN: week by week ---------------------------------------------
    print("=" * 110)
    print("STEP 2 -- THE BAND WEEK BY WEEK, in calendar order")
    print("=" * 110)
    weeks = collections.defaultdict(list)
    for r in rows:
        if BAND[0] <= r["px"] < BAND[1]:
            wk = time.strftime("%Y-W%V", time.gmtime(r["ts"]))
            weeks[wk].append((r["px"], r["won"]))
    print(f"  {'week':<10} {'n':>5} {'converts':>10} {'needs':>9} "
          f"{'net ROI':>9}   {'bar':<24}")
    seq = []
    for wk in sorted(weeks):
        s = score(weeks[wk])
        if s is None:
            continue
        n, conv, needed, roi = s
        thin = "  (thin)" if n < MIN_WEEK else ""
        # A crude bar, centred on zero, so the shape is visible at a glance.
        slots = max(-10, min(10, int(round(roi))))
        bar = (" " * (10 + slots) + "#") if slots < 0 else \
              (" " * 10 + "#" * max(1, slots))
        print(f"  {wk:<10} {n:>5} {conv:>9.2f}% {needed:>8.2f}% "
              f"{roi:>+8.2f}%   {bar:<24}{thin}")
        if n >= MIN_WEEK:
            seq.append((wk, roi, n))
    print(f"  {'':<10} {'':>5} {'':>10} {'':>9} {'':>9}   "
          f"{'|':<11}zero")
    print()

    print("=" * 110)
    print("  WHAT THE SHAPE SAYS")
    print()
    if len(seq) < 4:
        print(f"  Only {len(seq)} weeks reached {MIN_WEEK} in-band markets. "
              "Not enough to read a trend.")
    else:
        pos = [w for w in seq if w[1] > 0]
        neg = [w for w in seq if w[1] <= 0]
        print(f"  {len(seq)} weeks with at least {MIN_WEEK} markets: "
              f"{len(pos)} positive, {len(neg)} negative")
        half = len(seq) // 2
        early = st.mean([w[1] for w in seq[:half]])
        late = st.mean([w[1] for w in seq[half:]])
        print(f"  mean weekly ROI: first half {early:+.2f}%, "
              f"second half {late:+.2f}%")
        flips = sum(1 for a, b in zip(seq, seq[1:])
                    if (a[1] > 0) != (b[1] > 0))
        print(f"  sign changes week to week: {flips} of {len(seq) - 1}")
        print()
        if flips >= (len(seq) - 1) * 0.4:
            print("  THIS LOOKS LIKE VARIANCE, NOT A REGIME. The sign flips")
            print("  most weeks, which means the half-split signs were an")
            print("  artifact of where the midpoint happened to fall. The")
            print("  honest reading is that a weekly sample is too small to")
            print("  show an edge of a few points either way -- and that the")
            print("  'holds in both halves' bar is weaker evidence than it")
            print("  has been treated as throughout this project.")
        elif late - early > 1.5:
            print("  THIS LOOKS LIKE A REGIME CHANGE: the later weeks are")
            print("  materially better and the sign is stable within each")
            print("  stretch. If so, the recent weeks are the relevant")
            print("  sample, the live T-3 session is trading the regime it")
            print("  was measured in, and the older data should be excluded")
            print("  EXPLICITLY and in advance rather than by pull depth.")
        elif early - late > 1.5:
            print("  THE EDGE IS FADING: earlier weeks better than later.")
            print("  That is the worst case for the live session, and it")
            print("  argues for stopping T-3 rather than scaling it.")
        else:
            print("  No clear trend and few sign changes: the band sits near")
            print("  zero throughout. That is consistent with a small real")
            print("  edge that no sample here can resolve, and it does not")
            print("  support acting on either run's headline number.")
    print()
    print("  Still a quote result -- the ask from candles, no depth check.")
    print("=" * 110)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

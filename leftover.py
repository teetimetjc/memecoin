"""Is the T-3 edge in the markets T-9 takes, or in the ones it leaves?

THE WORRY, STATED PLAINLY. The T-3 rule looked roughly ten times better than
the frozen spec's own band did at T-9: +3.29% and +3.13% net ROI across both
chronological halves, against +0.05% and +0.26%. A later re-pull put T-3 at
+2.60%/+2.27%, so the estimate is drifting down, but the ordering has held
every time it has been measured.

And the live T-3 session is not allowed to bet those markets. It yields to
the frozen session (FAVLIVE_SKIP_HELD), which enters six minutes earlier and
therefore gets first pick. So the live T-3 test is betting the LEFTOVERS:
markets that were not in the 90-96c band at T-9 and drifted into 90-94c
during the following six minutes.

THAT IS A DIFFERENT COHORT FROM THE ONE THE BACKTEST SCORED, and nobody has
ever measured it. The backtest took every in-band market at T-3, including
all the ones T-9 now takes first. If the edge lives mostly in markets that
were ALREADY favourites at T-9, then yielding hands away the entire finding
and the live T-3 session is testing something close to nothing. If the edge
lives in the late drifters, yielding costs nothing and the arrangement is
fine. Those two possibilities have opposite consequences and the record
cannot currently tell them apart.

HOW THE SPLIT IS MADE. For each settled market, read the yes ask at T-9 and
at T-3 from the same per-minute candles. Among markets in the T-3 band:

    CONTESTED -- also in the T-9 band (0.90-0.96) six minutes earlier, so
                 the frozen session would have seen it and, bar its cap,
                 taken it. These are the markets T-3 is forbidden.

    LEFTOVER  -- NOT in the T-9 band then. These are what the live T-3
                 session actually gets, and the only cohort its results can
                 speak about.

Both are scored by the same function, chronologically split, fee charged, and
a cohort counts only if it is positive in BOTH halves.

THE ONE THING THIS CANNOT MODEL, said before the numbers rather than after.
The frozen session takes at most TWO bets per window, so on a busy window it
does not actually take every contested market -- some fall through to T-3 by
luck of ordering. CONTESTED is therefore the UPPER BOUND on what yielding
costs, not the exact figure. It is the right bound for the question being
asked: if even the upper bound is small, the worry is settled.

Entry is the ask, so this is a quote result like every backtest here. It
says what the cohorts were priced at, not what could be filled at T-3, where
the book is thinner.

Read-only. Places nothing.
"""
import statistics as st
import sys
import time

import backfill as B

T9_OFF, T3_OFF = 9, 3
T9_BAND = (0.90, 0.96)           # the frozen spec's live band
T3_BAND = (0.90, 0.94)           # the T-3 session's live band
PER_SERIES = 1200
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2700
SERIES = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")


def asks(cs, close_ts):
    """{offset: yes_ask} for the two offsets, from the same candle set.

    Both sides of the book must be present and ordered before a candle is
    trusted; an ask with no bid is not a price anything traded at. Taking
    both offsets from one candle set keeps the comparison strictly
    within-market: same market, same history, two moments.
    """
    out = {}
    for off in (T9_OFF, T3_OFF):
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
        if best:
            out[off] = best[1]
    return out


def score(group, minimum=30):
    """(n, won%, priced%, net ROI%) for a list of (price, won), or None.

    Break-even is the group's OWN mean cost per contract, because a winning
    contract pays exactly $1.00 -- not 50%, and not the whole sample's mean.
    """
    if len(group) < minimum:
        return None
    n = len(group)
    w = 100.0 * sum(1 for _p, won in group if won) / n
    p = 100.0 * st.mean([p for p, _ in group])
    fee = st.mean([100.0 * 0.07 * px * (1 - px) for px, _ in group])
    cost = p + fee
    return n, w, p, 100.0 * (w - cost) / cost


def line(label, rows, mid):
    """One cohort, whole and split. Returns the two half ROIs."""
    whole = score([(p, won) for p, won, _t in rows])
    if whole is None:
        print(f"  {label:<26} {len(rows):>5} markets   too few to score")
        return None, None
    o = score([(p, won) for p, won, _t in rows if _t < mid], minimum=15)
    nw = score([(p, won) for p, won, _t in rows if _t >= mid], minimum=15)
    n, w, p, roi = whole
    if o is None or nw is None:
        print(f"  {label:<26} {n:>5} {w:>7.2f}% {p:>8.2f}% {roi:>+8.2f}%"
              "      too thin to split")
        return None, None
    if o[3] > 0 and nw[3] > 0:
        verd = "HOLDS IN BOTH"
    elif o[3] <= 0 and nw[3] <= 0:
        verd = "negative in both"
    else:
        verd = "FLIPS -- noise"
    print(f"  {label:<26} {n:>5} {w:>7.2f}% {p:>8.2f}% {roi:>+8.2f}%   "
          f"{o[3]:>+7.2f}% {nw[3]:>+7.2f}%  {verd}")
    return o[3], nw[3]


def main():
    print("=" * 110)
    print("DOES THE T-3 EDGE SURVIVE ON THE MARKETS T-9 LEAVES BEHIND?")
    print(f"  T-9 band {T9_BAND[0]:.2f}-{T9_BAND[1]:.2f} at T-{T9_OFF}   "
          f"T-3 band {T3_BAND[0]:.2f}-{T3_BAND[1]:.2f} at T-{T3_OFF}")
    print("=" * 110)
    started = time.time()
    rows = []
    for ser in SERIES:
        if time.time() - started > BUDGET:
            print(f"  [budget reached before {ser}]")
            break
        try:
            mk = B.settled_markets(ser, PER_SERIES)
        except Exception as e:
            print(f"  {ser}: could not list markets ({str(e)[:50]})")
            continue
        got = 0
        for m in mk:
            if time.time() - started > BUDGET:
                break
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            close = B.ts(m.get("close_time"))
            if close is None:
                continue
            cs = B.candles(ser, m.get("ticker"), m.get("close_time"))
            time.sleep(B.PAUSE)
            q = asks(cs, close)
            # BOTH offsets are required. A market readable at T-3 but not at
            # T-9 cannot be assigned to a cohort, and guessing which side it
            # belongs to is exactly the inference this file exists to avoid.
            if T9_OFF in q and T3_OFF in q:
                rows.append((q[T9_OFF], q[T3_OFF], res == "yes", close))
                got += 1
        print(f"  {ser:<12} {got:>5} markets readable at both offsets")

    print(f"\n  {len(rows)} markets with a quote at T-{T9_OFF} AND T-{T3_OFF}\n")
    if len(rows) < 300:
        print("  TOO FEW to split a cohort in half. Nothing claimed.")
        return 0

    rows.sort(key=lambda r: r[3])
    mid = rows[len(rows) // 2][3]

    in_t3 = [r for r in rows if T3_BAND[0] <= r[1] < T3_BAND[1]]
    contested = [(r[1], r[2], r[3]) for r in in_t3
                 if T9_BAND[0] <= r[0] < T9_BAND[1]]
    leftover = [(r[1], r[2], r[3]) for r in in_t3
                if not (T9_BAND[0] <= r[0] < T9_BAND[1])]

    print(f"  {len(in_t3)} markets in the T-3 band, of which:")
    print(f"    {len(contested):>5} CONTESTED -- also in the T-9 band six "
          "minutes earlier (T-3 is forbidden these)")
    print(f"    {len(leftover):>5} LEFTOVER  -- not in the T-9 band then "
          "(all the live T-3 session can bet)")
    if in_t3:
        print(f"    so yielding withholds at most "
              f"{100.0 * len(contested) / len(in_t3):.1f}% of the "
              "opportunities the backtest scored")
    print()

    print("-" * 110)
    print(f"  {'cohort':<26} {'n':>5} {'won':>8} {'priced':>9} "
          f"{'net ROI':>9}   {'older':>8} {'newer':>8}  verdict")
    print("-" * 110)
    line("ALL in the T-3 band", [(r[1], r[2], r[3]) for r in in_t3], mid)
    print()
    ca, cb = line("CONTESTED (T-9 takes)", contested, mid)
    la, lb = line("LEFTOVER (T-3 gets)", leftover, mid)
    print()

    print("=" * 110)
    print("  WHAT THIS DECIDES")
    print()
    if la is not None and lb is not None and la > 0 and lb > 0:
        print("  LEFTOVER holds in both halves. The arrangement is not")
        print("  costing the edge: the late drifters carry it on their own,")
        print("  and the live T-3 session is testing something real.")
    elif la is not None and lb is not None:
        print("  LEFTOVER does NOT hold in both halves. The live T-3 session")
        print("  is betting a cohort with no demonstrated edge, which means")
        print("  its results cannot confirm or refute the T-3 finding -- and")
        print("  a bad record from it would be uninterpretable rather than")
        print("  informative. That is an argument about what T-3 is TESTING,")
        print("  not yet a reason to let it contest T-9's markets.")
    else:
        print("  LEFTOVER is too thin to split. That is itself the answer to")
        print("  the practical question: if the cohort the live session can")
        print("  bet is this rare, the session cannot reach a decidable")
        print("  sample in any reasonable time.")
    print()
    print("  AND IF CONTESTED IS WHERE THE EDGE LIVES, the choice is not")
    print("  free. Letting T-3 take those markets means holding one market")
    print("  twice, at two prices, under two order ids: exposure doubles on")
    print("  a single outcome, and tally splits one settlement across both")
    print("  bets so NEITHER strategy's record describes what it did. The")
    print("  alternative worth considering is moving the frozen session's")
    print("  entry, or running T-3 on series T-9 does not trade -- not")
    print("  stacking two bots on one contract.")
    print()
    print("  CONTESTED IS AN UPPER BOUND. The frozen session takes at most")
    print("  two bets a window, so on busy windows some contested markets")
    print("  already fall through to T-3 untouched.")
    print("=" * 110)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

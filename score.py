"""Score the two frozen specs on forward data, and nothing else.

Reads `specs/expensive_favourite.md` and `specs/narrow_slice.md` as they were
written on 2026-10-05 and applies them to markets in `M15` that closed AFTER
that freeze. Takes no arguments and exposes no thresholds, because a
pre-registered test whose operator can adjust it is not one -- that property
is what made the fallen favourite's failure mean something instead of
starting an argument.

Running it early is safe and expected: it reports NOT ENOUGH DATA YET with
the count so far, which is a progress bar, not a result.

THE TWO RULES, AS FROZEN.

  expensive favourite  five majors, either side, 0.80 <= price < 0.96 at
                       T-9, held to settlement. Needs 1,200 bets.
  narrow slice         same five, YES only, 0.90 <= price < 0.96 at T-9,
                       held to settlement. Needs 500 bets.

Both are $10 flat with `contracts = int(10/price)` and the verified fee
0.07*C*P*(1-P) unrounded, charged on entry only -- a winner settles at $1.00
with nothing left to sell.

FOUR CONDITIONS EACH, ALL DECLARED BEFORE THE DATA EXISTED: the sample size
above, profit per bet above zero with clustered t > 2.5, still positive after
deleting the two best close-times AND the two worst, and a win rate above its
own break-even line at the price actually paid. The t bar is 2.5 rather than
2.0 because both rules were mined from a search rather than reasoned from a
mechanism.

WHY THE TWO ARE SCORED TOGETHER. The slice is a subset of the wider rule, so
reporting them side by side is the whole point: both passing is a
volume-versus-margin choice, only the wider one passing means the slice was
noise inside it, and only the slice passing means the edge sits at the
expensive end and the wider rule dilutes it.

IT ALSO PRINTS THE FULL ACCOUNTING for each rule -- bets, dollars risked, win
rate against its own break-even line, and profit -- separately for the
history the rule was FOUND in and the forward data since the freeze. The two
are never added together. A rule's performance on the data that selected it
is not evidence, and the moment the two are summed into one number that
distinction is gone for good.

Read-only. Scores paper bets at $10; places nothing.
"""

import calendar
import collections
import math
import statistics as st
import sys
import time

import predictor as P

TAB = "M15"
HIST = "M15H"
FREEZE = "2026-10-05T00:00:00Z"
HIST_SPLIT = 0.70        # the cut the rules were discovered across
MAJORS = ("KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M")
ENTRY = 9
STAKE = 10.0
T_BAR = 2.5

RULES = (
    dict(name="expensive favourite", spec="specs/expensive_favourite.md",
         band=(0.80, 0.96), sides=("YES", "NO"), need_n=1200),
    dict(name="narrow slice", spec="specs/narrow_slice.md",
         band=(0.90, 0.96), sides=("YES",), need_n=500),
)


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def ts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19],
                                            "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def fee(c, p):
    return 0.07 * c * p * (1 - p)


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    cut = ts(FREEZE)
    out, skipped = [], 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c("Close Time"))
        if t is None:
            continue
        if t <= cut:
            skipped += 1          # discovery-era market; cannot confirm itself
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ct=str(c("Close Time")), bid=b, ask=a,
                        yes=(res == "yes")))
    print(f"  {TAB}: {len(out)} graded majors after the freeze "
          f"({skipped} before it, skipped)")
    return out


def load_hist():
    """The backfilled history: the data these rules were FOUND in."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(HIST).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c("Close Time"))
        if t is None:
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ct=str(c("Close Time")), t=t, bid=b, ask=a,
                        yes=(res == "yes")))
    print(f"  {HIST}: {len(out)} graded majors with a T-{ENTRY} quote")
    return out


def tally(bs, label, indent="    "):
    """bets, risked, win rate against break-even, profit."""
    n = len(bs)
    if not n:
        print(f"{indent}{label:<26} no bets")
        return
    risked = STAKE * n
    profit = sum(b[0] for b in bs)
    win = sum(1 for b in bs if b[3]) / n
    px = st.mean([b[2] for b in bs])
    c = int(STAKE / px)
    need = (c * px + fee(c, px)) / c
    roi = 100.0 * profit / risked
    print(f"{indent}{label:<26} {n:>6} bets  ${risked:>10,.0f} risked  "
          f"won {100*win:>5.1f}% vs {100*need:>5.1f}% needed  "
          f"${profit:>+10,.2f}  {roi:>+6.2f}%")


def bets(M, band, sides):
    lo, hi = band
    out = []
    for m in M:
        for side in sides:
            px = m["ask"] if side == "YES" else (1.0 - m["bid"])
            if not (lo <= px < hi):
                continue
            c = int(STAKE / px)
            if c < 1:
                continue
            won = m["yes"] if side == "YES" else (not m["yes"])
            v = (c if won else 0) - (c * px + fee(c, px))
            out.append((v, m["ct"], px, won))
    return out


def report(rule, M):
    print("\n" + "=" * 92)
    print(f"{rule['name'].upper()}   ({rule['spec']})")
    print("=" * 92)
    bs = bets(M, rule["band"], rule["sides"])
    n = len(bs)
    cts = {b[1] for b in bs}
    print(f"  qualifying bets {n} of {rule['need_n']} needed"
          f"   close-times {len(cts)}")
    if n < rule["need_n"]:
        pct = 100.0 * n / rule["need_n"]
        print(f"  NOT ENOUGH DATA YET ({pct:.0f}% of the way). This is a")
        print("  progress bar, not a result, and not a failure.")
        if n >= 40:
            m = sum(b[0] for b in bs) / n
            print(f"  (running, for information only: ${m:+.3f}/bet -- "
                  "ignore it, the sample is short)")
        return None

    g = collections.defaultdict(list)
    for v, ct, _, _ in bs:
        g[ct].append(v)
    mean = sum(b[0] for b in bs) / n
    se = cse(g, n)
    t = (mean / se) if se else 0.0
    win = sum(1 for b in bs if b[3]) / n
    px = st.mean([b[2] for b in bs])
    c = int(STAKE / px)
    need_win = (c * px + fee(c, px)) / c
    d = collections.defaultdict(float)
    for v, ct, _, _ in bs:
        d[ct] += v
    by = sorted(d.values(), reverse=True)
    minus_best = sum(by[2:])
    minus_worst = sum(by[:-2])

    print(f"  paid            {100*px:.1f}c")
    print(f"  won             {100*win:.1f}%   break-even {100*need_win:.1f}%")
    print(f"  profit per bet  ${mean:+.3f} +/- {se or 0:.3f}   t = {t:+.2f}")
    print(f"  total           ${mean*n:+,.2f} on ${10*n:,} staked")
    print(f"  minus two best close-times   ${minus_best:+,.2f}")
    print(f"  minus two worst close-times  ${minus_worst:+,.2f}")

    checks = [
        (f"at least {rule['need_n']} bets", n >= rule["need_n"]),
        ("profit per bet above zero", mean > 0),
        (f"clustered t above {T_BAR}", t > T_BAR),
        ("positive without the two best close-times", minus_best > 0),
        ("positive without the two worst close-times", minus_worst > 0),
        ("win rate above its break-even line", win > need_win),
    ]
    print()
    for label, ok in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {label}")
    passed = all(ok for _, ok in checks)
    print(f"\n  VERDICT: {'PASSES' if passed else 'FAILS'} "
          "the pre-registered conditions")
    if not passed:
        print("  A fail is final for this spec. Re-cutting the thresholds")
        print("  would make this discovery data rather than a test.")
    return passed


def gap_window(H, M):
    """Live data the SEARCH never saw, but which predates the freeze.

    The rules were found in M15H, which ends at its own last close time. The
    live M15 tab kept collecting past that, so the span between the two is
    genuinely out of sample -- no search touched it -- yet it sits before the
    freeze and is therefore NOT part of the pre-registered test.

    It is reported separately and labelled, because the decision to look at
    it was made AFTER seeing a favourable first 69 bets. The data is clean;
    the choice to examine it is not blind. Treating it as part of the frozen
    test would be moving the goalposts, which is the one thing the freeze
    exists to prevent. Treating it as nothing would be throwing away four
    days of honest evidence. So: shown, caveated, and kept out of the
    verdict.
    """
    if not H:
        return
    hmax = max(m["t"] for m in H)
    cut = ts(FREEZE)
    if hmax >= cut:
        return
    gap = [m for m in M if False]      # M is already post-freeze only
    print("\n" + "=" * 118)
    print("THE GAP WINDOW -- live data the search never saw, before the freeze")
    print("=" * 118)
    print(f"  history ends {time.strftime('%Y-%m-%d %H:%M', time.gmtime(hmax))}"
          f" UTC, freeze at {FREEZE}")
    print(f"  that is {(cut-hmax)/86400.0:.1f} days of M15 rows that no search")
    print("  touched. Scored below, and NOT part of either frozen test: the")
    print("  decision to look came after seeing the first 69 forward bets.")
    return hmax


def load_gap(hmax):
    """M15 markets closing after the history ends but before the freeze."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    cut = ts(FREEZE)
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        if str(c("Series")) not in MAJORS:
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c("Close Time"))
        if t is None or not (hmax < t <= cut):
            continue
        b, a = _f(c(f"bid{ENTRY}")), _f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ct=str(c("Close Time")), t=t, bid=b, ask=a,
                        yes=(res == "yes")))
    return out


def accounting(H, M):
    """Everything risked and won, history and forward kept apart."""
    cut = ts(FREEZE)
    times = sorted({m["t"] for m in H})
    hcut = times[int(HIST_SPLIT * len(times))] if times else 0
    print("\n" + "=" * 118)
    print("FULL ACCOUNTING -- paper money at $10 a bet")
    print("=" * 118)
    print("  History and forward are NEVER added together: a rule's record on")
    print("  the data that selected it is not evidence of anything.\n")
    for r in RULES:
        print(f"  {r['name'].upper()}  "
              f"({100*r['band'][0]:.0f}-{100*r['band'][1]:.0f}c, "
              f"{'/'.join(r['sides'])}, T-{ENTRY})")
        hb = bets([m for m in H if m["t"] < hcut], r["band"], r["sides"])
        ho = bets([m for m in H if m["t"] >= hcut], r["band"], r["sides"])
        tally(hb, "history, discovery 70%")
        tally(ho, "history, holdout 30%")
        tally(hb + ho, "history, combined")
        fb = bets(M, r["band"], r["sides"])
        tally(fb, "FORWARD since freeze")
        if len(fb) < r["need_n"]:
            print(f"      ^ {len(fb)} of {r['need_n']} needed -- too short to "
                  "mean anything either way")
        print()


def main():
    M = load()
    H = load_hist()
    if H:
        accounting(H, M)
        hmax = gap_window(H, M)
        if hmax:
            G = load_gap(hmax)
            print()
            for r in RULES:
                tally(bets(G, r["band"], r["sides"]),
                      r["name"], indent="  ")
            print("\n  Unblinded window: informative, not evidence. The")
            print("  verdict below still rests only on post-freeze data.")
    if not M:
        print("no graded majors after the freeze yet")
        return 0
    res = [report(r, M) for r in RULES]
    if all(x is not None for x in res):
        print("\n" + "=" * 92)
        wide, slim = res
        if wide and slim:
            print("  BOTH PASS -- the choice between them is volume against")
            print("  margin, and neither is tradeable until slippage at the")
            print("  intended stake has been measured from book depth.")
        elif wide and not slim:
            print("  Only the wider rule passes: the slice was noise inside it.")
        elif slim and not wide:
            print("  Only the slice passes: the edge sits at the expensive end")
            print("  and the wider band dilutes it.")
        else:
            print("  NEITHER PASSES. Both specs are closed.")
        print("=" * 92)
    return 0


if __name__ == "__main__":
    sys.exit(main())

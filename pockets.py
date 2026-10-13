"""Where does YES beat NO, and where does NO beat YES?

THE QUESTION. The bot now bets whichever side reaches 90-96c at T-9. That
choice is made by the market, not by us. What is still open is whether there
are POCKETS -- times of day, days of week, coins, volatility regimes -- where
one side reliably pays and the other reliably does not, so that one could be
favoured and the other sat out.

WHY THIS IS KALSHI AND NOT POLYMARKET. The months-long Polymarket history
that showed the rule losing 04-01..07-18 and winning 07-19..10-07 lives in
the other project's workbook, not here; poly_probe.py in this repo only does
live reconnaissance. Kalshi's own M15 sheet holds ~7,000 graded majors, which
cannot speak to months-long seasons but can speak to intraday and weekday
pockets with far more bets than the forward tests have.

THIS IS EXPLORATORY AND IT IS NOT A TEST. It deliberately uses the WHOLE
history including the pre-freeze period, because the point is to find
candidates rather than to confirm anything, and a candidate found in data the
rule was mined from is worth exactly one thing: a forward test. Every frozen
spec in this project exists because something looked good here first.

HOW MANY CELLS. Six time blocks, seven weekdays, five coins and three
volatility regimes, each split two ways by side -- comfortably over forty
comparisons. At the usual bar roughly two will clear t=2 by luck alone. So
the output flags only cells with BOTH |t| > 2.5 AND at least 150 bets, prints
every cell regardless so the shape is visible, and says plainly that a flag
is a candidate and not a finding.

WHAT WOULD MAKE A POCKET REAL: it survives a forward test it was not chosen
from, and it has a mechanism anyone can state in a sentence. "Asian hours are
thinner" is a mechanism. "Tuesdays are good" is not.

Read-only: reads the M15 sheet, writes nothing, places nothing.
"""

import collections
import statistics as st
import sys
import time
import calendar

import predictor as P
import score as S


TAB = S.TAB
MAJORS = set(S.MAJORS)
STAKE = S.STAKE
ENTRY = S.ENTRY
BAND = (0.90, 0.96)
MIN_N = 150
T_BAR = 2.5
# Four-hour blocks, labelled with the Chicago hours the operator thinks in.
# CDT is UTC-5 through 2026-11-01.
BLOCKS = [(0, 4, "00-04 UTC  (7pm-11pm CT)"),
          (4, 8, "04-08 UTC  (11pm-3am CT)"),
          (8, 12, "08-12 UTC  (3am-7am CT)"),
          (12, 16, "12-16 UTC  (7am-11am CT)"),
          (16, 20, "16-20 UTC  (11am-3pm CT)"),
          (20, 24, "20-24 UTC  (3pm-7pm CT)")]


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        ser = str(c(r, "Series"))
        if ser not in MAJORS:
            continue
        res = str(c(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = S.ts(c(r, "Close Time"))
        if t is None:
            continue
        b, a = S._f(c(r, f"bid{ENTRY}")), S._f(c(r, f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        tm = time.gmtime(t)
        out.append(dict(ct=str(c(r, "Close Time")), series=ser, bid=b, ask=a,
                        yes=(res == "yes"), hour=tm.tm_hour,
                        dow=calendar.day_name[tm.tm_wday]))
    print(f"  {TAB}: {len(out):,} graded majors with a two-sided T-{ENTRY} "
          "quote (whole history, pre-freeze included)")
    return out


def regime(M):
    """Leave-one-out decisiveness, as condno.py defines it."""
    win = collections.defaultdict(list)
    for m in M:
        win[m["ct"]].append(m)
    kept = []
    for ms in win.values():
        if len(ms) < 4:
            continue
        for m in ms:
            peers = [abs(100.0 * x["ask"] - 50.0) for x in ms if x is not m]
            m["reg"] = st.median(peers)
            kept.append(m)
    return kept


def bets(M, side):
    lo, hi = BAND
    out = []
    for m in M:
        px = m["ask"] if side == "YES" else (1.0 - m["bid"])
        if not (lo <= px < hi):
            continue
        n = int(STAKE / px)
        if n < 1:
            continue
        won = m["yes"] if side == "YES" else (not m["yes"])
        v = (n if won else 0) - (n * px + S.fee(n, px))
        out.append(dict(v=v, ct=m["ct"], px=px, won=won, m=m))
    return out


def stat(rows):
    n = len(rows)
    if not n:
        return None
    per = sum(r["v"] for r in rows) / n
    win = 100.0 * sum(1 for r in rows if r["won"]) / n
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["v"])
    se = S.cse(g, n)
    return dict(n=n, per=per, win=win, t=(per / se) if se else 0.0)


def show(title, groups):
    """groups: ordered list of (label, yes_rows, no_rows)."""
    print("\n" + "=" * 100)
    print(f"  {title}")
    print("=" * 100)
    print(f"  {'pocket':<28} {'YES n':>6} {'won':>6} {'$/bet':>8} {'t':>6}   "
          f"{'NO n':>6} {'won':>6} {'$/bet':>8} {'t':>6}   {'favour':>8}")
    flags = []
    for label, ys, ns in groups:
        y, nn = stat(ys), stat(ns)

        def fmt(d):
            if not d:
                return f"{'-':>6} {'-':>6} {'-':>8} {'-':>6}"
            return (f"{d['n']:>6,} {d['win']:>5.1f}% {d['per']:>+8.3f} "
                    f"{d['t']:>+6.2f}")
        fav = ""
        if y and nn and y["n"] >= 50 and nn["n"] >= 50:
            fav = "YES" if y["per"] > nn["per"] else "NO"
        print(f"  {label:<28} {fmt(y)}   {fmt(nn)}   {fav:>8}")
        for side, d in (("YES", y), ("NO", nn)):
            if d and d["n"] >= MIN_N and abs(d["t"]) > T_BAR:
                flags.append((label, side, d))
    return flags


def main():
    M = load()
    M = regime(M)
    print(f"  {len(M):,} scored for volatility regime\n")

    Y = bets(M, "YES")
    N = bets(M, "NO")
    print(f"  in-band bets: YES {len(Y):,}   NO {len(N):,}")
    allflags = []

    def split(key):
        return (lambda rows, v: [r for r in rows if r["m"][key] == v])

    # overall
    allflags += show("OVERALL", [("all history", Y, N)])

    # time of day
    g = []
    for lo, hi, lab in BLOCKS:
        g.append((lab,
                  [r for r in Y if lo <= r["m"]["hour"] < hi],
                  [r for r in N if lo <= r["m"]["hour"] < hi]))
    allflags += show("BY TIME OF DAY (market close)", g)

    # weekday
    order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday"]
    allflags += show("BY WEEKDAY", [
        (d, [r for r in Y if r["m"]["dow"] == d],
         [r for r in N if r["m"]["dow"] == d]) for d in order])

    # coin
    allflags += show("BY COIN", [
        (s, [r for r in Y if r["m"]["series"] == s],
         [r for r in N if r["m"]["series"] == s])
        for s in sorted(MAJORS)])

    # volatility regime
    reg = sorted(m["reg"] for m in M)
    q1, q2 = reg[len(reg) // 3], reg[2 * len(reg) // 3]
    tiers = [(f"CHOPPY  (<{q1:.0f}c)", lambda x: x < q1),
             (f"MIDDLE  ({q1:.0f}-{q2:.0f}c)", lambda x: q1 <= x < q2),
             (f"DECISIVE (>={q2:.0f}c)", lambda x: x >= q2)]
    allflags += show("BY VOLATILITY REGIME (peers' distance from 50c)", [
        (lab, [r for r in Y if f(r["m"]["reg"])],
         [r for r in N if f(r["m"]["reg"])]) for lab, f in tiers])

    print("\n" + "=" * 100)
    print("  CANDIDATES (|t| > 2.5 AND at least 150 bets)")
    print("=" * 100)
    if not allflags:
        print("  None. No pocket separates on this data at that bar, which is")
        print("  the most common and least exciting outcome, and it is still")
        print("  an answer: there is nothing here worth forward-testing.")
    else:
        for lab, side, d in allflags:
            print(f"  {side:<4} {lab:<30} {d['n']:>6,} bets  "
                  f"won {d['win']:.1f}%  ${d['per']:+.3f}/bet  t={d['t']:+.2f}")
        print()
        print("  A CANDIDATE IS NOT A FINDING. Forty-plus cells were examined")
        print("  on the same history the rule was mined from, so about two")
        print("  should clear this bar by luck. Before any of these changes a")
        print("  live setting it needs a mechanism stated in one sentence and")
        print("  a forward test it was not chosen from -- which is exactly how")
        print("  the narrow slice and the T-6 spec came to exist.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

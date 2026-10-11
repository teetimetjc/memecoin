"""Scores specs/t6_slice.md: is T-6 actually better than T-9?

The live rule enters nine minutes before a window closes. Across 37,040
historical markets, entering at six looked better -- more bets, a better
return per dollar, a far stronger t. But T-9 was itself chosen by searching
that same history, so finding T-6 in it is the same search running twice.
This scores both forward, from the freeze instant in the spec.

THE TEST IS THE HEAD-TO-HEAD, NOT EITHER COLUMN ALONE. If the next few weeks
happen to suit the rule, both offsets rise together and a single number
would read as proof of something it has not shown. The two are therefore
scored over the SAME window, and the pass condition is that T-6 beats T-9 --
not merely that T-6 makes money.

EVERY THRESHOLD IS A CONSTANT HERE AND A LINE IN THE SPEC. No arguments, no
environment switches: a pre-registered test whose operator can adjust it on
seeing the result is not one.

Read-only.
"""

import calendar
import collections
import math
import sys
import time

import predictor as P
import score as S


TAB = "M15"
FREEZE = "2026-10-11T00:15:00Z"
BAND = (0.90, 0.96)
LIVE_OFF = 9
TEST_OFF = 6
STAKE = 4.0
MAJORS = set(S.MAJORS)
# Spec pass conditions, fixed 2026-10-11.
NEED_N = 500
T_BAR = 2.5


def _f(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except Exception:
        return None


def ts(s):
    try:
        return calendar.timegm(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None


def fee(n, p):
    if n <= 0 or not (0 < p < 1):
        return 0.0
    return math.ceil(0.07 * n * p * (1 - p) * 100) / 100.0


def load():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    if len(rows) < 2:
        return [], 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    cut = ts(FREEZE)
    out, before = [], 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        if str(c(r, "Series")) not in MAJORS:
            continue
        res = str(c(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = ts(c(r, "Close Time"))
        if t is None:
            continue
        if t <= cut:
            # A market that closed before the freeze cannot confirm a rule
            # chosen by looking at markets that closed before the freeze.
            before += 1
            continue
        q = {}
        for off in (LIVE_OFF, TEST_OFF):
            b, a = _f(c(r, f"bid{off}")), _f(c(r, f"ask{off}"))
            q[off] = a if (b is not None and a is not None
                           and 0 < b <= a < 1) else None
        out.append(dict(ct=str(c(r, "Close Time")), q=q, yes=(res == "yes")))
    return out, before


def arm(rows, off):
    out = []
    for m in rows:
        px = m["q"].get(off)
        if px is None or not (BAND[0] <= px < BAND[1]):
            continue
        n = int(STAKE // px)
        if n <= 0:
            continue
        cost = n * px + fee(n, px)
        out.append(dict(ct=m["ct"], px=px, cost=cost, won=m["yes"],
                        pnl=(n * 1.0 - cost) if m["yes"] else -cost))
    return out


def clustered_t(rows):
    n = len(rows)
    if n < 3:
        return 0.0
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ct"]].append(r["pnl"])
    G = len(g)
    if G < 3:
        return 0.0
    m = sum(r["pnl"] for r in rows) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    se = math.sqrt((G / (G - 1.0)) * ss / (n * n))
    return (m / se) if se > 0 else 0.0


def trim(rows):
    g = collections.defaultdict(float)
    for r in rows:
        g[r["ct"]] += r["pnl"]
    order = sorted(g, key=lambda k: g[k])
    bad = set(order[:2]) | set(order[-2:])
    return [r for r in rows if r["ct"] not in bad]


def summarise(label, rows):
    n = len(rows)
    if not n:
        print(f"  {label:<22} no qualifying bets yet")
        return None
    won = sum(1 for r in rows if r["won"])
    cost = sum(r["cost"] for r in rows)
    pnl = sum(r["pnl"] for r in rows)
    per = 100 * pnl / cost
    print(f"  {label:<22} {n:>5} bets  won {100*won/n:>5.1f}%  "
          f"net ${pnl:>+8.2f}  {per:>+6.2f}%  ${pnl/n:>+6.3f}/bet  "
          f"t = {clustered_t(rows):+.2f}")
    return dict(n=n, pnl=pnl, cost=cost, per=per, t=clustered_t(rows))


def main():
    print("=" * 90)
    print("T-6 versus T-9, FORWARD ONLY   specs/t6_slice.md, frozen "
          + FREEZE)
    print(f"  {100*BAND[0]:.0f}-{100*BAND[1]:.0f}c YES on the five majors, "
          f"${STAKE:.0f} a bet, held to settlement")
    print("=" * 90)

    rows, before = load()
    print(f"  {len(rows)} graded majors closing after the freeze "
          f"({before} before it, skipped)\n")

    a9 = summarise(f"T-{LIVE_OFF} (live rule)", arm(rows, LIVE_OFF))
    a6 = summarise(f"T-{TEST_OFF} (this spec)", arm(rows, TEST_OFF))

    print("\n" + "=" * 90)
    if not a6 or a6["n"] < NEED_N:
        have = a6["n"] if a6 else 0
        print(f"  NOT ENOUGH DATA YET: {have} of {NEED_N} bets "
              f"({100*have/NEED_N:.0f}% of the way).")
        print("  A progress bar, not a result, and not a failure.")
        if a6 and a9:
            print(f"  (running, for information only: T-{TEST_OFF} "
                  f"{a6['per']:+.2f}% vs T-{LIVE_OFF} {a9['per']:+.2f}% "
                  "-- ignore it, the sample is short)")
        return 0

    tr = trim(arm(rows, TEST_OFF))
    tr_pnl = sum(r["pnl"] for r in tr)
    tr_t = clustered_t(tr)
    gap = a6["per"] - (a9["per"] if a9 else 0.0)
    checks = [
        (f"at least {NEED_N} bets", a6["n"] >= NEED_N, str(a6["n"])),
        ("net above zero", a6["pnl"] > 0, f"${a6['pnl']:+.2f}"),
        (f"clustered t above {T_BAR}", a6["t"] > T_BAR, f"{a6['t']:+.2f}"),
        (f"beats T-{LIVE_OFF} per dollar", gap > 0, f"{gap:+.2f}pt"),
        ("survives trimming", tr_pnl > 0 and tr_t > T_BAR,
         f"${tr_pnl:+.2f}, t={tr_t:+.2f}"),
    ]
    for name, ok, val in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}]  {name:<26} {val}")
    print()
    if all(ok for _, ok, _ in checks):
        print(f"  VERDICT: PASSES. Moving the live entry to T-{TEST_OFF} is "
              "justified --")
        print("  and nothing else is. Watch the first week's FILLS against "
              "these")
        print("  quoted figures before trusting it at size.")
    else:
        print(f"  VERDICT: FAILS. The live rule stays at T-{LIVE_OFF}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

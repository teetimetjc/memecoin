"""Score the forward replication spec, and nothing else.

Reads `specs/replication.md` as frozen on 2026-10-08 and applies both rules to
NON-MAJOR fifteen-minute markets closing after the freeze. Takes no arguments
and exposes no thresholds, for the same reason score.py does not: a
pre-registered test whose operator can adjust it is not one.

PAPER ONLY. Nothing here places an order and nothing in favlive reads this.
The spec forbids betting these markets regardless of the result, because
mirror.py measured the round trip there at about 25% of stake.

Running it early is expected and safe: it reports the count so far and says
NOT ENOUGH DATA YET, which is a progress bar rather than a verdict.
"""

import collections
import math
import statistics as st
import sys
import time

import predictor as P
import score as S

TAB = "M15"
FREEZE = "2026-10-08T06:00:00Z"
ENTRY = S.ENTRY
STAKE = 4.0
T_BAR = 2.5

RULES = (
    dict(name="narrow slice", band=(0.90, 0.96), sides=("YES",), need_n=500),
    dict(name="expensive favourite", band=(0.80, 0.96), sides=("YES", "NO"),
         need_n=1200),
)


def load():
    """Graded NON-MAJOR fifteen-minute rows closing after the freeze."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    cut = S.ts(FREEZE)
    out, before, major = [], 0, 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue

        def c(k):
            i = h.get(k)
            return r[i] if i is not None and i < len(r) else ""

        ser = str(c("Series"))
        if not ser.endswith("15M"):
            continue
        if ser in S.MAJORS:
            major += 1
            continue
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        t = S.ts(c("Close Time"))
        if t is None:
            continue
        if t <= cut:
            before += 1          # pre-freeze; cannot confirm itself
            continue
        b, a = S._f(c(f"bid{ENTRY}")), S._f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ser=ser, ct=str(c("Close Time")), bid=b, ask=a,
                        yes=(res == "yes")))
    print(f"  {TAB}: {len(out)} graded non-major rows after the freeze")
    print(f"        ({before} before it, {major} major rows — both skipped)")
    return out


def report(rule, M):
    print("\n" + "=" * 96)
    print(f"{rule['name'].upper()}  on non-major markets   "
          f"({100*rule['band'][0]:.0f}-{100*rule['band'][1]:.0f}c, "
          f"{'/'.join(rule['sides'])}, T-{ENTRY})")
    print("=" * 96)

    old = S.STAKE
    try:
        S.STAKE = STAKE
        bs = S.bets(M, rule["band"], rule["sides"])
    finally:
        S.STAKE = old

    n = len(bs)
    cts = {b[1] for b in bs}
    sers = {m["ser"] for m in M}
    print(f"  qualifying bets {n} of {rule['need_n']} needed   "
          f"close-times {len(cts)}   series {len(sers)}")
    if n < rule["need_n"]:
        print(f"  NOT ENOUGH DATA YET ({100.0*n/rule['need_n']:.0f}% of the "
              "way). A progress bar, not a result.")
        if n >= 40:
            m = sum(b[0] for b in bs) / n
            print(f"  (running, for information only: ${m:+.3f}/bet — ignore "
                  "it, the sample is short)")
        return None

    g = collections.defaultdict(list)
    for v, ct, _, _ in bs:
        g[ct].append(v)
    mean = sum(b[0] for b in bs) / n
    se = S.cse(g, n)
    t = (mean / se) if se else 0.0
    win = sum(1 for b in bs if b[3]) / n
    px = st.mean([b[2] for b in bs])
    c = int(STAKE / px)
    need_win = (c * px + S.fee(c, px)) / c
    d = collections.defaultdict(float)
    for v, ct, _, _ in bs:
        d[ct] += v
    by = sorted(d.values(), reverse=True)
    minus_best, minus_worst = sum(by[2:]), sum(by[:-2])

    print(f"  paid            {100*px:.1f}c")
    print(f"  won             {100*win:.1f}%   break-even {100*need_win:.1f}%")
    print(f"  profit per bet  ${mean:+.3f} +/- {se or 0:.3f}   t = {t:+.2f}")
    print(f"  total           ${mean*n:+,.2f} on ${STAKE*n:,.0f} staked")
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
    print(f"\n  VERDICT: {'REPLICATES' if passed else 'DOES NOT REPLICATE'}")
    return passed


def main():
    print("=" * 96)
    print("FORWARD REPLICATION  (specs/replication.md, frozen 2026-10-08)")
    print("=" * 96)
    print("  The frozen rules, unchanged, on fifteen-minute markets they were")
    print("  never fitted to. PAPER ONLY — the spec forbids betting these")
    print("  markets whatever the result, since the round trip there costs")
    print("  about 25% of stake.")
    print(f"  freeze: {FREEZE}\n")

    M = load()
    if not M:
        print("\n  No post-freeze non-major rows yet. Collection is already")
        print("  running — collect15 gathers every fifteen-minute series — so")
        print("  this fills on its own.")
        return 0

    res = [report(r, M) for r in RULES]
    if any(x is not None for x in res):
        print("\n" + "=" * 96)
        print("  A failure here should lower confidence in the two original")
        print("  specs even if they pass: five coins moving together is closer")
        print("  to one bet than to five.")
        print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())

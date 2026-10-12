"""Does the NO side earn its keep in a choppy market, where YES goes dormant?

THE QUESTION. On 2026-10-12 the bot sat idle for hours because nothing
reached the 90-96c YES band: at T-9 the five majors were quoting 9c, 23c,
12c, 9c, 29c and the like. But a market at 9c YES is a market at about 91c
NO -- a favourite in the band, on the side the bot does not bet. So the
dormancy is one-sided, and the question is whether those NO favourites are
worth taking in exactly the regime that starves the YES side.

WHAT IS ALREADY KNOWN, so this is not sold as a fresh idea. The NO-side
favourite has been measured once already, unconditionally, over 2,052 bets:
+0.03% at t=0.04. Zero. This asks the narrower question the earlier test
averaged away -- whether NO works specifically when the tape is chaotic.

THE REGIME MEASURE, AND WHY NOT THE OBVIOUS ONE. The first attempt at this
(volgate.py) measured how far each market's own ask had moved before T-9,
and it failed: a 15-minute market starts near 50c and converges toward 0 or
1 as the clock runs down, so every path shows a huge move and the median
"drop" came out at -37c. Price movement within one market is dominated by
that convergence, not by chop.

So the regime is read ACROSS markets at one instant instead, where the drift
cancels because every market is the same age:

    REGIME = median |ask_at_T9 - 50c| over the OTHER majors in the same
             close window.

Near zero means every other major was still a coin-flip nine minutes out --
a chaotic window. Large means they had resolved into favourites -- a decisive
window. No time axis, so nothing to drift.

LEAVE-ONE-OUT IS NOT A DETAIL. A window containing a 91c NO favourite is, by
construction, a window with at least one decisive market. Including the bet's
own market in its regime score would let the bet's own extremeness decide
which regime it lands in, which guarantees a result and means nothing. Each
market is scored by its PEERS only.

MULTIPLE COMPARISONS, declared: two sides by three terciles is six cells, and
the terciles are fixed thirds of the regime distribution rather than
thresholds anyone tuned. This is also post-hoc regime slicing prompted by one
bad day, which is the classic way to find a pattern that is not there. The
bar is therefore the frozen specs' own bar, clustered t > 2.5, and a cell
that merely looks nice is not a finding.

Read-only: reads the M15 sheet, writes nothing, places nothing.
"""

import collections
import math
import statistics as st
import sys

import predictor as P
import score as S


TAB = S.TAB
MAJORS = set(S.MAJORS)
STAKE = S.STAKE
ENTRY = S.ENTRY
BAND = (0.90, 0.96)
# A window needs this many OTHER majors quoted to score its regime.
MIN_PEERS = 3


def load():
    """Post-freeze majors with the T-9 two-sided quote, keyed by close time."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(TAB).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}
    cut = S.ts(S.FREEZE)
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
        t = S.ts(c("Close Time"))
        if t is None or t <= cut:
            continue
        b = S._f(c(f"bid{ENTRY}"))
        a = S._f(c(f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ct=str(c("Close Time")), series=str(c("Series")),
                        bid=b, ask=a, yes=(res == "yes")))
    print(f"  {TAB}: {len(out):,} graded majors after the freeze with a "
          f"two-sided T-{ENTRY} quote")
    return out


def with_regime(M):
    """Attach each market's leave-one-out regime score."""
    win = collections.defaultdict(list)
    for m in M:
        win[m["ct"]].append(m)
    kept = []
    thin = 0
    for ct, ms in win.items():
        if len(ms) - 1 < MIN_PEERS:
            thin += len(ms)
            continue
        for m in ms:
            peers = [abs(100.0 * x["ask"] - 50.0) for x in ms if x is not m]
            m["regime"] = st.median(peers)
            m["peers"] = len(peers)
            kept.append(m)
    print(f"  {len(kept):,} scored by {MIN_PEERS}+ peers in the same window "
          f"({thin:,} dropped from thin windows), "
          f"{len(win):,} windows")
    return kept


def bets(M, side):
    lo, hi = BAND
    out = []
    for m in M:
        px = m["ask"] if side == "YES" else (1.0 - m["bid"])
        if not (lo <= px < hi):
            continue
        c = int(STAKE / px)
        if c < 1:
            continue
        won = m["yes"] if side == "YES" else (not m["yes"])
        v = (c if won else 0) - (c * px + S.fee(c, px))
        out.append(dict(v=v, ct=m["ct"], px=px, won=won,
                        regime=m["regime"]))
    return out


def line(label, bs, indent="    "):
    n = len(bs)
    if not n:
        print(f"{indent}{label:<24} no bets")
        return None
    profit = sum(b["v"] for b in bs)
    per = profit / n
    win = 100.0 * sum(1 for b in bs if b["won"]) / n
    px = st.mean([b["px"] for b in bs])
    c = int(STAKE / px)
    need = 100.0 * (c * px + S.fee(c, px)) / c
    g = collections.defaultdict(list)
    for b in bs:
        g[b["ct"]].append(b["v"])
    se = S.cse(g, n)
    t = (per / se) if se else 0.0
    flag = "  <-- clears t>2.5" if (t > 2.5 and per > 0) else ""
    print(f"{indent}{label:<24} {n:>6,} bets  won {win:>5.1f}% vs "
          f"{need:>5.1f}% needed  ${per:>+7.3f}/bet  t={t:>+6.2f}{flag}")
    return dict(n=n, per=per, t=t)


def main():
    print("=" * 98)
    print("DOES THE NO SIDE PAY IN A CHOPPY MARKET?")
    print("  90-96c at T-9, split by how decisive the OTHER majors were in "
          "the same window")
    print("=" * 98)

    M = with_regime(load())
    if not M:
        print("  nothing to score.")
        return 0

    reg = sorted(m["regime"] for m in M)
    q1 = reg[len(reg) // 3]
    q2 = reg[2 * len(reg) // 3]
    print(f"\n  regime = median |ask-50c| over peers. terciles at "
          f"{q1:.1f}c and {q2:.1f}c")
    print(f"    CHOPPY   below {q1:.1f}c   (peers still near a coin-flip)")
    print(f"    MIDDLE   {q1:.1f}-{q2:.1f}c")
    print(f"    DECISIVE above {q2:.1f}c  (peers already resolved)")

    tiers = (("CHOPPY", lambda x: x < q1),
             ("MIDDLE", lambda x: q1 <= x < q2),
             ("DECISIVE", lambda x: x >= q2))

    for side in ("NO", "YES"):
        bs = bets(M, side)
        print("\n" + "-" * 98)
        print(f"{side} SIDE, {len(bs):,} bets in the 90-96c band")
        print("-" * 98)
        if not bs:
            print("  none.")
            continue
        line("all regimes", bs)
        print()
        for name, f in tiers:
            line(name, [b for b in bs if f(b["regime"])])

    print("\n" + "=" * 98)
    print("  READ THE CHOPPY ROW OF THE NO BLOCK FIRST -- that is the whole")
    print("  question. It needs t > 2.5 and a win rate above its own")
    print("  break-even to mean anything, because:")
    print()
    print("    - the unconditional NO test already returned zero over 2,052")
    print("      bets, so this is looking for something that hid inside a null")
    print("    - six cells were examined and one bad day prompted the slice")
    print()
    print("  AND EVEN A PASS DOES NOT MAKE IT TRADEABLE. live.py records the")
    print("  NO mapping -- buying NO means selling YES -- as an UNCONFIRMED")
    print("  inference, which is why ALLOW_DOWN is off. Read backwards, every")
    print("  NO order lands on the wrong side of the market. That mapping")
    print("  needs confirming against a real fill before any NO bet is sent,")
    print("  whatever this table says.")
    print("=" * 98)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Search the 70-day backfill for anything that makes money.

43,122 markets, each with a six-point price path and a real takeable quote
at every point. Three questions, in the order that matters:

  1. DOES THE PRICE-BAND RESULT REPLICATE? Three days of live data said
     buying under 20c loses 73% of stake. On nineteen times the sample and
     ten weeks of different conditions, does it hold? A finding that only
     exists in one week is not a finding.

  2. DOES THE PATH CARRY ANYTHING? A market that walked 0.42 -> 0.95 is a
     different object from one that sat at 0.65, and until this dataset
     existed nothing could tell them apart.

  3. WHERE, IF ANYWHERE, IS PROFIT POSITIVE? Reported per series and per
     hour, with the day-level concentration check applied automatically --
     because the two candidates that survived everything else both died to
     it, one carrying its whole result in two days of thirteen.

Every number is a real quote: the ask you would have paid, or 1 minus the
bid for the other side. No slippage assumption is needed or added.

Errors cluster on the close time. Six samples of one market are one market,
and every series closing at the same minute shares a move.
"""

import collections
import math
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
MIN_N = 120


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def pnl(price, won, stake=10.0):
    c = int(stake / price)
    if c < 1:
        return None
    fee = math.ceil(round(0.07 * c * price * (1 - price), 9) * 100) / 100.0
    return (c if won else 0) - (c * price + fee)


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load(sh, name):
    rows = sh.worksheet(name).get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None or a is not None:
                path[o] = (b, a)
        if not path:
            continue
        out.append(dict(tk=r[0], ser=str(c("Series")),
                        close=str(c("Close Time")), yes=(res == "yes"),
                        path=path, vol=_f(c("Volume"))))
    return out


def report(bets, label, indent="  "):
    """bets: list of (dollars, close_time, price, won)."""
    if len(bets) < MIN_N:
        print(f"{indent}{label:<30} n={len(bets):<6} too few")
        return None
    g = collections.defaultdict(list)
    for v, ct, _, _ in bets:
        g[ct].append(v)
    n = len(bets)
    m = sum(v for v, _, _, _ in bets) / n
    se = cse(g, n)
    won = 100 * sum(1 for _, _, _, w in bets if w) / n
    paid = 100 * sum(p for _, _, p, _ in bets) / n
    by = sorted((sum(v) for v in g.values()), reverse=True)
    minus2 = sum(by[2:])
    share = 100 * sum(1 for x in by if x > 0) / len(by)
    t = m / se if se else 0.0
    flag = ""
    if m > 0 and t > 2.0:
        flag = "  <== POSITIVE" + ("" if minus2 > 0 else " but 2 days carry it")
    print(f"{indent}{label:<30} n={n:<6} won {won:>5.1f}% paid {paid:>5.1f}% "
          f"${m:>+6.2f} +/-{se:>4.2f} t={t:>+5.1f} "
          f"days+ {share:>3.0f}%{flag}")
    return m, t, minus2


def quotes(m, off, side):
    b, a = m["path"].get(off, (None, None))
    if side == "YES":
        return a
    return (1 - b) if b is not None else None


def main():
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    print("=" * 104)
    print(f"SEARCHING {tab}: {len(M)} settled markets, "
          f"{len({m['close'] for m in M})} close-times")
    print("=" * 104)

    # every takeable quote, both sides, every offset
    ALL = []
    for m in M:
        for off in OFFSETS:
            for side in ("YES", "NO"):
                p = quotes(m, off, side)
                if p is None or not (0.01 < p < 0.99):
                    continue
                won = m["yes"] if side == "YES" else (not m["yes"])
                v = pnl(p, won)
                if v is not None:
                    ALL.append((v, m["close"], p, won, off, side, m["ser"], m))
    print(f"\ntakeable quotes: {len(ALL)}")

    print("\n" + "=" * 104)
    print("1. BY PRICE BAND -- does the live finding replicate on 19x the data?")
    print("=" * 104)
    for lo, hi in ((0.01, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.30),
                   (0.30, 0.40), (0.40, 0.50), (0.50, 0.60), (0.60, 0.70),
                   (0.70, 0.80), (0.80, 0.90), (0.90, 0.99)):
        sel = [(v, c, p, w) for v, c, p, w, *_ in ALL if lo <= p < hi]
        report(sel, f"{lo:.2f}-{hi:.2f}")

    print("\n" + "=" * 104)
    print("2. THE PATH -- did the move direction matter?  (entry at T-3)")
    print("=" * 104)
    for lab, lo, hi in (("fell hard  (-30pp or worse)", -1.0, -0.30),
                        ("fell       (-10 to -30pp)", -0.30, -0.10),
                        ("flat       (-10 to +10pp)", -0.10, 0.10),
                        ("rose       (+10 to +30pp)", 0.10, 0.30),
                        ("rose hard  (+30pp or more)", 0.30, 1.0)):
        sel = []
        for m in M:
            e = quotes(m, 3, "YES")
            s = m["path"].get(14) or m["path"].get(12)
            if e is None or not s:
                continue
            start = s[1] if s[1] is not None else s[0]
            if start is None or not (0.01 < e < 0.99):
                continue
            if not (lo <= e - start < hi):
                continue
            v = pnl(e, m["yes"])
            if v is not None:
                sel.append((v, m["close"], e, m["yes"]))
        report(sel, lab)

    print("\n" + "=" * 104)
    print("3. BY SERIES  (all quotes)")
    print("=" * 104)
    for ser in sorted({m["ser"] for m in M}):
        sel = [(v, c, p, w) for v, c, p, w, _, _, s, _ in ALL if s == ser]
        report(sel, ser)

    print("\n" + "=" * 104)
    print("4. BY OFFSET  (how late is best?)")
    print("=" * 104)
    for off in OFFSETS:
        sel = [(v, c, p, w) for v, c, p, w, o, *_ in ALL if o == off]
        report(sel, f"T-{off} min")

    print("\n" + "=" * 104)
    print("Anything marked POSITIVE cleared t>2 AND survived deleting its two")
    print("best days. That is the bar the last two candidates failed, and it")
    print("is still only a reason to pre-register a forward test, never to bet.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())

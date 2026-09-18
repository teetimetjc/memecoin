"""Re-score the whole forward test the way Kalshi actually settles it.

THE BUG THIS MEASURES. Every "Correct?" column in the sheet answers one
question: did the coin's spot price finish above or below where it was when
the signal fired ("Price at Pred")? A Kalshi 15-minute contract settles a
different question: is the coin above or below a FIXED STRIKE at the close.
The strike ("K Target") is set when the market opens and is not where spot
sat when we sampled it.

The two questions agree only when the strike equals the sample price. They
do not: the median gap is about 0.04% of price, which is the same order as a
15-minute move, so the disagreement is not rare.

WHY THIS FLATTERS CHEAP BETS SPECIFICALLY, which is the part that matters.
A contract is cheap precisely BECAUSE the strike is far away -- the market
charges 31c for DOWN when spot is already well above the strike, because
falling that far is genuinely unlikely. Grading it as "did spot drift down
from my sample" turns that long shot back into a coin flip on paper. So the
cheaper the bet, the bigger the fabricated edge, which is exactly the
"cheap entries are where the edge lives" finding the strategy was built on.

Run it against an export of the workbook:
    python grade_check.py /path/to/Meme_Coin.xlsx
"""

import collections
import math
import sys


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def _pnl(entry_cents, won, stake=10.0):
    p = entry_cents / 100.0
    n = stake / p
    return (n if won else 0.0) - stake - _fee(n, p)


def load(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    it = wb["Predictions"].iter_rows(values_only=True)
    hdr = next(it)
    I = {h: i for i, h in enumerate(hdr) if h}
    N = len(hdr)
    out = []
    for r in it:
        r = (list(r) + [None] * N)[:N]
        if r[I["CVD Call"]] not in ("UP", "DOWN"):
            continue
        if r[I["CVD Correct?"]] not in ("Yes", "No"):
            continue
        pred, tgt, ev = (_f(r[I["Price at Pred"]]), _f(r[I["K Target"]]),
                         _f(r[I["Price at Eval"]]))
        if not (pred and tgt and ev):
            continue
        call = r[I["CVD Call"]]
        entry = _f(r[I["K Up%"]]) if call == "UP" else _f(r[I["K Down%"]])
        if not entry or not (0 < entry < 100):
            continue
        out.append({
            "ts": r[I["Timestamp"]], "sym": r[I["Symbol"]], "call": call,
            "entry": entry,
            # UP is a YES contract, DOWN is a NO contract, and YES pays when
            # the coin finishes ABOVE the strike. The calibration table below
            # tests that mapping rather than assuming it.
            "won_kalshi": (ev > tgt) if call == "UP" else (ev < tgt),
            "won_sheet": (ev > pred) if call == "UP" else (ev < pred),
            "gap_pct": abs(tgt - pred) / pred * 100.0,
            "up_pct": _f(r[I["K Up%"]]), "above_tgt": ev > tgt, "above_pred": ev > pred,
        })
    return out


def calibration(rows):
    """Which definition does the market's own price predict? That is the one
    it settles on. This is the test that makes the mapping a finding rather
    than an assumption."""
    bk = collections.defaultdict(lambda: [0, 0, 0])
    for r in rows:
        up = r["up_pct"]
        if up is None or not (0 < up < 100):
            continue
        b = bk[int(up // 10) * 10]
        b[0] += 1; b[1] += r["above_pred"]; b[2] += r["above_tgt"]
    print("\n  CALIBRATION -- what does the market's own UP% predict?")
    print(f"    {'market UP%':>12s} {'n':>5s} {'actual, sheet defn':>20s} {'actual, strike defn':>21s}")
    ep = et = tot = 0.0
    for b in sorted(bk):
        n, wp, wt = bk[b]
        if n < 15:
            continue
        mid = b + 5
        print(f"    {b:5d}-{b+10:3d}%  {n:5d} {wp/n*100:19.1f}% {wt/n*100:20.1f}%")
        ep += abs(wp / n * 100 - mid) * n; et += abs(wt / n * 100 - mid) * n; tot += n
    print(f"\n    mean |error|   sheet defn {ep/tot:.2f}pp     strike defn {et/tot:.2f}pp")
    print("    The market prices what it settles; the lower error is the real rule.")


def edge(rows, key):
    """Win rate minus the probability charged, clustered by window."""
    g = collections.defaultdict(list)
    for r in rows:
        g[r["ts"]].append((1.0 if r[key] else 0.0) - r["entry"] / 100.0)
    cl = [sum(v) / len(v) for v in g.values()]
    nb = sum(len(v) for v in g.values())
    m = sum(sum(v) for v in g.values()) / nb
    va = sum(len(v) * (c - m) ** 2 for v, c in zip(g.values(), cl)) / (len(cl) - 1)
    se = math.sqrt(va / len(cl))
    return m, se, nb, len(cl)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "Meme_Coin.xlsx"
    rows = load(path)
    n = len(rows)
    print("=" * 70)
    print(f"GRADE CHECK -- {n} graded CVD signals")
    print("=" * 70)

    dis = sum(1 for r in rows if r["won_sheet"] != r["won_kalshi"])
    gaps = sorted(r["gap_pct"] for r in rows)
    print(f"\n  Graded differently by Kalshi : {dis} of {n} ({dis/n*100:.1f}%)")
    print(f"  |strike - sample| as % of px : median {gaps[n//2]:.4f}%  "
          f"mean {sum(gaps)/n:.4f}%")

    print(f"\n  {'view':<14s}{'n':>6s}{'sheet hit':>11s}{'sheet P&L':>12s}"
          f"{'KALSHI hit':>12s}{'KALSHI P&L':>13s}")
    for cut, label in [(None, "all signals"), (60, "under 60c"),
                       (50, "under 50c"), (40, "under 40c")]:
        sel = [r for r in rows if cut is None or r["entry"] < cut]
        if not sel:
            continue
        c = len(sel)
        sw = sum(r["won_sheet"] for r in sel); kw = sum(r["won_kalshi"] for r in sel)
        sp = sum(_pnl(r["entry"], r["won_sheet"]) for r in sel)
        kp = sum(_pnl(r["entry"], r["won_kalshi"]) for r in sel)
        print(f"  {label:<14s}{c:6d}{sw/c*100:10.2f}%{sp:+12.2f}"
              f"{kw/c*100:11.2f}%{kp:+13.2f}")

    calibration(rows)

    print("\n  EDGE vs the price paid (clustered by window)")
    for label, key in [("by Kalshi strike", "won_kalshi"), ("by sheet defn", "won_sheet")]:
        m, se, nb, nc = edge(rows, key)
        print(f"    {label:<18s}{m*100:+6.2f}pp   95% CI "
              f"{(m-1.96*se)*100:+6.2f} to {(m+1.96*se)*100:+6.2f}"
              f"   ({nb} bets, {nc} windows)")
    print("=" * 70)


if __name__ == "__main__":
    main()

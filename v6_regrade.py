"""v6, re-scored against Kalshi's own settlement result.

WHY THIS RUN EXISTS. The headline number for v6 -- about -$402 over roughly
1,478 bets -- was produced by grade_check.py, which decides whether a bet won
by comparing our spot feed at evaluation time against the strike:

    won = (Price at Eval > K Target)     for an UP call

CG0 showed that exact comparison is wrong about 19% of the time. Kalshi
settles on its own reference index at its own moment; we sampled a different
feed at a different moment, and every 15-minute window sits near the money,
so the two disagree constantly. The same flaw killed the sub-10c study,
where it manufactured a 2.4x mispricing out of nothing.

So the -$402 is not trustworthy AS A NUMBER, and the direction of the error
is not obvious in advance. Label noise pulls a measured win rate toward a
coin flip: it flatters a rule that is worse than chance and punishes one that
is better. v6 measured below chance, which means correcting the labels should
move it UP. The open question is whether it moves up enough to matter.

PREDICTED IN ADVANCE, before running: the corrected result stays a loss.
With label noise e, a true win rate t measures as t(1-e) + (1-t)e. To carry a
measured 49% back to a profitable ~52% needs e near 0.2 AND the errors to
land asymmetrically. Noise this size is real but it is symmetric, so the
expectation is the loss shrinks and survives. Writing that down first is the
point -- a study that can accommodate either result explains nothing.

WHAT IS JOINED, and how. One row per v6 signal, matched to the Settled tab by
TICKER. Not by (timestamp, symbol): a ticker names exactly one market, while
the old key relied on our clock and Kalshi's window agreeing, which is a
second thing to get wrong. Rows whose market has no authoritative result are
DROPPED and counted, never guessed.

    UP   is a YES contract -> wins when Kalshi's result is "yes"
    DOWN is a NO  contract -> wins when Kalshi's result is "no"

Getting that mapping backwards is the single mistake that has cost this
project the most, so it is also checked empirically: the calibration table
asks which definition the market's own UP% actually predicts. The market
prices what it settles, so the definition with the lower calibration error is
the real rule. If that came out backwards, the mapping above would be wrong.

Every figure is reported three ways -- by Kalshi's result, by the old strike
comparison, and by the sheet's own "Correct?" column -- on the SAME rows, so
the difference is the grading and nothing else.

Read-only. Places nothing, writes nothing.
"""

import collections
import math
import sys

STAKE = 10.0                     # what the -$402 was scored at
MIN_BUCKET = 15


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def _pnl(entry_cents, won):
    """P&L on one bet. Fee is charged on entry only: a winner settles at
    $1.00 with nothing to sell, so there is no second leg to pay for."""
    p = entry_cents / 100.0
    n = STAKE / p
    return (n if won else 0.0) - STAKE - _fee(n, p)


def load(sh):
    rows = sh.worksheet("Predictions").get_all_values()
    if len(rows) < 2:
        return [], {}
    hi = {h: i for i, h in enumerate(rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    srows = sh.worksheet("Settled").get_all_values()
    si = {h: i for i, h in enumerate(srows[0])}
    settled = {}
    for r in srows[1:]:
        t = (r[si.get("Ticker", 0)] or "").strip()
        v = (r[si.get("Result", 1)] or "").strip().lower()
        if t and v in ("yes", "no"):
            settled[t] = v

    out = []
    skip = collections.Counter()
    for r in rows[1:]:
        call = cell(r, "CVD Call").strip().upper()
        if call not in ("UP", "DOWN"):
            skip["not a v6 signal"] += 1
            continue
        entry = _f(cell(r, "K Up%")) if call == "UP" else _f(cell(r, "K Down%"))
        if not entry or not (0 < entry < 100):
            skip["no usable entry price"] += 1
            continue
        # The late ticker is the market the bet would have been placed in.
        # The early one is the same window sampled sooner, kept only as a
        # fallback for rows written before the late column existed.
        tk = cell(r, "K Late Ticker").strip() or cell(r, "K Early Ticker").strip()
        res = settled.get(tk)
        if res is None:
            skip["no settled result for ticker" if tk else "no ticker logged"] += 1
            continue
        tgt, ev = _f(cell(r, "K Target")), _f(cell(r, "Price at Eval"))
        pred = _f(cell(r, "Price at Pred"))
        sheet = cell(r, "CVD Correct?").strip()
        out.append({
            "ts": cell(r, "Timestamp"), "sym": cell(r, "Symbol"),
            "call": call, "entry": entry, "ticker": tk,
            "won_real": (res == "yes") if call == "UP" else (res == "no"),
            "won_strike": (None if not (tgt and ev)
                           else ((ev > tgt) if call == "UP" else (ev < tgt))),
            "won_sheet": (True if sheet == "Yes"
                          else False if sheet == "No" else None),
            "up_pct": _f(cell(r, "K Up%")),
            "res_yes": res == "yes",
            "above_tgt": (None if not (tgt and ev) else ev > tgt),
            "above_pred": (None if not (pred and ev) else ev > pred),
        })
    return out, skip


def clustered(rows, key):
    """Mean P&L a bet, with the interval clustered by window.

    Five coins in one 15-minute window share the same market move, so
    treating them as independent would shrink the interval by about the
    square root of five and invent significance that is not there."""
    g = collections.defaultdict(list)
    for r in rows:
        if r[key] is None:
            continue
        g[r["ts"]].append(_pnl(r["entry"], r[key]))
    if len(g) < 3:
        return None
    nb = sum(len(v) for v in g.values())
    m = sum(sum(v) for v in g.values()) / nb
    cl = [sum(v) / len(v) for v in g.values()]
    var = sum(len(v) * (c - m) ** 2 for v, c in zip(g.values(), cl)) / (len(cl) - 1)
    half = 1.96 * math.sqrt(var / len(cl))
    return {"mean": m, "lo": m - half, "hi": m + half, "total": m * nb,
            "n": nb, "windows": len(cl)}


def calibration(rows):
    """Which definition does the market's own UP% predict? That is the one it
    settles on -- so this makes the yes/no mapping a finding, not a guess."""
    bk = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        up = r["up_pct"]
        if up is None or not (0 < up < 100) or r["above_tgt"] is None:
            continue
        b = bk[int(up // 10) * 10]
        b[0] += 1
        b[1] += r["above_pred"] is True
        b[2] += r["above_tgt"]
        b[3] += r["res_yes"]
    print("\n  CALIBRATION -- what does the market's own UP% actually predict?")
    print(f"    {'market UP%':>13s} {'n':>6s} {'sheet defn':>12s} "
          f"{'strike defn':>13s} {'KALSHI result':>15s}")
    err = [0.0, 0.0, 0.0]
    tot = 0
    for b in sorted(bk):
        n, wp, wt, wr = bk[b]
        if n < MIN_BUCKET:
            continue
        mid = b + 5
        print(f"    {b:5d}-{b+10:3d}%  {n:6d} {wp/n*100:11.1f}% "
              f"{wt/n*100:12.1f}% {wr/n*100:14.1f}%")
        for i, w in enumerate((wp, wt, wr)):
            err[i] += abs(w / n * 100 - mid) * n
        tot += n
    if not tot:
        print("    too few rows to bucket")
        return
    print(f"\n    mean |error|   sheet {err[0]/tot:.2f}pp    "
          f"strike {err[1]/tot:.2f}pp    KALSHI {err[2]/tot:.2f}pp")
    print("    The market prices what it settles, so the LOWEST error here is")
    print("    the real settlement rule. If that is not the Kalshi column,")
    print("    something in this join is wrong and nothing below should be")
    print("    believed.")


def main():
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    rows, skip = load(sh)

    print("=" * 72)
    print("v6 RE-GRADED AGAINST KALSHI'S OWN SETTLEMENT")
    print("=" * 72)
    print(f"\n  {len(rows)} v6 signals matched to a real settlement result")
    for k, v in skip.most_common():
        print(f"    dropped, {k}: {v}")
    if len(rows) < 50:
        print("\n  Too few matched rows to re-grade. Run settle_fetch.py "
              "first -- it\n  now harvests tickers from Predictions as well "
              "as Path.")
        return 1
    print(f"\n  PREDICTED IN ADVANCE: still a loss, but smaller than -$402.")

    both = [r for r in rows if r["won_strike"] is not None]
    if both:
        dis = sum(1 for r in both if r["won_strike"] != r["won_real"])
        print(f"\n  The old strike comparison disagreed with Kalshi on "
              f"{dis} of {len(both)}\n  rows ({dis/len(both)*100:.1f}%) -- "
              f"this is the bug, measured directly on\n  the v6 sample "
              f"rather than inherited from the Path study.")
    sheet_rows = [r for r in rows if r["won_sheet"] is not None]
    if sheet_rows:
        d2 = sum(1 for r in sheet_rows if r["won_sheet"] != r["won_real"])
        print(f"  The sheet's own \"CVD Correct?\" disagreed on {d2} of "
              f"{len(sheet_rows)} ({d2/len(sheet_rows)*100:.1f}%).")

    print(f"\n  SCORED THREE WAYS, ON THE SAME {len(rows)} ROWS "
          f"(${STAKE:.0f} a bet)")
    print(f"    {'grading':<22s}{'n':>6s}{'hit rate':>11s}"
          f"{'per bet':>10s}{'total':>12s}   95% CI per bet")
    for label, key in [("KALSHI result", "won_real"),
                       ("old strike compare", "won_strike"),
                       ("sheet Correct?", "won_sheet")]:
        st = clustered(rows, key)
        if not st:
            print(f"    {label:<22s}  --")
            continue
        sel = [r for r in rows if r[key] is not None]
        hits = sum(1 for r in sel if r[key])
        print(f"    {label:<22s}{st['n']:6d}{hits/len(sel)*100:10.2f}%"
              f"{st['mean']:+10.2f}{st['total']:+12.2f}   "
              f"{st['lo']:+.2f} to {st['hi']:+.2f}")

    print("\n  BY PRICE PAID, scored by Kalshi's result only")
    print(f"    {'band':<14s}{'n':>6s}{'hit':>9s}{'implied':>9s}"
          f"{'edge':>8s}{'per bet':>10s}{'total':>11s}")
    bands = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]
    for lo, hi_ in bands:
        sel = [r for r in rows if lo <= r["entry"] < hi_]
        if len(sel) < MIN_BUCKET:
            continue
        st = clustered(sel, "won_real")
        hit = sum(1 for r in sel if r["won_real"]) / len(sel) * 100
        imp = sum(r["entry"] for r in sel) / len(sel)
        print(f"    {str(lo)+'-'+str(hi_)+'c':<14s}{len(sel):6d}{hit:8.1f}%"
              f"{imp:8.1f}%{hit-imp:+8.1f}"
              + (f"{st['mean']:+10.2f}{st['total']:+11.2f}" if st else ""))

    print("\n  BY CALL DIRECTION")
    for c in ("UP", "DOWN"):
        sel = [r for r in rows if r["call"] == c]
        st = clustered(sel, "won_real")
        if not st:
            continue
        hit = sum(1 for r in sel if r["won_real"]) / len(sel) * 100
        imp = sum(r["entry"] for r in sel) / len(sel)
        print(f"    {c:<14s}{len(sel):6d}{hit:8.1f}%{imp:8.1f}%"
              f"{hit-imp:+8.1f}{st['mean']:+10.2f}{st['total']:+11.2f}")

    calibration(rows)
    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CG9 -- does a Kalshi price move predict the NEXT Kalshi price move?

WHY THIS IS NOT ANOTHER FAILED FORECAST. Six ideas have died asking which
way a coin will go, or whether a contract is underpriced against its
settlement. This asks neither. It asks whether the CONTRACT's own price has
short-term structure -- momentum or mean reversion measured mid to mid,
with settlement never entering the calculation. The mislabelled outcome
that killed the cheap-contract study cannot touch this, because no outcome
is used.

If a 30-second move predicts the next 30-second move, that is tradeable
without any view on crypto at all.

WHAT WOULD MAKE IT A MIRAGE, and is therefore controlled for:

  THE SPREAD. Mid-to-mid moves are free; real ones are not. A signal worth
  less than half the spread on each side is not an edge, so the spread at
  the moment of the signal is reported beside the result.

  BID-ASK BOUNCE. A mid computed from a wide book jitters as one side is
  quoted and requoted, which manufactures spurious mean reversion. Samples
  with a spread above 4c are reported separately for that reason.

  THE DRIFT TO ZERO OR ONE. Every one of these markets resolves, so late in
  a window prices run to the extremes and any "momentum" is just the clock.
  Results are split by time remaining to keep that visible.

Reports, for each bucket of the previous move: how many, the average NEXT
move, and whether that next move is bigger than the spread it would cost to
capture.

Read-only. Places nothing.
"""

import statistics as st
import sys

import hedge

MIN_N = 30


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    import predictor as P
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet("Path").get_all_values()
    if len(rows) < 2:
        print("no Path rows")
        return 1
    hi = {h: i for i, h in enumerate(rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    obs = []
    for r in rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED:
            continue
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        n = min(len(asks), len(bids))
        mids, spreads, times = [], [], []
        for k in range(n):
            a, b = asks[k], bids[k]
            if a is None or b is None:
                mids.append(None); spreads.append(None); times.append(None)
                continue
            mids.append((a + b) / 2.0)
            spreads.append(a - b)
            times.append(first + k * step)
        # Three consecutive good samples: the move into now, and the move
        # after. Anything with a gap is dropped rather than interpolated --
        # a missing sample is missing information, not a straight line.
        for k in range(1, len(mids) - 1):
            if None in (mids[k-1], mids[k], mids[k+1]):
                continue
            obs.append({"prev": mids[k] - mids[k-1],
                        "next": mids[k+1] - mids[k],
                        "spread": spreads[k],
                        "left": 900.0 - times[k],
                        "mid": mids[k]})

    print("=" * 70)
    print("CG9 -- DOES A KALSHI MOVE PREDICT THE NEXT KALSHI MOVE?")
    print("=" * 70)
    print(f"\n{len(obs)} consecutive book triples")
    if not obs:
        return 0
    print(f"median spread: {st.median([o['spread'] for o in obs]):.1f}c")

    def table(sub, label):
        print(f"\n{label}   (n={len(sub)})")
        if len(sub) < MIN_N:
            print("   too few to judge")
            return
        print(f"{'previous move':>16} {'N':>6} {'next move':>11} "
              f"{'spread':>8} {'worth it?':>10}")
        cuts = [(-99, -6, "fell 6c+"), (-6, -3, "fell 3-6c"),
                (-3, -1, "fell 1-3c"), (-1, 1, "flat"),
                (1, 3, "rose 1-3c"), (3, 6, "rose 3-6c"),
                (6, 99, "rose 6c+")]
        for lo, up, name in cuts:
            s = [o for o in sub if lo <= o["prev"] < up]
            if len(s) < MIN_N:
                print(f"{name:>16} {len(s):>6}   (too few)")
                continue
            nxt = st.mean([o["next"] for o in s])
            spr = st.median([o["spread"] for o in s])
            se = st.stdev([o["next"] for o in s]) / (len(s) ** 0.5)
            sig = "yes" if abs(nxt) - 1.96 * se > spr / 2 else "no"
            print(f"{name:>16} {len(s):>6} {nxt:>+10.2f}c {spr:>7.1f}c "
                  f"{sig:>10}")

    table(obs, "ALL SAMPLES")
    table([o for o in obs if o["spread"] <= 4], "TIGHT BOOKS ONLY (spread <= 4c)")
    table([o for o in obs if o["left"] >= 360],
          "EARLY IN THE WINDOW (6+ min left)")
    table([o for o in obs if 20 <= o["mid"] <= 80],
          "MID-RANGE PRICES ONLY (20-80c)")

    print("\n'worth it?' asks whether the next move beats half the spread")
    print("with 95% confidence -- i.e. whether it survives the cost of")
    print("crossing to capture it. A signal smaller than the spread is a")
    print("statistic, not a trade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

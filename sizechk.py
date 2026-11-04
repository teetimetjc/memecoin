"""What unit is yes_ask_size_fp in? Kalshi-only read, no Odds quota.

THE OBSERVATION THAT PROMPTED THIS. nflgap.py's first run printed resting
ask sizes of 759,373 and 321,747 contracts on NFL game markets. At 60c that
is $455,000 sitting on one side of one book, which is not credible, and the
field carries an `_fp` suffix -- Kalshi's fixed-point convention, which the
orderbook envelope already uses (`orderbook_fp`).

So the number is probably scaled. PROBABLY IS NOT GOOD ENOUGH, and guessing
a divisor is exactly the move that produces a plausible wrong number. This
asks the exchange instead.

HOW IT DECIDES. The orderbook endpoint returns the resting levels for a
market. The quantity at the best level IS the size behind the best quote, by
definition. So for each market:

    market object : yes_ask_size_fp, yes_ask_size, yes_bid_size_fp, ...
    orderbook     : the quantity at the matching level

and the ratio between them is the scale factor -- read off real pairs, not
assumed. If the ratio is 1 across every market, the sizes are already
contracts and the implausible numbers are real. If it is a constant like
10000, that is the divisor. If it varies, neither story holds and that is
the finding.

THE RAW ROWS ARE PRINTED TOO, untouched, because a derived ratio computed
from two fields that were both misread would agree with itself.

WHY IT MATTERS. Depth is the number that decides whether a gap is tradeable,
and a quote with nothing behind it is what killed T-3 and oil, both times
after the price looked clean. An edge sized against a phantom 700,000
contracts is the same mistake with a bigger number.

Read-only. Places no order, writes no row, spends no Odds API quota.
"""
import json
import os

import collect15 as C

SERIES = (os.environ.get("SIZECHK_SERIES") or "KXNFLGAME").strip()
# How many markets to compare. Small: this is a unit question, not a survey.
N = int(os.environ.get("SIZECHK_N") or 6)
DEPTH = int(os.environ.get("SIZECHK_DEPTH") or 3)


def fnum(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def best(levels):
    """(price, size) at the best -- highest -- resting bid on one side."""
    bp = bs = None
    for row in levels or []:
        try:
            p, s = fnum(row[0]), fnum(row[1])
        except Exception:
            continue
        if p is None:
            continue
        if bp is None or p > bp:
            bp, bs = p, s
    return bp, bs


def main():
    print("=" * 100)
    print(f"SIZE UNITS: market-object size fields vs the orderbook, {SERIES}")
    print("  Asks the exchange what the scale is rather than guessing a")
    print("  divisor. Spends no Odds API quota.")
    print("=" * 100)

    d, err = C.get("/trade-api/v2/markets", series_ticker=SERIES, limit=200)
    if err:
        print(f"\n  THE READ FAILED: {err}  -- a real answer, not an empty "
              f"series.")
        return 1
    mks = (d or {}).get("markets") or []
    print(f"\n  {len(mks)} market(s) in {SERIES}")
    if not mks:
        print("  EMPTY. Check the series ticker.")
        return 1

    # Markets with a two-sided book are the only ones that can answer this.
    live = [m for m in mks
            if fnum(m.get("yes_ask_size_fp")) or fnum(m.get("yes_ask_size"))]
    print(f"  {len(live)} carry a size field; comparing the first "
          f"{min(N, len(live))}")
    if not live:
        print("  NO SIZE FIELDS AT ALL. That is the finding: the sizes in")
        print("  nflgap came from somewhere else, and that is worth knowing")
        print("  before anything is sized against them.")
        return 1

    print(f"\n  field names on a market object that mention size:")
    print("    " + ", ".join(sorted(k for k in live[0] if "size" in k.lower())))

    ratios = []
    for m in live[:N]:
        tk = m.get("ticker")
        print("\n" + "-" * 100)
        print(f"  {tk}")
        for k in sorted(k for k in m if "size" in k.lower()):
            print(f"    market.{k:<22} {m.get(k)}")
        print(f"    market.yes_ask_dollars      {m.get('yes_ask_dollars')}"
              f"   yes_bid_dollars {m.get('yes_bid_dollars')}")

        shown = False
        for path in (f"/trade-api/v2/markets/{tk}/orderbook",
                     f"/trade-api/v2/markets/{tk}/orderbook_fp"):
            ob_d, ob_err = C.get(path, depth=DEPTH)
            if not ob_d:
                print(f"    {path.rsplit('/', 1)[-1]:<14} no response "
                      f"({ob_err})")
                continue
            ob = ob_d.get("orderbook_fp") or ob_d.get("orderbook") or ob_d
            if not isinstance(ob, dict):
                continue
            print(f"    RAW from {path.rsplit('/', 1)[-1]}, untouched:")
            print("      " + json.dumps(ob, default=str)[:600])
            shown = True

            yes_lv = ob.get("yes_dollars") or ob.get("yes")
            no_lv = ob.get("no_dollars") or ob.get("no")
            # Kalshi runs ONE book: a resting NO bid at q IS a YES ask at
            # 1-q, so the size behind the YES ask is the NO bid's size.
            nb_p, nb_s = best(no_lv)
            yb_p, yb_s = best(yes_lv)
            print(f"    best NO bid  price {nb_p}  size {nb_s}   "
                  f"(= the YES ask's depth)")
            print(f"    best YES bid price {yb_p}  size {yb_s}")

            for fld, obs in (("yes_ask_size_fp", nb_s),
                             ("yes_ask_size", nb_s),
                             ("yes_bid_size_fp", yb_s),
                             ("yes_bid_size", yb_s)):
                mv = fnum(m.get(fld))
                if mv is None or obs is None or obs == 0:
                    continue
                r = mv / obs
                ratios.append((fld, r))
                print(f"    RATIO market.{fld} / orderbook = "
                      f"{mv:.0f} / {obs:.0f} = {r:.4f}")
            break
        if not shown:
            print("    the orderbook answered nothing for this market")

    print("\n" + "=" * 100)
    if not ratios:
        print("  NO PAIR COULD BE COMPARED. The scale is still unknown, and")
        print("  that is the honest result -- nothing should be sized against")
        print("  these fields until it is. Do not pick a divisor from the")
        print("  magnitudes alone.")
        return 1

    vals = sorted(r for _, r in ratios)
    lo, hi = vals[0], vals[-1]
    mid = vals[len(vals) // 2]
    print(f"  {len(vals)} comparisons   ratio median {mid:.4f}   "
          f"range {lo:.4f} to {hi:.4f}")
    # A scale factor is a constant. Spread means it is not a scale factor.
    if hi <= 0 or lo <= 0:
        print("  A ratio came out non-positive. Something is misread; stop.")
    elif hi / lo <= 1.02:
        if abs(mid - 1.0) <= 0.02:
            print("  CONSTANT AND ~1: the market-object sizes ARE contracts.")
            print("  The implausible numbers in nflgap are real resting size,")
            print("  which is itself worth a second look before trusting it.")
        else:
            print(f"  CONSTANT at {mid:.4f}: that is the scale factor. Divide")
            print(f"  the market-object size by it to get contracts "
                  f"({mid:.0f} looks like the fixed-point multiplier).")
    else:
        print("  THE RATIO IS NOT CONSTANT. Then it is not a unit conversion,")
        print("  and the two fields are not measuring the same thing -- the")
        print("  market object's size may be cumulative across levels, or a")
        print("  24h figure, or stale. Nothing gets sized against it until")
        print("  this is understood; a divisor fitted to a spread like this")
        print("  would be a plausible wrong number.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

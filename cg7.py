"""CG7 -- is YES + NO ever purchasable for less than a dollar?

WHY THIS IS DIFFERENT FROM EVERYTHING THAT FAILED. Every idea tested so far
was a forecast: which way will the coin go, will the cheap side come back,
is this contract underpriced. All six died. This asks nothing about the
future. YES and NO together pay exactly $1.00 at settlement, always, so if
both can be BOUGHT for less than that with fees included, the profit is
arithmetic rather than prediction.

WHAT MAKES IT FAIL, and why the answer is probably no: you pay the ASK on
both sides, and the two asks straddle a spread. A market quoted 71c yes /
32c no sums to 103c -- the 3c is the spread, and it is exactly what stops
this from working. The question is whether the book ever crosses far enough
to overcome both the spread and two fees.

Fees are charged on BOTH legs and are largest in the middle of the range,
where ceil(0.07 * n * p * (1-p)) peaks. A 50/50 market pays about 1.75c a
contract per side; a 5/95 market pays about 0.33c. So the cheapest place
for this to work is the extremes, which is also where the sum is least
likely to cross.

Counted honestly:
  - the sum uses both ASKS, the prices a buyer actually pays
  - depth is the smaller of the two sides, since an unmatched leg is not
    an arbitrage but a naked position
  - fees are charged twice, at the real Kalshi formula
  - a "crossed" quote with 1 contract behind it is reported separately,
    because it is not tradeable in any size

Read-only. Places nothing.
"""

import math
import sys

import hedge

MIN_PROFIT_C = 0.0               # report everything, judge afterwards


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


fee = lambda n, p: math.ceil(0.07 * n * p * (1 - p) * 100) / 100


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

    samples = 0
    crossed = []
    sums = []
    for r in rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        if ts in hedge.POISONED:
            continue
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        asz = [_f(x) for x in str(cell(r, "Yes Ask Sz")).split(",")]
        bsz = [_f(x) for x in str(cell(r, "Yes Bid Sz")).split(",")]
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        n = min(len(asks), len(bids))
        for k in range(n):
            a, b = asks[k], bids[k]
            if a is None or b is None:
                continue
            # Buying NO means selling YES, so the NO ask is 100 minus the
            # YES BID. Using the yes ask for both sides would compare a
            # price against itself and invent a spread that is not there.
            no_ask = 100.0 - b
            total = a + no_ask
            samples += 1
            sums.append(total)
            if total >= 100.0:
                continue
            # Depth is the SMALLER side: one leg without the other is a
            # naked position, not an arbitrage.
            sa = asz[k] if k < len(asz) else None
            sb = bsz[k] if k < len(bsz) else None
            size = None
            if sa is not None and sb is not None:
                size = min(sa, sb)
            gross = 100.0 - total          # cents per pair, before fees
            f = (fee(1, a / 100.0) + fee(1, no_ask / 100.0)) * 100
            crossed.append({"ts": ts, "sym": str(cell(r, "Symbol")),
                            "secs": first + k * step, "yes": a,
                            "no": no_ask, "sum": total, "gross": gross,
                            "fee": f, "net": gross - f, "size": size})

    print("=" * 66)
    print("CG7 -- YES + NO ARBITRAGE")
    print("=" * 66)
    print(f"\n{samples} order-book samples examined")
    if sums:
        sums.sort()
        print(f"YES ask + NO ask, cents:")
        print(f"  minimum {sums[0]:.1f}   median {sums[len(sums)//2]:.1f}"
              f"   maximum {sums[-1]:.1f}")
        print(f"  (100.0 is break-even BEFORE fees; the excess is the spread)")

    print(f"\nsamples where the two sides summed under 100c: {len(crossed)}")
    if not crossed:
        print("\nNONE. The spread never crossed, so there is nothing to")
        print("arbitrage -- which is what a functioning market looks like.")
        return 0

    profitable = [c for c in crossed if c["net"] > MIN_PROFIT_C]
    tradeable = [c for c in profitable if c["size"] and c["size"] >= 10]
    print(f"  of those, profitable after BOTH fees: {len(profitable)}")
    print(f"  of those, with 10+ contracts on the thinner side: "
          f"{len(tradeable)}")

    if profitable:
        print(f"\n{'when':>18} {'coin':>5} {'yes':>6} {'no':>6} {'sum':>6} "
              f"{'gross':>6} {'fee':>6} {'net':>6} {'size':>6}")
        for c in sorted(profitable, key=lambda x: -x["net"])[:15]:
            print(f"{c['ts']:>18} {c['sym']:>5} {c['yes']:>5.1f}c "
                  f"{c['no']:>5.1f}c {c['sum']:>5.1f}c {c['gross']:>5.2f}c "
                  f"{c['fee']:>5.2f}c {c['net']:>5.2f}c "
                  f"{(str(int(c['size'])) if c['size'] else '?'):>6}")
        tot = sum(c["net"] * (min(c["size"], 100) if c["size"] else 0)
                  for c in tradeable) / 100.0
        print(f"\nIf every tradeable one had been taken at up to 100 pairs:")
        print(f"  ${tot:.2f} total, across {len(tradeable)} opportunities")
        print(f"  over {samples} samples -- i.e. one every "
              f"{samples/max(len(tradeable),1):.0f} book reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())

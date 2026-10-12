"""Pull the exchange's own fill records for one market and print them raw.

WHY THIS EXISTS. On 2026-10-11 an ETH order quoted at 90.1c came back
recorded as filled at 54.0c -- thirty-six cents below the touch, where the
next-best fill in the whole live record is eleven cents below. That single
bet is nineteen percent of the entire profit to date, so whether it is a
real fill or a reporting artifact is worth one API call rather than an
assumption.

A buy cannot normally execute below the best offer. The explanations worth
distinguishing are:

  SOMEONE WAS RESTING AN OFFER FAR BELOW THE TOUCH and the order swept it.
  Then the fill is real, the money is real, and the record stands -- it was
  simply a very lucky sweep through a thin book.

  THE ORDER FILLED IN PIECES at different prices and the average is being
  reported as if it were one price. Then the fill is still real but the
  single number is a summary, and the pieces are worth seeing.

  THE PRICE FIELD MEANS SOMETHING ELSE than the code assumes -- a no-side
  price on a yes order, say. Then the arithmetic is wrong and so is the
  headline.

So it prints every fill the exchange has for the market, field by field,
with nothing computed on top.

Read-only.
"""

import json
import os
import sys

import tally


def main():
    want = (sys.argv[1] if len(sys.argv) > 1
            else os.environ.get("TICKER", "")).strip()
    if not want:
        print("usage: fillcheck.py TICKER")
        return 1
    print("=" * 84)
    print(f"EVERY FILL THE EXCHANGE HAS FOR  {want}")
    print("=" * 84)

    d = tally.get("/portfolio/fills", limit=200)
    fills = [f for f in ((d or {}).get("fills") or [])
             if str(f.get("ticker") or "") == want]
    if not fills:
        print("  no fills returned for that ticker in the last 200")
        return 0

    for i, f in enumerate(fills, 1):
        print(f"\n  --- fill {i} of {len(fills)} ---")
        for k in sorted(f):
            print(f"    {k:<26} {json.dumps(f[k])}")

    print("\n" + "=" * 84)
    # The only arithmetic here, and it is stated rather than hidden: how many
    # contracts at what price, so a split fill is visible as a split.
    tot = 0.0
    cnt = 0.0
    for f in fills:
        c = tally._f(f.get("count_fp")) or tally._f(f.get("count")) or 0.0
        side = str(f.get("side") or "").lower()
        px = (tally._f(f.get("no_price_dollars")) if side == "no"
              else tally._f(f.get("yes_price_dollars")))
        if px is None or not c:
            continue
        cnt += c
        tot += c * px
        print(f"  {c:>6.0f} contracts at {100*px:>5.1f}c  (side {side})")
    if cnt:
        print(f"  {cnt:>6.0f} total, weighted average {100*tot/cnt:.1f}c")
    return 0


if __name__ == "__main__":
    sys.exit(main())

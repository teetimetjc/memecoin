"""Is my fee formula right? Check it against fills that actually happened.

Every dollar figure in this project uses

    fee = ceil(0.07 * contracts * price * (1 - price))   to the cent

taken from Kalshi's documentation and never verified against a real
transaction. It is load-bearing in a way that deserves checking, because
several conclusions sit within a cent or two of break-even: deep favourites
came out at -$0.02 to -$0.05 per bet, and if the true fee is lower than this
those bands turn positive and "no edge anywhere" becomes wrong.

TWO SOURCES OF GROUND TRUTH.

  The Fills tab: real orders placed against Kalshi, with the contract count
  and the dollars actually spent. Cost minus contracts times price is the
  fee, whatever the formula says.

  Kapelame's trade log: a fee field on every paper trade, computed
  independently by code this project did not write. Agreement between two
  independent implementations is weak evidence; disagreement is strong.

If the formula is wrong, the interesting direction is LOWER -- an overstated
fee would have manufactured a negative result, which is exactly the kind of
error that hides behind a conclusion nobody wants to hear.
"""

import json
import math
import statistics as st
import sys

import predictor as P


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def mine(c, price):
    return math.ceil(round(0.07 * c * price * (1 - price), 9) * 100) / 100.0


def main():
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)

    print("=" * 84)
    print("1. REAL FILLS  (cost actually spent vs contracts x price)")
    print("=" * 84)
    rows = sh.worksheet("Fills").get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    n = 0
    for r in rows[1:]:
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        if str(c("Status")).strip().upper() != "PLACED":
            continue
        ct = _f(c("Contracts"))
        cost = _f(c("Cost"))
        ask = _f(c("Asked At c"))
        if not ct or cost is None or ask is None:
            continue
        px = ask / 100.0
        implied = cost - ct * px
        n += 1
        print(f"   {ct:>5.0f} contracts @ {ask:>5.1f}c   spent ${cost:>7.2f}"
              f"   notional ${ct*px:>7.2f}   implied fee ${implied:>+6.2f}"
              f"   my formula ${mine(int(ct), px):>5.2f}")
    if not n:
        print("   no PLACED fills with a contract count and cost")
    else:
        print(f"\n   NOTE: if implied fee is ~0 the Cost column is notional only")
        print(f"   and carries no fee, which makes it silent on this question")
        print(f"   rather than evidence the fee is zero.")

    print("\n" + "=" * 84)
    print("2. KAPELAME'S OWN FEE FIELD  (independent implementation)")
    print("=" * 84)
    ws = sh.worksheet("Kapelame Paper")
    seen = {}
    for r in ws.get_all_values()[1:]:
        if len(r) < 4 or r[2] != "db:trades":
            continue
        try:
            d = json.loads(r[3])
        except Exception:
            continue
        seen[(r[0], d.get("id"))] = d
    diffs, rows2 = [], 0
    print(f"   {'contracts':>9} {'entry':>7} {'their fee':>10} {'mine':>8} {'diff':>8}")
    for d in list(seen.values()):
        ct = _f(d.get("contracts"))
        px = _f(d.get("entry_price"))
        their = _f(d.get("fee"))
        if not ct or px is None or their is None:
            continue
        m = mine(int(ct), px)
        diffs.append(their - m)
        if rows2 < 12:
            print(f"   {ct:>9.0f} {px:>7.3f} {their:>10.3f} {m:>8.2f} "
                  f"{their-m:>+8.3f}")
        rows2 += 1
    if diffs:
        print(f"\n   {len(diffs)} trades compared")
        print(f"   mean difference   {st.mean(diffs):+.4f}")
        print(f"   median difference {st.median(diffs):+.4f}")
        exact = sum(1 for x in diffs if abs(x) < 0.005)
        print(f"   within half a cent: {exact}/{len(diffs)} "
              f"({100*exact/len(diffs):.0f}%)")
        print(f"   worst overstatement by me: {max(diffs):+.4f}")
        print(f"   worst understatement by me: {min(diffs):+.4f}")
    else:
        print("   no comparable trades")

    print("\n" + "=" * 84)
    print("3. WHAT AN ERROR WOULD BE WORTH")
    print("=" * 84)
    print("   The bands that sit near break-even, and the per-contract fee")
    print("   my formula charges there:")
    for px in (0.85, 0.90, 0.95, 0.98):
        c = int(10.0 / px)
        f = mine(c, px)
        print(f"     price {px:.2f}: {c} contracts, fee ${f:.2f} "
              f"= {100*f/c:.2f}c per contract = ${f:.2f} on a $10 bet")
    print("\n   Deep favourites measured -$0.02 to -$0.05 per bet. If the real")
    print("   fee is more than a few cents per $10 below the formula, those")
    print("   bands are positive and the sweeping negative result is wrong.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

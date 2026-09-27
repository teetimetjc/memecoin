"""Ask Kalshi what it actually charged, on orders that really filled.

The fee under every dollar figure in this project is unverified. The Fills
tab turned out to be silent -- its Cost column is contracts times the LIMIT
price, so the apparent "fee" is just the one-cent limit buffer. Kapelame's
own fee field disagrees with my formula on all 431 trades and runs about
0.37x of it, but Kapelame is third-party paper code and is no more
authoritative than my memory of the docs.

Kalshi is authoritative, and there are real order IDs in the Fills tab. The
exchange records what it charged.

READ-ONLY, AND DELIBERATELY SO. This touches the portfolio endpoints, which
report positions and fills; it places nothing, cancels nothing and modifies
nothing. The account is in the state it was: betting halted, alerts off.

THE RESPONSE SHAPE IS DUMPED BEFORE IT IS PARSED. Whether a fill carries a
fee field, and under what name, is exactly the sort of thing this project
has guessed wrong four times -- orderbook vs orderbook_fp, last_price vs
last_price_dollars, a nesting level, and a settlement flag. So the first
record comes back verbatim and the comparison is built on what is there.
"""

import json
import math
import statistics as st
import sys

import requests

import predictor as P

HOST = "https://api.elections.kalshi.com"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def mine(c, price):
    return math.ceil(round(0.07 * c * price * (1 - price), 9) * 100) / 100.0


def get(path, **params):
    try:
        h = P._kalshi_headers("GET", path.split("?")[0]) or {}
        r = requests.get(HOST + path, headers=h, params=params or None,
                         timeout=25)
        return (r.json() if r.ok else None), (None if r.ok else
                                              f"HTTP {r.status_code} "
                                              f"{r.text[:120]}")
    except Exception as e:
        return None, str(e)[:120]


def main():
    print("=" * 84)
    print("WHAT DID KALSHI ACTUALLY CHARGE?")
    print("=" * 84)
    print("read-only: portfolio endpoints only, nothing placed or cancelled\n")

    for path in ("/trade-api/v2/portfolio/fills",
                 "/trade-api/v2/portfolio/settlements",
                 "/trade-api/v2/portfolio/orders"):
        d, err = get(path, limit=50)
        print(f"{path}")
        if not d:
            print(f"   -> {err}\n")
            continue
        key = next((k for k in ("fills", "settlements", "orders") if k in d),
                   None)
        rows = d.get(key) or [] if key else []
        print(f"   -> OK, {len(rows)} records under '{key}'")
        if rows:
            print(f"   keys: {sorted(rows[0].keys())}")
            feeish = [k for k in rows[0] if "fee" in k.lower()]
            print(f"   fee-ish fields: {feeish or 'NONE'}")
            print(f"   first record verbatim:\n     {json.dumps(rows[0])[:520]}")
        print()

    # If fills carry a fee, compare it to the formula on real transactions.
    d, err = get("/trade-api/v2/portfolio/fills", limit=200)
    fills = (d or {}).get("fills") or []
    if not fills:
        print("No fills returned, so the exchange cannot settle this here.")
        print("That leaves the fee unverified -- which is the honest state,")
        print("not a pass. Conclusions resting within a cent of zero stay")
        print("provisional until a real fill exists to check against.")
        return 0

    feekeys = [k for k in fills[0] if "fee" in k.lower()]
    if not feekeys:
        print("Fills carry no fee field. The charge would have to be inferred")
        print("from balance movements, which is a different and weaker test.")
        return 0

    print("=" * 84)
    print(f"COMPARISON on {len(fills)} real fills, using '{feekeys[0]}'")
    print("=" * 84)
    diffs = []
    for f in fills:
        c = _f(f.get("count"))
        px = _f(f.get("yes_price_dollars")) or (
            (_f(f.get("yes_price")) or 0) / 100.0)
        theirs = _f(f.get(feekeys[0]))
        if theirs is not None and theirs > 1.5:
            theirs = theirs / 100.0          # cents, not dollars
        if not c or not px or theirs is None:
            continue
        m = mine(int(c), px)
        diffs.append(theirs - m)
        if len(diffs) <= 15:
            print(f"   {c:>5.0f} @ {100*px:>5.1f}c   Kalshi ${theirs:>6.3f}"
                  f"   mine ${m:>6.3f}   diff ${theirs-m:>+6.3f}")
    if diffs:
        print(f"\n   {len(diffs)} compared   mean diff ${st.mean(diffs):+.4f}"
              f"   median ${st.median(diffs):+.4f}")
        ok = sum(1 for x in diffs if abs(x) < 0.005)
        print(f"   within half a cent: {ok}/{len(diffs)} "
              f"({100*ok/len(diffs):.0f}%)")
        if st.mean(diffs) < -0.005:
            print("\n   MY FORMULA OVERSTATES THE FEE. Every negative result")
            print("   within a few cents of zero has to be recomputed, and")
            print("   the near-break-even bands may be positive.")
        elif st.mean(diffs) > 0.005:
            print("\n   My formula UNDERSTATES the fee; the negatives are")
            print("   larger than reported, not smaller.")
        else:
            print("\n   The formula matches. Every dollar figure stands.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

import collections
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
    print("   'mine' is ceil-to-the-cent; 'raw' is the same rate unrounded.")
    print("   If raw matches and mine does not, the RATE is right and the")
    print("   CEILING is the error -- worth up to a cent a bet, no more.\n")
    diffs, rawdiffs = [], []
    for f in fills:
        # count_fp, not count. The _fp/_dollars migration, fifth time.
        c = _f(f.get("count_fp")) or _f(f.get("count"))
        px = _f(f.get("yes_price_dollars"))
        if px is None:
            nb = _f(f.get("no_price_dollars"))
            px = (1.0 - nb) if nb is not None else None
        if px is None:
            px = (_f(f.get("yes_price")) or 0) / 100.0
        theirs = _f(f.get(feekeys[0]))
        if theirs is not None and theirs > 1.5:
            theirs = theirs / 100.0          # cents, not dollars
        if not c or not px or theirs is None:
            continue
        m = mine(int(c), px)
        raw = 0.07 * c * px * (1 - px)
        diffs.append(theirs - m)
        rawdiffs.append(theirs - raw)
        if len(diffs) <= 12:
            print(f"   {c:>6.1f} @ {100*px:>5.2f}c   Kalshi ${theirs:>8.5f}"
                  f"   mine ${m:>6.3f} (d {theirs-m:>+6.3f})"
                  f"   raw ${raw:>8.5f} (d {theirs-raw:>+8.5f})")
    if diffs:
        print(f"\n   {len(diffs)} compared   mean diff ${st.mean(diffs):+.4f}"
              f"   median ${st.median(diffs):+.4f}")
        ok = sum(1 for x in diffs if abs(x) < 0.005)
        print(f"   within half a cent: {ok}/{len(diffs)} "
              f"({100*ok/len(diffs):.0f}%)")
        if rawdiffs:
            print(f"\n   UNROUNDED rate: mean diff ${st.mean(rawdiffs):+.6f}"
                  f"   median ${st.median(rawdiffs):+.6f}")
            near = sum(1 for x in rawdiffs if abs(x) < 0.0005)
            print(f"   within 0.05c of the raw rate: {near}/{len(rawdiffs)}"
                  f" ({100*near/len(rawdiffs):.0f}%)")
        if st.mean(diffs) < -0.005:
            print("\n   MY FORMULA OVERSTATES THE FEE. Every negative result")
            print("   within a few cents of zero has to be recomputed, and")
            print("   the near-break-even bands may be positive.")
        elif st.mean(diffs) > 0.005:
            print("\n   My formula UNDERSTATES the fee; the negatives are")
            print("   larger than reported, not smaller.")
        else:
            print("\n   The formula matches. Every dollar figure stands.")

    # ------------------------------------------------------------------
    # MAKER VERSUS TAKER, measured rather than read off a blog.
    #
    # Everything this project has priced -- including both maker studies --
    # applied 0.07 * C * P * (1-P) to resting orders as well as crossing
    # ones. If Kalshi charges a RESTING fill less than that, every maker
    # conclusion here was scored against a fee it would not have paid, and
    # the exchange's own fills are the only authority worth having: the fee
    # schedule PDF is not reachable from this runner, and a secondary
    # summary of a fee is not a fee.
    #
    # The implied rate is fee / (C * P * (1-P)). If takers come out at 0.07
    # and makers at something smaller, that ratio is the answer.
    # ------------------------------------------------------------------
    print("\n" + "=" * 84)
    print("MAKER VERSUS TAKER: the implied rate, per side of the book")
    print("=" * 84)
    flags = sorted({k for f in fills for k in f
                    if "taker" in k.lower() or "maker" in k.lower()})
    if not flags:
        print("   Fills carry no maker/taker flag; keys available:")
        print(f"   {sorted(fills[0].keys())}")
        print("\n   Without it the two cannot be separated here, so the maker")
        print("   studies keep the taker fee -- the conservative choice, and")
        print("   an OPEN QUESTION rather than a settled one.")
        return 0
    print(f"   flag field(s): {flags}\n")
    fl = flags[0]
    byside = collections.defaultdict(list)
    for f in fills:
        c = _f(f.get("count_fp")) or _f(f.get("count"))
        px = _f(f.get("yes_price_dollars"))
        if px is None:
            nb = _f(f.get("no_price_dollars"))
            px = (1.0 - nb) if nb is not None else None
        theirs = _f(f.get(feekeys[0]))
        if theirs is not None and theirs > 1.5:
            theirs = theirs / 100.0
        if not c or not px or theirs is None:
            continue
        denom = c * px * (1 - px)
        if denom <= 0:
            continue
        byside[str(f.get(fl))].append(theirs / denom)
    for k, v in sorted(byside.items()):
        if not v:
            continue
        print(f"   {fl}={k:<7} n={len(v):<5} implied rate "
              f"median {st.median(v):.5f}   mean {st.mean(v):.5f}")
    if len(byside) < 2:
        print("\n   Only one side of the book appears in these fills, so the")
        print("   other rate is still unmeasured. The bots cross the spread,")
        print("   so the missing side is almost certainly the resting one --")
        print("   which is exactly the side a maker strategy would use.")
    else:
        print("\n   If the resting rate is materially below 0.07, maker.py and")
        print("   maker70.py were both scored too harshly and their")
        print("   conclusions have to be recomputed before being trusted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

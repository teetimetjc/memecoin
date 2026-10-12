"""Dump every fill and position for one ticker, raw, and say what they mean.

WHY. On 2026-10-12 the NO mapping test placed one order intending to BUY NO
at 91c on KXXRP15M. The fill came back reading

    side='no' action='sell' count=5.0 yes_price=0.15 no_price=0.85

and the position read flat. "Sell NO" is the opposite of the intended trade,
and a flat position confirms nothing either way, so the one question the
order existed to answer is still open and the market settles in minutes.

This prints the raw records with nothing computed on top, then states the two
readings that are consistent with them, so the settlement can decide between
them rather than an assumption.

Read-only.
"""
import json, os, sys
import tally

def main():
    want = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("TICKER","")).strip()
    if not want:
        print("usage: poscheck.py TICKER"); return 1
    print("="*86); print(f"RAW FILLS AND POSITIONS FOR {want}"); print("="*86)

    d = tally.get("/portfolio/fills", limit=200)
    fills = [f for f in ((d or {}).get("fills") or []) if str(f.get("ticker") or "")==want]
    print(f"\n  {len(fills)} fill(s)")
    for i,f in enumerate(fills,1):
        print(f"\n  --- fill {i} ---")
        for k in sorted(f): print(f"    {k:<28} {json.dumps(f[k])}")

    p = tally.get("/portfolio/positions", limit=200)
    pos = [x for x in ((p or {}).get("market_positions") or []) if str(x.get("ticker") or "")==want]
    print(f"\n  {len(pos)} position row(s)")
    for x in pos:
        print(f"\n  --- position ---")
        for k in sorted(x): print(f"    {k:<28} {json.dumps(x[k])}")

    print("\n"+"="*86)
    print("  HOW TO READ IT. We sent an ASK on the YES contract at 0.08,")
    print("  intending to sell YES at 8c, which is buying NO at 92c.")
    print()
    print("  READING ONE -- the mapping works. 'side=no action=sell' is the")
    print("  exchange describing the same trade from the NO book's side, and")
    print("  yes_price 0.15 means the ask filled ABOVE our minimum, i.e. NO")
    print("  acquired at 85c, cheaper than the 92c we were willing to pay.")
    print("  On a sell, the limit is a FLOOR, so a higher fill is a better")
    print("  one -- the mirror of the buy-side rule.")
    print()
    print("  READING TWO -- the mapping is backwards. We genuinely sold NO")
    print("  contracts, which is YES exposure at 15c, the opposite side.")
    print()
    print("  THE SETTLEMENT DECIDES. If this market settles NO and the")
    print("  account is PAID, reading one is right. If it settles YES and")
    print("  the account is paid, reading two is right and ALLOW_DOWN must")
    print("  stay off for good.")
    print("="*86)
    return 0

if __name__ == "__main__":
    sys.exit(main())

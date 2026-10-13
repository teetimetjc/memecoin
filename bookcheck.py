"""The live order book for the wide-spread FX series, and what 'AW' means.

WHY. finspread found the widest spreads on Kalshi concentrated in a family
of FX series -- KXUSDMXNAW, KXUSDINRAW, KXUSDJPYAW, KXGBPUSDAW, KXAUDUSDAW
-- all quoting 9-10c while their non-AW siblings on the SAME currency pairs
quote 1-2c. A fivefold spread difference on the same underlying is either a
real structural opportunity or a feature of the contract nobody looked up.

Candles cannot settle it. They say a quote existed at a price; they say
nothing about how much size sat there, how much was queued ahead of a
resting order, or why the market is shaped that way. Those three things are
what decide whether a spread can be earned, and all three are in the live
book and the market description.

SO THIS READS, FOR EACH SERIES:
  the market's own title, subtitle and rules -- to find out what AW IS
  the live yes/no bid and ask, and the SIZE at each
  how many levels deep the book goes
  open interest and volume, as a check on whether anyone is there at all

A WIDE SPREAD WITH ONE CONTRACT ON EACH SIDE IS NOT AN OPPORTUNITY. It is a
market with nobody in it, and an order posted there earns nothing because
nothing trades. The size column is the one that matters most.

Read-only: market metadata and public order books. Places nothing.
"""
import json, sys, time
import backfill as B

SERIES = ["KXGBPUSDAW", "KXAUDUSDAW", "KXUSDMXNAW", "KXUSDINRAW",
          "KXUSDJPYAW", "KXUSDJPY", "KXEURUSD"]


def main():
    print("=" * 100)
    print("LIVE ORDER BOOKS FOR THE WIDE-SPREAD FX SERIES")
    print("=" * 100)
    for s in SERIES:
        print(f"\n{'='*100}\n  {s}\n{'='*100}")
        d, err = B.get("/trade-api/v2/markets", series_ticker=s,
                       status="open", limit=4)
        mk = (d or {}).get("markets") or []
        if not mk:
            print(f"  no OPEN markets right now ({err or 'none returned'})")
            continue
        m = mk[0]
        # What is this contract?
        for k in ("title", "subtitle", "yes_sub_title", "rules_primary"):
            v = m.get(k)
            if v:
                print(f"    {k:<16} {str(v)[:150]}")
        for k in ("open_time", "close_time", "expiration_time",
                  "volume", "open_interest", "liquidity",
                  "yes_bid", "yes_ask", "no_bid", "no_ask",
                  "yes_bid_dollars", "yes_ask_dollars"):
            if m.get(k) is not None:
                print(f"    {k:<16} {json.dumps(m.get(k))}")

        print(f"\n    --- live book, top levels ---")
        for mm in mk[:3]:
            tk = str(mm.get("ticker"))
            ob, e2 = B.get(f"/trade-api/v2/markets/{tk}/orderbook", depth=5)
            book = (ob or {}).get("orderbook") or {}
            yes = book.get("yes") or []
            no = book.get("no") or []
            vol = mm.get("volume")
            oi = mm.get("open_interest")
            print(f"\n    {tk}   volume={vol}  open_interest={oi}")
            if not yes and not no:
                print(f"      EMPTY BOOK on both sides ({e2 or 'no levels'})")
                continue
            # Kalshi returns [[price_cents, size], ...] ascending.
            print(f"      yes side (price, size): "
                  f"{yes[-5:] if yes else 'none'}")
            print(f"      no  side (price, size): "
                  f"{no[-5:] if no else 'none'}")
            if yes and no:
                yb = max(p for p, _ in yes)
                nb = max(p for p, _ in no)
                ya = 100 - nb          # best yes ask = 100 - best no bid
                ysz = sum(sz for p, sz in yes if p == yb)
                asz = sum(sz for p, sz in no if p == nb)
                print(f"      => yes {yb}c bid (size {ysz}) / {ya}c ask "
                      f"(size {asz})   spread {ya-yb}c")
            time.sleep(B.PAUSE)
        time.sleep(B.PAUSE)

    print("\n" + "=" * 100)
    print("  THE SIZE COLUMN DECIDES THIS, not the spread. A 10c spread with")
    print("  one contract a side is an empty room: an order posted into it")
    print("  earns nothing, because nothing trades. Volume and open interest")
    print("  are the same question asked twice.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

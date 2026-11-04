"""Which exchange venue does each series trade on, and where is the cash?

THE HALT. At 03:20 UTC Kalshi refused a ~$5 XRP order for insufficient
balance while /portfolio/balance read $99.29. The balance payload carries a
balance_breakdown keyed by exchange_index: all $99.29 sits on index 0, and
indices 1-3 hold $0.00. KXXRP15M-26NOV050000-00 carries exchange_index 2.
So the order was checked against a venue with no money in it, while the
funding check read the total across all venues and approved the window.

Both numbers were right. They were measuring different things.

WHAT THIS ANSWERS: whether the live T-9 strategy can trade at all. If every
crypto series sits off the funded venue, it cannot place a single order and
will halt on the first signal of every session. If some sit on venue 0,
those still work and the rest do not -- which would be worse in a quiet way,
because the record would keep filling with a biased subset of coins and
nothing would look broken.

Read-only. Signed GETs. Places nothing and changes no setting.
"""
import json
import collect15 as C
import control

SERIES = ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
          "KXNFLGAME", "KXNBAGAME"]

print("=" * 88)
print("WHERE THE MONEY IS, AND WHERE EACH SERIES TRADES")
print("=" * 88)

bal = control.fetch_balance()
print(f"\n  control.fetch_balance() -- what the funding check uses: "
      f"${bal:,.2f}" if bal is not None else "\n  balance unreadable")

import predictor as P
import requests
h = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance", headers=h, timeout=15)
by_venue = {}
if r.ok:
    for row in (r.json().get("balance_breakdown") or []):
        try:
            by_venue[int(row.get("exchange_index"))] = float(row.get("balance"))
        except (TypeError, ValueError):
            pass
    print("\n  balance by venue:")
    for i in sorted(by_venue):
        print(f"    exchange_index {i}   ${by_venue[i]:,.2f}"
              + ("   <- the cash is here" if by_venue[i] > 0 else ""))
else:
    print(f"\n  could not read the breakdown: HTTP {r.status_code}")

print("\n" + "-" * 88)
print(f"  {'series':<14}{'venue':>7}{'funded $':>12}   verdict")
print("-" * 88)
dead, alive = [], []
for s in SERIES:
    d, err = C.get("/trade-api/v2/markets", series_ticker=s, limit=1)
    if err:
        print(f"  {s:<14}{'?':>7}{'?':>12}   read failed: {err}")
        continue
    mks = (d or {}).get("markets") or []
    if not mks:
        print(f"  {s:<14}{'?':>7}{'?':>12}   no markets returned")
        continue
    idx = mks[0].get("exchange_index")
    funded = by_venue.get(idx)
    ok = funded is not None and funded > 0
    (alive if ok else dead).append(s)
    print(f"  {s:<14}{str(idx):>7}"
          f"{(f'{funded:,.2f}' if funded is not None else '?'):>12}   "
          + ("can trade" if ok else "CANNOT TRADE -- no cash on this venue"))

print("\n" + "=" * 88)
if dead and not alive:
    print("  EVERY SERIES IS OFF THE FUNDED VENUE. The live strategy cannot")
    print("  place a single order. Setting the Control tab to RUNNING will")
    print("  not change that: it will halt again on the first signal.")
elif dead and alive:
    print("  SPLIT, WHICH IS THE WORSE CASE. Some series can trade and some")
    print(f"  cannot: {', '.join(alive)} work, {', '.join(dead)} do not.")
    print("  Nothing would look broken -- the bot would keep betting and the")
    print("  record would quietly fill with one subset of coins.")
elif alive:
    print("  Every series is on a funded venue. The rejection came from")
    print("  something else and this does not explain it.")
print("=" * 88)

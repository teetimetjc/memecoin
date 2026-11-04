"""Why did Kalshi say insufficient balance with $99 in the account?

THE CONTRADICTION. At 03:20 UTC the bot's own pre-flight check passed --
control.check_window reads /portfolio/balance, saw enough for the whole
window, and allowed the order. Kalshi then rejected a single ~$5 XRP order
with HTTP 400 insufficient_balance, and the bot halted on the rejection.

Both numbers cannot be right, so one of them is not measuring what it is
being used for. This finds out which, by reading the account three ways and
printing the raw payloads rather than a derived figure:

  /portfolio/balance    EVERY field, not just the one key the bot reads.
                        If the response carries both a total and an
                        available figure and we have been reading the total,
                        that is the whole answer and it has been wrong since
                        betting started.
  resting orders        An unfilled limit order reserves cash on most
                        exchanges. Fifteen-minute markets produce a lot of
                        orders; any that failed to fill and were never
                        cancelled would hold money hostage indefinitely,
                        and would be invisible to a balance check that reads
                        a total.
  open positions        Money already at risk, for the same reason.

WHAT WOULD MAKE THIS URGENT RATHER THAN CURIOUS. If resting orders are
holding a large share of the balance, the account has been quietly running
on less cash than every funding check believed, for as long as that has been
true -- and the halt is the first time it surfaced because it is the first
time Kalshi was asked for more than was actually there.

NO GUESSING. If the raw payloads do not explain the rejection, this says so
rather than fitting a story to them. A plausible explanation for a money
bug is worse than an open question.

Read-only: signed GETs only. Places nothing, cancels nothing, changes no
setting, and does not clear the halt.
"""
import json
import os

import requests

import predictor as P


def get(path, **params):
    hdrs = P._kalshi_headers("GET", path)
    if not hdrs:
        return None, "could not sign the request"
    try:
        r = requests.get(P.KALSHI_BASE + path.replace("/trade-api/v2", ""),
                         headers=hdrs, params=params or None, timeout=15)
        if not r.ok:
            return None, f"HTTP {r.status_code} {r.text[:160]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)[:160]


def dollars(cents):
    try:
        return float(cents) / 100.0
    except (TypeError, ValueError):
        return None


def main():
    print("=" * 96)
    print("WHY insufficient_balance WITH $99 IN THE ACCOUNT")
    print("  Read-only. Places nothing and does not clear the halt.")
    print("=" * 96)

    # ---------------------------------------------------------------- 1
    print("\n" + "-" * 96)
    print("1. /portfolio/balance -- EVERY field, raw")
    print("-" * 96)
    bal, err = get("/trade-api/v2/portfolio/balance")
    if err:
        print(f"  FAILED: {err}")
    else:
        print("  " + json.dumps(bal, indent=2, default=str)[:900])
        print()
        for k, v in sorted((bal or {}).items()):
            d = dollars(v)
            print(f"    {k:<28} {v!r}"
                  + (f"   (= ${d:,.2f} if cents)" if d is not None else ""))
        if len(bal or {}) > 1:
            print("\n  MORE THAN ONE FIELD. control.fetch_balance reads only")
            print("  'balance'. If another field here is smaller, THAT is the")
            print("  number Kalshi authorises against and the funding check")
            print("  has been reading the wrong one.")

    # ---------------------------------------------------------------- 2
    print("\n" + "-" * 96)
    print("2. RESTING ORDERS -- cash an unfilled limit order is holding")
    print("-" * 96)
    held = 0.0
    n_rest = 0
    d, err = get("/trade-api/v2/portfolio/orders", status="resting", limit=200)
    if err:
        print(f"  could not read orders: {err}")
    else:
        orders = (d or {}).get("orders") or []
        print(f"  {len(orders)} resting order(s)")
        if orders:
            print(f"  {'ticker':<34}{'side':<6}{'act':<6}{'remain':>8}"
                  f"{'price':>8}{'holds $':>10}")
        for o in orders[:40]:
            rem = o.get("remaining_count")
            if rem is None:
                rem = o.get("count")
            px = o.get("yes_price") if (o.get("side") or "").lower() == "yes" \
                else o.get("no_price")
            pxd = dollars(px)
            try:
                cost = float(rem) * (pxd or 0.0)
            except (TypeError, ValueError):
                cost = 0.0
            held += cost
            n_rest += 1
            print(f"  {str(o.get('ticker'))[:33]:<34}"
                  f"{str(o.get('side'))[:5]:<6}{str(o.get('action'))[:5]:<6}"
                  f"{str(rem):>8}{(f'{pxd:.2f}' if pxd else '?'):>8}"
                  f"{cost:>10.2f}")
        if len(orders) > 40:
            print(f"  ... and {len(orders) - 40} more")
        print(f"\n  cash held by resting orders: ${held:,.2f} "
              f"across {n_rest} order(s)")

    # ---------------------------------------------------------------- 3
    print("\n" + "-" * 96)
    print("3. OPEN POSITIONS -- money already at risk")
    print("-" * 96)
    d, err = get("/trade-api/v2/portfolio/positions", limit=200)
    if err:
        print(f"  could not read positions: {err}")
    else:
        pos = [p for p in ((d or {}).get("market_positions") or [])
               if p.get("position")]
        print(f"  {len(pos)} open position(s)")
        for p in pos[:30]:
            print(f"    {str(p.get('ticker'))[:44]:<46} "
                  f"position {p.get('position')}   "
                  f"exposure {p.get('market_exposure')}")

    # ---------------------------------------------------------------- 4
    print("\n" + "=" * 96)
    print("READING IT")
    print("=" * 96)
    b = dollars((bal or {}).get("balance")) if bal else None
    if b is not None:
        print(f"  balance field              ${b:,.2f}")
        print(f"  less cash in resting orders ${held:,.2f}")
        print(f"  = actually spendable        ${b - held:,.2f}")
        print()
        if held > 1.0 and (b - held) < 10.0:
            print("  THAT EXPLAINS IT. Resting orders are holding nearly the")
            print("  whole balance, so Kalshi had little or nothing free to")
            print("  authorise a new order against -- while the funding check")
            print("  read the total and saw $99. The orders should be")
            print("  reviewed and the check should read the spendable figure.")
        elif held > 1.0:
            print("  Resting orders hold real money but not enough to explain")
            print("  a rejection on a ~$5 order. Something else is going on.")
        else:
            print("  RESTING ORDERS DO NOT EXPLAIN IT. There is no meaningful")
            print("  cash tied up, so the rejection came from something this")
            print("  read does not cover. Do not invent a reason: the next")
            print("  step is the raw order-rejection response and Kalshi's")
            print("  own documentation on what balance an order is checked")
            print("  against.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read -- and only on request, set -- the target balance allocation.

WHY READ FIRST. All five crypto series trade on exchange shard 2, which
holds $0.00, while the account's $99.29 sits on shard 0. Kalshi authorises
an order against its market's own shard, so T-9 cannot place a bet until
cash is on 2. Automatic rebalancing fixes that permanently: Kalshi checks
every ten seconds and transfers to hold a target split.

The docs for the POST body are not reachable from this session, and
GUESSING A FIELD NAME ON A REQUEST THAT MOVES MONEY IS NOT ACCEPTABLE. So
this reads the current allocation first and prints it raw: whatever shape
comes back is the shape to send, learned from the account rather than
remembered.

  (no argument)   read and print. Changes nothing.
  apply           send the split in SPLIT below, then read it back.

`apply` is deliberately a separate word on the command line. Nothing here
runs by schedule, and the read mode cannot alter anything.
"""
import json
import sys

import requests

import predictor as P

# 85 on the crypto shard where every coin trades, 15 left on 0 rather than
# emptying it -- the House/Senate positions sit off shard 2 and an automated
# sweep interacting with open positions has not been verified.
SPLIT = {2: 85, 0: 15}

PATH = "/trade-api/v2/portfolio/target_balance_allocation"


def call(method, body=None):
    hdrs = P._kalshi_headers(method, PATH)
    if not hdrs:
        return None, "could not sign the request"
    url = P.KALSHI_BASE + PATH.replace("/trade-api/v2", "")
    try:
        if method == "GET":
            r = requests.get(url, headers=hdrs, timeout=15)
        else:
            hdrs["Content-Type"] = "application/json"
            r = requests.post(url, headers=hdrs, json=body, timeout=15)
        if not r.ok:
            return None, f"HTTP {r.status_code} {r.text[:300]}"
        return (r.json() if r.text.strip() else {}), None
    except Exception as e:
        return None, str(e)[:200]


def show_balance(label):
    hdrs = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
    r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance", headers=hdrs,
                     timeout=15)
    if not r.ok:
        print(f"  {label}: could not read balance (HTTP {r.status_code})")
        return
    rows = (r.json() or {}).get("balance_breakdown") or []
    print(f"  {label}:")
    for row in rows:
        print(f"    shard {row.get('exchange_index')}   ${row.get('balance')}")


def main():
    apply = len(sys.argv) > 1 and sys.argv[1].strip().lower() == "apply"
    print("=" * 84)
    print("TARGET BALANCE ALLOCATION" + ("  --  APPLY" if apply
                                         else "  --  READ ONLY"))
    print("=" * 84)

    show_balance("balance by shard, before")

    print("\n  GET current allocation, raw:")
    cur, err = call("GET")
    if err:
        print(f"    FAILED: {err}")
        print("\n    Without the current shape there is nothing to copy, and")
        print("    a guessed body on a money endpoint is not worth the risk.")
        print("    Set the split in Kalshi's clearing portal instead, under")
        print("    the Exchange Indexes settings page.")
        return 1
    print("    " + json.dumps(cur, indent=2, default=str)[:1200])

    if not apply:
        print("\n  READ ONLY. Nothing was changed.")
        print(f"  The split that WOULD be sent: "
              + ", ".join(f"shard {k} -> {v}%" for k, v in SPLIT.items()))
        print("  Re-run with 'apply' to send it.")
        print("=" * 84)
        return 0

    # Build the body in the shape the GET came back in, rather than from
    # memory. If the key is absent the shape is unknown and this stops.
    key = next((k for k in ("allocations", "target_allocations",
                            "balance_allocations")
                if isinstance(cur, dict) and k in cur), None)
    if key is None:
        print("\n  THE RESPONSE HAS NO ALLOCATIONS ARRAY I RECOGNISE.")
        print(f"  top-level keys: {sorted(cur) if isinstance(cur, dict) else type(cur)}")
        print("  Stopping rather than inventing a field name on a money")
        print("  endpoint. Use the clearing portal, or paste this output.")
        return 1

    sample = (cur.get(key) or [None])[0]
    print(f"\n  allocations key: {key!r}   element shape: {sample!r}")
    idx_f = pct_f = None
    if isinstance(sample, dict):
        idx_f = next((f for f in sample if "index" in f.lower()), None)
        pct_f = next((f for f in sample
                      if "percent" in f.lower() or "pct" in f.lower()
                      or "allocation" in f.lower()), None)
    if not idx_f or not pct_f:
        # NAMED BY KALSHI ITSELF. The first attempt sent 'percentage' and
        # was refused 400 missing_parameters with
        #   Allocations[0].Percent  Error:Field validation ... 'required'
        # which names the field and confirms 'exchange_index' was accepted,
        # since it drew no complaint. This is read off the rejection, not
        # guessed a second time.
        idx_f, pct_f = "exchange_index", "percent"
        print(f"  no existing element to copy; using {idx_f!r}/{pct_f!r}, "
              f"the names Kalshi's own 400 named")

    body = {key: [{idx_f: i, pct_f: p} for i, p in SPLIT.items()]}
    if isinstance(cur, dict) and "resting_margin_reservation" in cur:
        # Carry the existing policy through rather than resetting it.
        body["resting_margin_reservation"] = cur["resting_margin_reservation"]
    total = sum(SPLIT.values())
    if total != 100:
        print(f"  REFUSING: the percentages sum to {total}, not 100.")
        return 1

    print("\n  POST body:")
    print("    " + json.dumps(body))
    res, err = call("POST", body)
    if err:
        print(f"\n  THE POST FAILED: {err}")
        print("  Nothing was changed. The error text above is the next clue;")
        print("  do not retry with a different guessed field name.")
        return 1
    print("\n  accepted. response:")
    print("    " + json.dumps(res, indent=2, default=str)[:800])

    print("\n  GET it back, to confirm what is actually stored:")
    back, err = call("GET")
    print("    " + (json.dumps(back, indent=2, default=str)[:800]
                    if not err else f"read-back failed: {err}"))
    print()
    show_balance("balance by shard, immediately after")
    print("\n  Kalshi rebalances on a ten-second cycle, so the money may not")
    print("  have moved yet. Re-run in read mode in a minute to see it land.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

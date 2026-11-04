"""Discover the transfer schema by asking the API, one safe body at a time.

THE ENDPOINT IS CONFIRMED. An empty body to
/trade-api/v2/portfolio/intra_exchange_instance_transfer returned

    HTTP 400 {"code":"invalid_parameters","details":"amount must be positive"}

so the path exists and the field is called `amount`. It named nothing else,
which may mean amount is simply validated first.

THE UNIT IS UNKNOWN, and that is the dangerous part of a transfer. So the
first real probe sends amount=100 and nothing else, which is SAFE BY
CONSTRUCTION on this account:

    if amount is CENTS   -> $1.00, trivial, and toward the shard we want
    if amount is DOLLARS -> $100.00, more than shard 0's $94.29, so the
                            exchange refuses it for insufficient funds

Either answer is informative and neither can move more than a dollar. That
is the whole design: a probe whose worst case is bounded by arithmetic
rather than by trust.

Each body is sent once, the raw response is printed, and nothing is
retried with a mutated guess. Bodies come from PROBE_BODIES as JSON so the
next step is a config change, not a code change.

Places no order. Moves at most $1 by construction.
"""
import json
import os

import requests

import predictor as P

PATH = "/trade-api/v2/portfolio/intra_exchange_instance_transfer"
DEFAULT = '[{"amount": 100}]'
BODIES = json.loads(os.environ.get("PROBE_BODIES") or DEFAULT)


def balances(label):
    h = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
    r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance", headers=h,
                     timeout=15)
    if not r.ok:
        print(f"  {label}: HTTP {r.status_code}")
        return
    print(f"  {label}:")
    for row in (r.json() or {}).get("balance_breakdown") or []:
        print(f"    shard {row.get('exchange_index')}   ${row.get('balance')}")


def main():
    print("=" * 88)
    print("TRANSFER SCHEMA PROBE -- bounded so the worst case is about $1")
    print("=" * 88)
    balances("balances before")

    url = P.KALSHI_BASE + PATH.replace("/trade-api/v2", "")
    for i, body in enumerate(BODIES, 1):
        print(f"\n  PROBE {i}: {json.dumps(body)}")
        h = P._kalshi_headers("POST", PATH)
        if not h:
            print("    could not sign")
            break
        h["Content-Type"] = "application/json"
        r = requests.post(url, headers=h, json=body, timeout=20)
        print(f"    HTTP {r.status_code} {r.text[:500]}")
        if r.ok:
            print("    ACCEPTED -- stopping here and reading the balances, so")
            print("    the effect is observed rather than assumed.")
            break

    print()
    balances("balances after")
    print("\n" + "=" * 88)
    print("  Compare the two readings. A shard that changed by $1.00 means")
    print("  amount is CENTS. A refusal for insufficient funds means it is")
    print("  DOLLARS. No change and a 400 means a field is still missing,")
    print("  and the response above names it or it does not -- either way")
    print("  the next body goes in PROBE_BODIES, not into a guess here.")
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

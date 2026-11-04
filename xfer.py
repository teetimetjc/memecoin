"""Move user-level funds onto an exchange shard. Read first, then transfer.

WHY THIS EXISTS TONIGHT. Every crypto series trades on shard 2. Shard 2
holds $0.34; shard 0 holds $94.29. Kalshi authorises an order against its
own market's shard, so T-9 cannot bet. The target allocation (85/15) is
stored and has not moved a cent in forty minutes, so automatic rebalancing
is not active on this account. A manual app bet moves only what that bet
needs and then spends it, so it never accumulates.

That leaves an explicit transfer.

READS BEFORE IT WRITES, for the same reason as shardalloc: the request
schema is not reachable from this session and guessing a field name on a
money endpoint is not acceptable. The allocation endpoint already taught
this lesson once -- 'percentage' was refused and Kalshi's own 400 named
'percent'. So a rejection here is read, not worked around.

  (no argument)   print the shard balances and the planned transfer only.
  send            perform it, then read the balances back.

KNOWN RISK, stated rather than discovered later: Kalshi's docs warn that
cross-shard movement can run in several NON-ATOMIC steps, so a failure part
way can leave funds in an unexpected place. The amount is therefore kept
small and the balances are read back immediately.

Places no order and changes no strategy setting.
"""
import json
import sys

import requests

import predictor as P

TO_SHARD = int(__import__("os").environ.get("XFER_TO") or 2)
AMOUNT = float(__import__("os").environ.get("XFER_AMOUNT") or 30.0)

CANDIDATES = [
    "/trade-api/v2/portfolio/intra_account_transfer",
    "/trade-api/v2/portfolio/transfer",
]


def balances(label):
    h = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
    r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance", headers=h,
                     timeout=15)
    if not r.ok:
        print(f"  {label}: HTTP {r.status_code}")
        return {}
    out = {}
    print(f"  {label}:")
    for row in (r.json() or {}).get("balance_breakdown") or []:
        try:
            out[int(row["exchange_index"])] = float(row["balance"])
        except (TypeError, ValueError, KeyError):
            continue
    for i in sorted(out):
        print(f"    shard {i}   ${out[i]:,.4f}")
    return out


def main():
    send = len(sys.argv) > 1 and sys.argv[1].strip().lower() == "send"
    print("=" * 84)
    print("INTRA-ACCOUNT TRANSFER" + ("  --  SEND" if send else "  --  DRY"))
    print(f"  plan: move ${AMOUNT:.2f} onto shard {TO_SHARD}")
    print("=" * 84)

    before = balances("balances before")
    src = max((i for i in before if i != TO_SHARD),
              key=lambda i: before[i], default=None)
    if src is None:
        print("\n  no other shard to move from.")
        return 1
    if before.get(src, 0.0) < AMOUNT:
        print(f"\n  shard {src} holds ${before[src]:.2f}, less than the "
              f"${AMOUNT:.2f} requested. Refusing.")
        return 1
    print(f"\n  source shard {src} (${before[src]:,.2f}) -> "
          f"shard {TO_SHARD} (${before.get(TO_SHARD, 0.0):,.2f})")

    if not send:
        print("\n  DRY RUN. Nothing was moved. Re-run with 'send'.")
        print("=" * 84)
        return 0

    body = {"source_exchange_index": src,
            "destination_exchange_index": TO_SHARD,
            "amount_dollars": f"{AMOUNT:.4f}"}
    for path in CANDIDATES:
        print(f"\n  POST {path}")
        print(f"    {json.dumps(body)}")
        h = P._kalshi_headers("POST", path)
        if not h:
            print("    could not sign")
            continue
        h["Content-Type"] = "application/json"
        r = requests.post(P.KALSHI_BASE + path.replace("/trade-api/v2", ""),
                          headers=h, json=body, timeout=20)
        print(f"    HTTP {r.status_code} {r.text[:400]}")
        if r.ok:
            print("\n  accepted.")
            break
        if r.status_code in (400, 422):
            print("\n  REJECTED WITH DETAIL. That error names what is wrong;")
            print("  read it rather than trying another guessed field.")
            break
        if r.status_code == 404:
            print("    not this path; trying the next candidate")
            continue
        break

    print()
    balances("balances after")
    print("\n  If nothing moved, the endpoint or the field names are wrong.")
    print("  Do not retry blind: the error text above is the next step.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

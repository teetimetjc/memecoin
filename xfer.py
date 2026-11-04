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
import re
import sys

import requests

import predictor as P

TO_SHARD = int(__import__("os").environ.get("XFER_TO") or 2)
AMOUNT = float(__import__("os").environ.get("XFER_AMOUNT") or 30.0)

# THE CORRECT PATH, supplied by the account holder. The two I tried before
# (intra_account_transfer, transfer) both 404'd.
PATH = "/trade-api/v2/portfolio/intra_exchange_instance_transfer"


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

    url = P.KALSHI_BASE + PATH.replace("/trade-api/v2", "")

    def post(body):
        h = P._kalshi_headers("POST", PATH)
        if not h:
            return None
        h["Content-Type"] = "application/json"
        return requests.post(url, headers=h, json=body, timeout=20)

    # STEP 1: ASK THE API WHAT IT WANTS. The docs are not reachable from
    # this session, and guessing a field name on a money endpoint is not
    # acceptable -- the allocation endpoint already proved the point, where
    # 'percentage' was refused and Kalshi's own 400 named 'percent'. An
    # empty body cannot move money; it can only be rejected, and the
    # rejection lists the required fields by name.
    print(f"\n  STEP 1 -- probe {PATH} with an empty body, to be told the")
    print("  required fields rather than guess them. This cannot transfer.")
    r = post({})
    if r is None:
        print("    could not sign the request")
        return 1
    print(f"    HTTP {r.status_code} {r.text[:600]}")
    if r.status_code == 404:
        print("\n  404 -- this path does not exist on this account's API.")
        return 1
    if r.ok:
        print("\n  AN EMPTY BODY WAS ACCEPTED. That is unexpected and this")
        print("  stops rather than sending anything else. Check the account.")
        return 1

    names = set(re.findall(r"\.([A-Za-z_]+)' Error:Field validation",
                           r.text or ""))
    if not names:
        print("\n  THE ERROR DOES NOT NAME ANY FIELDS, so there is nothing")
        print("  to build a request from. Stopping rather than guessing.")
        print("  Paste the error above to Kalshi support, or allow")
        print("  docs.kalshi.com in this session's network settings and")
        print("  I will read the schema directly.")
        return 1
    print(f"\n    required fields named by Kalshi: {sorted(names)}")

    if not send:
        print("\n  PROBE ONLY. Nothing was moved. Re-run with 'send'.")
        print("=" * 84)
        return 0

    # STEP 2: build the body from the names the API gave, mapping each to
    # the value it can only sensibly take. Anything unrecognised stops this.
    body = {}
    unmapped = []
    for n in names:
        low = n.lower()
        if "source" in low and ("index" in low or "instance" in low):
            body[n] = src
        elif ("destination" in low or "target" in low or "dest" in low) and \
                ("index" in low or "instance" in low):
            body[n] = TO_SHARD
        elif "cent" in low:                       # amount in cents
            body[n] = int(round(AMOUNT * 100))
        elif "amount" in low or "dollar" in low:
            body[n] = f"{AMOUNT:.4f}"
        else:
            unmapped.append(n)
    if unmapped:
        print(f"\n  UNRECOGNISED REQUIRED FIELD(S): {unmapped}")
        print("  Stopping. A money transfer is not the place to invent a")
        print("  value for a field whose meaning is unknown.")
        return 1

    print(f"\n  STEP 2 -- POST {PATH}")
    print(f"    {json.dumps(body)}")
    r = post(body)
    print(f"    HTTP {r.status_code} {r.text[:600]}")
    if r.ok:
        print("\n  accepted.")
    else:
        print("\n  REJECTED. The error names what is still wrong; read it")
        print("  rather than retrying with a different guess.")

    print()
    balances("balances after")
    print("\n  If nothing moved, the endpoint or the field names are wrong.")
    print("  Do not retry blind: the error text above is the next step.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

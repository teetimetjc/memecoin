"""Move funds between exchange shards, using the DOCUMENTED schema.

WHY THIS EXISTS. Every crypto series trades on exchange shard 2. Deposits
and settlements land on shard 0. Kalshi collateralises an order inside the
matching engine that owns the market, so a crypto order is checked against
shard 2 alone -- the account total is irrelevant. When shard 2 runs dry,
T-9 halts with insufficient_balance while the account shows $90.

WHAT WAS WRONG BEFORE, stated plainly because it cost real money. The
previous version of this file discovered field names by posting an empty
body and reading the rejection. That produced a request with
`destination: "margined"`, and `margined` is NOT a shard -- it is the
Invest (margined) instance. $0.40 was transferred out of trading and into
Invest. The lesson is the one the file already claimed to follow and did
not: read the schema, do not infer it from error text.

THE SCHEMA, from Kalshi's Intra Account Transfer reference:

    POST /trade-api/v2/portfolio/intra_exchange_instance_transfer
      source                      enum  required  event_contract | margined
      destination                 enum  required  event_contract | margined
      amount                      int   required  CENTICENTS (10,000 = $1)
      source_exchange_shard       int   default 0
      destination_exchange_shard  int   default 0
      source_subaccount           int   default 0
      destination_subaccount      int   default 0

So `source`/`destination` choose the INSTANCE (trading vs Invest) and the
`*_exchange_shard` fields choose the SHARD. Moving cash from shard 0 to
shard 2 for trading means event_contract -> event_contract with the shard
fields set. Omitting them is what defaults both ends to shard 0.

AMOUNT IS IN CENTICENTS. $60 is 600000, not 6000 and not 60. The $0.40
incident is also what this unit error looks like, so the conversion is
done once, printed, and read back against the balances.

KNOWN RISK, from the docs rather than discovered later: a cross-shard
transfer runs in UP TO THREE NON-ATOMIC STEPS, and if a later step fails
the earlier ones are not undone, so funds can be left stranded in the
primary account on either shard. Hence: one transfer at a time, balances
read immediately after, and no retry loop.

  (no argument)   print balances and the exact request body. Sends nothing.
  send            perform the transfer, then read the balances back.

Places no order and changes no strategy setting.
"""
import json
import os
import sys
import uuid

import requests

import predictor as P

TO_SHARD = int(os.environ.get("XFER_TO") or 2)
FROM_SHARD = os.environ.get("XFER_FROM")       # default: the richest other
AMOUNT = float(os.environ.get("XFER_AMOUNT") or 60.0)

PATH = "/trade-api/v2/portfolio/intra_exchange_instance_transfer"
CENTICENTS = 10_000                             # per dollar, per the docs


def balances(label):
    h = P._kalshi_headers("GET", "/trade-api/v2/portfolio/balance")
    if not h:
        print(f"  {label}: could not sign the request")
        return {}
    r = requests.get(f"{P.KALSHI_BASE}/portfolio/balance", headers=h,
                     timeout=15)
    if not r.ok:
        print(f"  {label}: HTTP {r.status_code} {r.text[:200]}")
        return {}
    out = {}
    for row in (r.json() or {}).get("balance_breakdown") or []:
        try:
            out[int(row["exchange_index"])] = float(row["balance"])
        except (TypeError, ValueError, KeyError):
            continue
    print(f"  {label}:")
    for i in sorted(out):
        print(f"    shard {i}   ${out[i]:,.4f}"
              + ("   <- crypto trades here" if i == 2 else ""))
    return out


def main():
    send = len(sys.argv) > 1 and sys.argv[1].strip().lower() == "send"
    print("=" * 84)
    print("INTRA-ACCOUNT SHARD TRANSFER" + ("  --  SEND" if send else
                                            "  --  DRY RUN"))
    print("=" * 84)

    before = balances("balances before")
    if not before:
        print("\n  the balance read failed; nothing is attempted.")
        return 1

    if FROM_SHARD not in (None, ""):
        src = int(FROM_SHARD)
    else:
        src = max((i for i in before if i != TO_SHARD),
                  key=lambda i: before[i], default=None)
    if src is None:
        print("\n  no other shard to move from.")
        return 1
    if src == TO_SHARD:
        print(f"\n  source and destination are both {src}. Refusing: the")
        print("  docs say same-shard requests are treated as SUBACCOUNT")
        print("  transfers, which is not what this is for.")
        return 1

    have = before.get(src, 0.0)
    if have < AMOUNT:
        print(f"\n  shard {src} holds ${have:,.2f}, less than the "
              f"${AMOUNT:,.2f} requested. Refusing rather than moving a")
        print("  different amount than was asked for.")
        return 1

    body = {
        "source": "event_contract",
        "destination": "event_contract",
        "amount": int(round(AMOUNT * CENTICENTS)),
        "source_exchange_shard": src,
        "destination_exchange_shard": TO_SHARD,
    }

    print(f"\n  plan: ${AMOUNT:,.2f} from shard {src} "
          f"(${have:,.2f}) to shard {TO_SHARD} "
          f"(${before.get(TO_SHARD, 0.0):,.2f})")
    print(f"  amount in centicents: {body['amount']:,} "
          f"(= ${body['amount'] / CENTICENTS:,.2f}, checked back)")
    print(f"  both ends are event_contract, so this stays in TRADING and")
    print(f"  cannot land in Invest the way the earlier attempt did.")
    print(f"\n  request body:\n    {json.dumps(body)}")

    if not send:
        print("\n  DRY RUN. Nothing was sent. Re-run with 'send'.")
        print("=" * 84)
        return 0

    # client_transfer_id only on the real send, and only once: reusing it
    # is how a retry is made safe, so a fresh one per attempt is correct
    # and a 409 would mean this exact transfer was already accepted.
    body["client_transfer_id"] = str(uuid.uuid4())

    h = P._kalshi_headers("POST", PATH)
    if not h:
        print("\n  could not sign the request.")
        return 1
    h["Content-Type"] = "application/json"
    url = P.KALSHI_BASE + PATH.replace("/trade-api/v2", "")
    r = requests.post(url, headers=h, json=body, timeout=20)
    print(f"\n  POST {PATH}")
    print(f"    HTTP {r.status_code} {r.text[:600]}")

    if r.status_code == 409:
        print("\n  409 -- a transfer with this client_transfer_id was")
        print("  already accepted. Nothing new was created.")
    elif r.ok:
        print("\n  accepted. The docs say it settles ASYNCHRONOUSLY, so the")
        print("  read below may still show the old figures.")
    else:
        print("\n  REJECTED. Read the error above; do not retry with a")
        print("  different guess at the fields.")

    print()
    after = balances("balances after")
    if after and before:
        d_src = after.get(src, 0.0) - before.get(src, 0.0)
        d_dst = after.get(TO_SHARD, 0.0) - before.get(TO_SHARD, 0.0)
        print(f"\n  shard {src} moved {d_src:+,.4f}")
        print(f"  shard {TO_SHARD} moved {d_dst:+,.4f}")
        tot_b = sum(before.values())
        tot_a = sum(after.values())
        print(f"  total {tot_b:,.4f} -> {tot_a:,.4f} "
              f"({tot_a - tot_b:+,.4f})")
        if abs((tot_a - tot_b)) > 0.005:
            print("\n  THE TOTAL CHANGED. A shard-to-shard move should not")
            print("  change the total, so money left the trading instance.")
            print("  Check the Invest balance before sending anything else.")
    print("\n  CROSS-SHARD TRANSFERS ARE NOT ATOMIC. If the deltas above do")
    print("  not match the plan, funds may be mid-flight or stranded; read")
    print("  the balances again before any second attempt.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

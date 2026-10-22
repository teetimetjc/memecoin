"""What the measurement run would have paid at a flat $5 throughout.

THE QUESTION. The run was bet at $4, then $5, then $6, with a $2 ladder
rung in between, and the $6 era gave back everything the other two made.
Stake cannot create or destroy an edge -- it scales whatever is there --
so the obvious thing to ask is what the same bets, at the same prices,
with the same outcomes, would have come to at one flat size.

HOW EACH BET IS REBUILT, and nothing more than this is changed:

  THE FILL PRICE IS THE REAL ONE. Each contract is repriced at the VWAP the
  order actually got, quantity-weighted across its pieces, not at the quote
  and not at the limit. The whole point is to hold execution fixed and vary
  only the size.

  THE CONTRACT COUNT IS REBUILT THE WAY live.py BUILDS IT: contracts =
  floor(stake / limit), where the limit is ceil(entry + the slip buffer).
  That is integer arithmetic, so $5 does not simply scale $4 by 1.25 -- at a
  93c limit, $4 buys 4 contracts and $5 buys 5, which is 1.25x, but at a 97c
  limit $4 buys 4 and $5 buys 5 as well. The rounding matters and is kept.

  THE FEE IS RECOMPUTED, not scaled: 0.07 * n * p * (1-p) at the new
  contract count, which is the formula verified against 162 of 163 fills.

  THE OUTCOME IS UNCHANGED. A winning contract pays $1.00. Settlements come
  from Kalshi and are not re-derived.

THE ASSUMPTION THIS RESTS ON, stated because it is the whole weakness: that
the same fill price was available at the larger size. For a one-contract
difference in a book whose median depth at the best ask is 132 contracts,
that is reasonable. It would NOT be reasonable for a big step up, and this
file refuses to pretend otherwise -- it is a what-if about sizing, not a
promise about a stake nobody has run.

WHAT IT CANNOT MODEL. The stake ladder would have fired differently, the
drop guard and the per-window cap would have seen the same signals but a
different exposure, and the day-stop arithmetic would have moved. Those are
unchanged here, so this is "the same bets at one size", not "what the bot
would have done if configured at $5 from the start".

Read-only.
"""
import collections
import math

import live
import tally as T

RESTART = "2026-10-14 00:00"
FLAT = 5.0
T9_STAKES = ("2", "4", "5", "6", "7")


def fee_dollars(n, px_dollars):
    return 0.07 * n * px_dollars * (1.0 - px_dollars)


def main():
    bets = T.rows_since(RESTART)
    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break

    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    for _tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            x["_share"] = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0

    fills = T.actual_costs()
    rows_out = []
    skipped = 0
    for b in bets:
        if str(b.get("side") or "").upper() not in ("UP", "YES"):
            continue
        stake = str(T.stake_of(b) or "?")
        if stake not in T9_STAKES:
            continue
        s = setts.get(b["ticker"])
        if s is None:
            continue
        f = fills.get(b["oid"])
        n_real = int(b["contracts"] or 0)
        if not n_real or b["entry"] is None:
            skipped += 1
            continue
        # The price actually paid per contract, quantity-weighted.
        if f and f.get("qty") and sum(f["qty"]):
            qsum = sum(f["qty"])
            vwap = sum(p * q for p, q in zip(f["px"], f["qty"])) / qsum
        else:
            skipped += 1
            continue
        # vwap comes back in dollars per contract.
        px = float(vwap)
        if not (0 < px < 1):
            skipped += 1
            continue
        won = (s["rev"] * b.get("_share", 1.0)) > 0

        limit_c = min(99.0, math.ceil(b["entry"] + live.SLIP_BUFFER_CENTS))
        n5 = int(FLAT / (limit_c / 100.0))
        if n5 < 1:
            skipped += 1
            continue

        cost_real = n_real * px + fee_dollars(n_real, px)
        rev_real = n_real * 1.0 if won else 0.0
        cost5 = n5 * px + fee_dollars(n5, px)
        rev5 = n5 * 1.0 if won else 0.0
        rows_out.append(dict(ts=b["ts"], stake=stake, won=won, px=px,
                             n_real=n_real, n5=n5,
                             net_real=rev_real - cost_real,
                             net5=rev5 - cost5,
                             cost_real=cost_real, cost5=cost5))

    if not rows_out:
        print("  nothing to rebuild")
        return 0

    print("=" * 96)
    print(f"THE SAME BETS AT A FLAT ${FLAT:.0f} -- same fills, same outcomes, "
          "only the size changed")
    print("=" * 96)
    print(f"  {len(rows_out)} settled YES bets rebuilt"
          + (f", {skipped} skipped for want of a usable fill" if skipped
             else ""))
    print()

    def line(label, key_net, key_cost, key_n):
        net = sum(r[key_net] for r in rows_out)
        cost = sum(r[key_cost] for r in rows_out)
        con = sum(r[key_n] for r in rows_out)
        print(f"  {label:<22} {con:>5} contracts  ${cost:>8.2f} staked  "
              f"net ${net:>+7.2f}  ROI {100*net/cost if cost else 0:>+6.2f}%")
        return net

    a = line("as actually bet", "net_real", "cost_real", "n_real")
    b5 = line(f"flat ${FLAT:.0f}", "net5", "cost5", "n5")
    print()
    print(f"  DIFFERENCE  ${b5 - a:+.2f}")
    print()
    print("  ROI barely moves and that is the point: the win rate and the")
    print("  prices are identical, so a flat stake changes the DOLLARS and")
    print("  not the edge. If the flat-$5 net is higher, it is because the")
    print("  winning stretches were bet small and the losing one was bet")
    print("  big -- an accident of when the size changed, not a reason to")
    print("  believe $5 is a better number.")
    print()

    print("-- WHAT EACH ERA CONTRIBUTED, actual against flat " + "-" * 44)
    g = collections.defaultdict(list)
    for r in rows_out:
        g[r["stake"]].append(r)
    for k in sorted(g):
        gg = g[k]
        na = sum(r["net_real"] for r in gg)
        nf = sum(r["net5"] for r in gg)
        print(f"    ${k:<3} {len(gg):>4} bets   actual ${na:>+7.2f}   "
              f"at flat ${FLAT:.0f} ${nf:>+7.2f}   change ${nf-na:>+7.2f}")
    print()
    print("  THE ASSUMPTION: that the same fill price was available at the")
    print("  larger size. For a one or two contract difference against a")
    print("  median 132 resting at the best ask that is reasonable. It would")
    print("  not be for a big step up, and this says nothing about a stake")
    print("  nobody has run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

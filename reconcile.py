"""Does the +$35 the sheet implies match what Kalshi says the account holds?

WHY THIS IS NOT PARANOIA. The P&L in this project is RECONSTRUCTED: sheet
rows for what was bet, the fills endpoint for what was paid, the settlements
endpoint for what came back. Three sources, joined on order id and ticker,
and every join is a chance to double-count or to miss.

It has already gone wrong twice, both times in the flattering direction:

  THE FILLS PAGINATION BUG. actual_costs read one page of fills with no
  cursor, so an order whose pieces straddled the window had part of its cost
  counted as all of it. A bet that cost $3.76 was recorded at $0.94 and
  showed +$3.06 instead of +$0.24. The record re-wrote itself upward between
  two readings on the same day with no bet having resolved.

  THE COST COLUMN. live.py writes contracts times the LIMIT, not the fill, so
  reading it overstates spend -- which flatters nothing, but the first tally
  built on it reported +$4.08 where Kalshi said +$4.83, and a $0.75 error in
  either direction on a $35 claim is a tenth of the whole result.

THE ONE NUMBER THAT CANNOT DRIFT is the account balance. Kalshi computes it,
it needs no join, and it reflects every fee, every settlement and every bet
this project does not know about. So:

    expected balance = starting cash + reconstructed net - money still at risk

and if that disagrees with Kalshi's balance by more than rounding, the
reconstructed net is wrong and the +$35 should not be quoted.

OPEN POSITIONS ARE THE SUBTLETY. Cash spent on a bet that has not settled is
gone from the balance but is not a loss, so it has to be added back at cost.
Positions are read from the portfolio rather than inferred from the sheet,
because a position the sheet does not know about is exactly the kind of thing
this check exists to surface.

WHAT A MISMATCH WOULD MEAN, in order of likelihood: a settlement not yet
counted, a bet placed outside this record (the T-3 and oil sessions bet too),
a deposit or withdrawal, or a real double-count. The output lists the gap in
dollars so it can be chased rather than argued about.

STARTING CASH IS AN INPUT, not a measurement -- this project has no record of
the opening balance, so it is supplied and the arithmetic is run in reverse:
the file reports what the starting balance MUST have been for the net to be
right, and that number is either plausible or it is not.

Read-only. A signed GET and nothing else.
"""
import os

import tally as T

START = "2026-10-07 15:49"


def main():
    import predictor as P

    # ---- what Kalshi says -------------------------------------------------
    bal = None
    try:
        import control
        bal = control.fetch_balance()
    except Exception as e:
        print(f"  could not read the balance: {str(e)[:90]}")
    pos_cost = 0.0
    n_pos = 0
    try:
        d = T.get("/portfolio/positions", limit=200)
        for p in ((d or {}).get("market_positions") or []):
            q = T._f(p.get("position")) or 0
            if q:
                n_pos += 1
                # Cost basis as the exchange reports it, in dollars.
                c = (T._f(p.get("market_exposure"))
                     or T._f(p.get("total_traded")) or 0)
                pos_cost += abs(c) / 100.0
    except Exception as e:
        print(f"  could not read positions: {str(e)[:90]}")

    # ---- what the record claims ------------------------------------------
    bets = T.rows_since(START)
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
    staked = revenue = 0.0
    n_settled = n_open = 0
    open_cost = 0.0
    no_fill = 0
    for b in bets:
        f = fills.get(b["oid"])
        if f and f.get("cost"):
            cost = f["cost"]
        else:
            cost = b["limit_cost"] or 0.0
            no_fill += 1
        s = setts.get(b["ticker"])
        if s is None:
            n_open += 1
            open_cost += cost
            continue
        n_settled += 1
        staked += cost
        revenue += s["rev"] * b.get("_share", 1.0)

    net = revenue - staked

    print("=" * 96)
    print("DOES THE RECONSTRUCTED P&L MATCH THE ACCOUNT?")
    print("=" * 96)
    print(f"  bets in the record since {START}: {len(bets)}")
    print(f"    {n_settled} settled, {n_open} with no settlement yet")
    if no_fill:
        print(f"    {no_fill} priced from the sheet's LIMIT because no "
              "verified fill was found")
        print("      (those are the ones most likely to be wrong, in either "
              "direction)")
    print()
    print(f"  staked on settled bets   ${staked:>9.2f}")
    print(f"  returned by settlement   ${revenue:>9.2f}")
    print(f"  RECONSTRUCTED NET        ${net:>+9.2f}")
    print()
    print(f"  cash still at risk in unsettled bets  ${open_cost:>8.2f} "
          f"({n_open} bets)")
    print(f"  exchange-reported open position cost  ${pos_cost:>8.2f} "
          f"({n_pos} positions)")
    print()

    if bal is None:
        print("  NO BALANCE READ, so this is unreconciled and the net above")
        print("  should be treated as the record's claim rather than as fact.")
        print("=" * 96)
        return 1

    print(f"  KALSHI SAYS the balance is ${bal:,.2f}")
    implied_start = bal + pos_cost - net
    print(f"  so the starting cash must have been "
          f"${implied_start:,.2f}")
    print(f"    = balance ${bal:,.2f} + open positions ${pos_cost:,.2f} "
          f"- net ${net:+,.2f}")
    print()
    print("  IS THAT PLAUSIBLE? The run has been described throughout as")
    print("  roughly a $106 bankroll. If the implied figure above is near")
    print("  that, the reconstruction is sound and the net can be quoted. If")
    print("  it is far off, something is being double-counted or missed and")
    print("  the net cannot be.")
    print()
    gap = implied_start - 106.0
    print(f"  against the $106 figure the project has used: {gap:+.2f}")
    if abs(gap) <= 5:
        print("  CLOSE ENOUGH. The reconstruction reconciles.")
    else:
        print("  THAT IS A REAL GAP. Likely causes, most likely first:")
        print("    - the T-3 and oil sessions also bet, and their bets are in")
        print("      the same account but may not all be in this record's")
        print("      stake filter")
        print("    - a settlement that has not posted yet")
        print("    - a deposit or withdrawal the project never saw")
        print("    - a genuine double-count in the fills/settlements join")
        print("  Until it is explained, the net above is UNVERIFIED.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Return per dollar staked, at every price. Is there anything better than 1.8%?

Return per dollar staked is edge divided by price, so it is largest where
contracts are cheapest: two points of mispricing is 1.6% at 92c and 18% at
10c. If an edge of the same size existed across the board, the cheap end
would be the only sensible place to trade. The question this answers is
whether one does.

THE WHOLE CURVE IS PRINTED, EVERY BAND, IN PRICE ORDER. Nothing is ranked and
nothing is selected. A survey that reports its best cell is a search wearing a
survey's clothes, and that is how this project produced seventeen dead leads.

THE FITTED AND UNFITTED HALVES ARE SEPARATED, and the second is the one worth
reading. The five major series were searched exhaustively to find the current
rules, so a good-looking band there may be the residue of that search. The
thirteen other fifteen-minute series were never looked at while anything was
chosen, so their curve is a genuine out-of-sample picture of where money is
and is not made.

WHAT A GOOD CELL WOULD AND WOULD NOT MEAN. Finding a band with a better
return per dollar staked than the 1.8% the live rule earns would be a
candidate, not a finding: it would still have to be written down, frozen, and
scored forward before any money went near it, exactly as the two current
specs were. The honest use of this table is to see the SHAPE -- where the
market charges more than it mispricies -- rather than to pick its maximum.

Every bet is $4, whole contracts, the verified fee 0.07*C*P*(1-P), entered at
T-9 and held to settlement. Both sides are pooled: a contract bought at 12c
counts the same whether it was a cheap YES or a cheap NO.

Read-only.
"""

import collections
import sys

import score as S

STAKE = 4.0
ENTRY = S.ENTRY
EDGES = [0.02, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50,
         0.60, 0.70, 0.80, 0.85, 0.90, 0.94, 0.98]
MIN_BETS = 300


def load_all(tab):
    import predictor as P
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    try:
        rows = sh.worksheet(tab).get_all_values()
    except Exception as e:
        print(f"  no {tab} tab ({str(e)[:60]})")
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        res = str(c(r, "Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        b, a = S._f(c(r, f"bid{ENTRY}")), S._f(c(r, f"ask{ENTRY}"))
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        out.append(dict(ser=str(c(r, "Series")), ct=str(c(r, "Close Time")),
                        bid=b, ask=a, yes=(res == "yes")))
    return out


def contracts_bet(px, won):
    c = int(STAKE / px)
    if c < 1:
        return None
    cost = c * px + S.fee(c, px)
    return ((c if won else 0) - cost), cost, c


def every_bet(rows):
    """One bet per side per market, at what that side actually costs."""
    out = []
    for m in rows:
        for px, won in ((m["ask"], m["yes"]), (1.0 - m["bid"], not m["yes"])):
            if not (0.0 < px < 1.0):
                continue
            r = contracts_bet(px, won)
            if r is None:
                continue
            out.append((r[0], m["ct"], px, won, r[1], r[2]))
    return out


def curve(label, rows):
    bets = every_bet(rows)
    print("\n" + "-" * 112)
    print(f"  {label}   {len(rows):,} markets, {len(bets):,} priced bets")
    print("-" * 112)
    print(f"  {'price band':<14} {'bets':>7} {'contracts':>10} {'won':>7} "
          f"{'needed':>7} {'$/bet':>9} {'staked':>11} {'per $1':>8} {'t':>6}")
    for i in range(len(EDGES) - 1):
        lo, hi = EDGES[i], EDGES[i + 1]
        sel = [b for b in bets if lo <= b[2] < hi]
        if not sel:
            continue
        n = len(sel)
        profit = sum(b[0] for b in sel)
        cost = sum(b[4] for b in sel)
        ctr = sum(b[5] for b in sel) / n
        won = sum(1 for b in sel if b[3]) / n
        px = sum(b[2] for b in sel) / n
        c = max(1, int(STAKE / px))
        need = (c * px + S.fee(c, px)) / c
        g = collections.defaultdict(list)
        for b in sel:
            g[b[1]].append(b[0])
        se = S.cse(g, n)
        t = (profit / n / se) if se else 0.0
        roi = 100.0 * profit / cost if cost else 0.0
        thin = "" if n >= MIN_BETS else "  thin"
        print(f"  {100*lo:>3.0f}-{100*hi:<3.0f}c      {n:>7,} {ctr:>10.0f} "
              f"{100*won:>6.1f}% {100*need:>6.1f}% ${profit/n:>+8.3f} "
              f"${cost:>10,.0f} {roi:>+7.2f}% {t:>+6.1f}{thin}")


def main():
    rows = load_all("M15H")
    if not rows:
        print("no history")
        return 1
    fit = [r for r in rows if r["ser"] in S.MAJORS]
    unfit = [r for r in rows if r["ser"] not in S.MAJORS]

    print("=" * 112)
    print("RETURN PER DOLLAR STAKED, BY PRICE -- the whole curve, nothing "
          "selected")
    print("=" * 112)
    print(f"  ${STAKE:.0f} a bet, whole contracts, verified fee, T-{ENTRY} to "
          "settlement, both sides pooled.")
    print("  'per $1' is profit over cash actually committed -- the rate the")
    print("  live rule earns 1.8% of. 'contracts' shows the leverage: $4 buys")
    print("  4 contracts at 92c and 40 at 10c, so the cheap end moves faster")
    print("  in both directions.")

    curve("NEVER FITTED -- 13 series, never searched (read this one)", unfit)
    curve("THE FIVE THE RULES WERE BUILT ON -- searched exhaustively", fit)

    print("\n" + "=" * 112)
    print("  Any band that looks good here is a CANDIDATE, not a finding. The")
    print("  two live specs earned their status by being written down before")
    print("  the data that scores them existed; nothing in this table has.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    sys.exit(main())

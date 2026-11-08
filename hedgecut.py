"""Would buying the cheap opposite side as a hedge have helped? Read-only.

THE IDEA, as asked: place the $5 favourite as usual, and once it fills, buy
a small amount of the OTHER side -- about ten cents' worth -- so a losing
window costs less.

WHY IT DESERVES A MEASUREMENT RATHER THAN AN ARGUMENT. The arithmetic says
the hedge must lose money on average, because this strategy's whole edge is
that the longshot is OVERPRICED, and the hedge buys exactly that longshot.
But that argument is about the average. It does not answer whether the
hedge pays off on the PARTICULAR windows that lost, and losses here are not
spread evenly -- legs sharing a close window resolve together about 96% of
the time. So the real record gets replayed, bet by bet.

HOW THE HEDGE IS PRICED, which is the whole difficulty. The bet log records
the entry price of the side that was BOUGHT. It does not record what the
other side cost. Kalshi runs ONE book, so a resting YES bid at p IS a NO
ask at 1-p:

    main side YES  ->  hedge is NO,  and it costs (1 - yes_bid)
    main side NO   ->  hedge is YES, and it costs (yes_ask)

The yes_bid is not in the bet log. It IS in the collector sheet at T-9, so
each bet is joined to its collector row by ticker and the real quote is
used. Where no collector row exists, the fallback is (1 - entry), which
assumes a zero spread and therefore UNDERSTATES what the hedge cost. Those
bets are counted separately and the headline is reported both ways, because
a hedge study that quietly prices the hedge too cheaply would flatter the
idea it is testing.

THE FEE IS CHARGED, at Kalshi's real shape: 0.07 * n * p * (1-p), rounded
UP at the order level. On a ten-cent hedge that rounding is most of the
cost -- a single contract pays a whole cent on a nine-cent stake, about
11%. Ignoring it would make a losing idea look marginal.

DEPTH IS REPORTED, NOT ASSUMED. The hedge has to actually be buyable. The
collector stores the size behind each quote, and the standing finding of
this project is that the extremes are thin -- the 95/5 study found a median
of ONE contract on the cheap side. A hedge that cannot be filled is not a
hedge, so the depth distribution is printed beside the returns.

WHAT THIS CANNOT SEE, stated rather than discovered later:

  - The hedge is priced at the T-9 quote, the same instant as the main bet.
    The idea was to buy it AFTER the main bet fills, seconds later, by which
    time the price has moved -- and it has moved because the main order just
    took liquidity, so the real hedge would cost at least this much, not
    less.
  - A second order per window doubles the order count, and the fee rounds up
    per order. That is charged.
  - No slippage is modelled on the hedge beyond the quoted ask.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import os
import statistics as st

import predictor as P
import collect15 as C
import oddsmath as M
import tally as T
import coincut as CC

# Hedge sizes to test, in contracts. 0 is the unhedged record, and 'full'
# below adds the same number of contracts as the main bet -- the version
# that buys both sides outright.
SIZES = tuple(int(x) for x in
              (os.environ.get("HEDGE_SIZES") or "1,2,3").split(",")
              if x.strip())
OFFSET = int(os.environ.get("HEDGE_OFFSET") or 9)      # T-9, the live entry


def f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def collector_quotes():
    """ticker -> (yes_bid, yes_ask, bidsz, asksz) at T-OFFSET, as fractions."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(C.SHEET).get_all_values()
    if len(rows) < 2:
        return {}
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = {}
    for r in rows[1:]:
        tk = str(g(r, "Ticker")).strip()
        if not tk:
            continue
        yb = f(g(r, f"bid{OFFSET}"))
        ya = f(g(r, f"ask{OFFSET}"))
        if yb is None or ya is None:
            continue
        out[tk] = (yb, ya, f(g(r, f"bidsz{OFFSET}")),
                   f(g(r, f"asksz{OFFSET}")))
    return out


def hedge_price(bet, quotes):
    """(cost per hedge contract, depth, exact?) -- the OPPOSITE side's ask."""
    side = str(bet.get("side") or "").strip().upper()
    q = quotes.get(bet["ticker"])
    if q:
        yb, ya, bsz, asz = q
        if side.startswith("Y"):
            # hedge is NO; a resting YES bid at yb IS a NO ask at 1-yb,
            # and sweeping it consumes the resting YES bids.
            return (1.0 - yb), bsz, True
        if side.startswith("N"):
            return ya, asz, True
    # FALLBACK: no collector row. (1 - entry) assumes a zero spread, so it
    # prices the hedge too cheaply. Flagged, counted, reported separately.
    e = f(bet.get("entry"))
    if e is None:
        return None, None, False
    return max(0.0, 1.0 - e / 100.0), None, False


def replay(bets, quotes, size, full=False):
    """Net dollars and ROI with `size` hedge contracts per bet."""
    staked = returned = 0.0
    priced = skipped = 0
    for b in bets:
        n = int(b["contracts"] or 0)
        cost = b["cost"] or 0.0
        rev = b["rev"] or 0.0
        staked += cost
        returned += rev
        if size <= 0 and not full:
            continue
        hp, _depth, _exact = hedge_price(b, quotes)
        if hp is None or not (0.0 < hp < 1.0):
            skipped += 1
            continue
        k = n if full else size
        if k <= 0:
            continue
        hcost = k * hp + M.fee_cents(k, hp) / 100.0
        staked += hcost
        # The hedge pays $1 per contract exactly when the main bet lost.
        if rev <= 0:
            returned += k * 1.0
        priced += 1
    net = returned - staked
    roi = 100.0 * net / staked if staked else 0.0
    return net, roi, staked, priced, skipped


def main():
    print("=" * 100)
    print("HEDGING THE FAVOURITE WITH THE CHEAP OTHER SIDE")
    print(f"  Replayed on the real record since {CC.START} UTC, at the T-"
          f"{OFFSET} quote. Read-only.")
    print("=" * 100)

    bets, _setts = CC.load()
    if not bets:
        print("  no placed bets in the record")
        return 1
    bets, why = CC.keep(bets)
    if CC.STAKES:
        before = len(bets)
        bets = [b for b in bets
                if any(abs((T.stake_of(b) or -1) - v) < 0.01
                       for v in CC.STAKES)]
        print(f"\n  STAKE FILTER "
              f"{'/'.join(f'${v:.0f}' for v in CC.STAKES)}: "
              f"{before} -> {len(bets)} bet(s)")
    settled = [b for b in bets if b["rev"] is not None]
    print(f"  {len(settled)} settled bet(s) to replay")
    if not settled:
        print("  nothing settled to replay")
        return 1

    quotes = collector_quotes()
    print(f"  {len(quotes)} collector row(s) carry a T-{OFFSET} quote")

    # HOW MANY HEDGES ARE PRICED FROM A REAL QUOTE. If this is low the whole
    # table rests on the zero-spread fallback and should not be trusted.
    exact = sum(1 for b in settled if hedge_price(b, quotes)[2])
    print(f"  {exact} of {len(settled)} hedges priced from a REAL two-sided "
          f"quote")
    print(f"  {len(settled) - exact} fall back to (1 - entry), which assumes")
    print(f"     a zero spread and so prices the hedge TOO CHEAPLY")

    # ---------------------------------------------------------------- depth
    deps = [d for b in settled
            for _p, d, ex in [hedge_price(b, quotes)]
            if ex and d is not None]
    if deps:
        deps.sort()
        print(f"\n  depth on the hedge side at entry: median "
              f"{deps[len(deps) // 2]:.0f} contract(s), "
              f"10th pct {deps[max(0, len(deps) // 10)]:.0f}, "
              f"min {deps[0]:.0f}")
        thin = sum(1 for d in deps if d < 1)
        if thin:
            print(f"  {thin} window(s) had NOTHING resting on the hedge side")
    else:
        print("\n  no depth figures available on the hedge side")

    hps = [p for b in settled
           for p, _d, ex in [hedge_price(b, quotes)] if ex and p]
    if hps:
        print(f"  the hedge cost a mean of {100 * st.mean(hps):.2f}c per "
              f"contract (median {100 * st.median(hps):.2f}c)")

    # --------------------------------------------------------------- tables
    base_net, base_roi, base_stake, _, _ = replay(settled, quotes, 0)
    print(f"\n{'-' * 100}")
    print("WHAT EACH HEDGE SIZE WOULD HAVE RETURNED")
    print("-" * 100)
    print(f"  {'rule':<34}{'staked $':>12}{'net $':>12}{'ROI':>10}"
          f"{'vs unhedged':>14}")
    print(f"  {'no hedge (the live record)':<34}{base_stake:>12,.2f}"
          f"{base_net:>+12.2f}{base_roi:>+9.2f}%{'--':>14}")
    for s in SIZES:
        net, roi, stake, priced, skipped = replay(settled, quotes, s)
        print(f"  {f'+{s} contract(s) of the other side':<34}"
              f"{stake:>12,.2f}{net:>+12.2f}{roi:>+9.2f}%"
              f"{roi - base_roi:>+13.2f}%")
    net, roi, stake, priced, skipped = replay(settled, quotes, 0, full=True)
    print(f"  {'FULL hedge (both sides, equal n)':<34}"
          f"{stake:>12,.2f}{net:>+12.2f}{roi:>+9.2f}%"
          f"{roi - base_roi:>+13.2f}%")
    if skipped:
        print(f"\n  {skipped} bet(s) could not be priced and carry no hedge")

    # ------------------------------------------------- the win/lose split
    # The point of a hedge is what it does to the BAD outcome, so that is
    # reported on its own rather than blended into one ROI.
    wins = [b for b in settled if (b["rev"] or 0) > 0]
    losses = [b for b in settled if (b["rev"] or 0) <= 0]
    print(f"\n{'-' * 100}")
    print("WHERE THE MONEY MOVES -- the hedge trades winners for losers")
    print("-" * 100)
    print(f"  {len(wins)} winning bet(s), {len(losses)} losing bet(s)")
    for s in SIZES:
        wn, _, _, _, _ = replay(wins, quotes, s)
        ln, _, _, _, _ = replay(losses, quotes, s)
        wb, _, _, _, _ = replay(wins, quotes, 0)
        lb, _, _, _, _ = replay(losses, quotes, 0)
        print(f"  +{s}: winners {wb:+8.2f} -> {wn:+8.2f} "
              f"({wn - wb:+7.2f})    "
              f"losers {lb:+8.2f} -> {ln:+8.2f} ({ln - lb:+7.2f})")

    # ------------------------------------------------------------- halves
    print(f"\n{'-' * 100}")
    print("BOTH CHRONOLOGICAL HALVES")
    print("-" * 100)
    g = sorted(settled, key=lambda b: b["ts"])
    mid = len(g) // 2
    for label, part in (("first half", g[:mid]), ("second half", g[mid:])):
        _, r0, _, _, _ = replay(part, quotes, 0)
        bits = []
        for s in SIZES:
            _, rs, _, _, _ = replay(part, quotes, s)
            bits.append(f"+{s}: {rs:+6.2f}%")
        print(f"  {label:<12} n {len(part):>4}   "
              f"no hedge {r0:+6.2f}%   " + "   ".join(bits))

    print("\n" + "=" * 100)
    print("  HOW TO READ IT.")
    print()
    print("  A FULL HEDGE IS A GUARANTEED LOSS BY CONSTRUCTION, not by bad")
    print("  luck: buying both sides of one market costs more than the $1 it")
    print("  is certain to pay, and the fee is charged on both orders. If")
    print("  that row is not clearly negative, this file has a bug.")
    print()
    print("  A PARTIAL HEDGE IS INSURANCE, and the question is only whether")
    print("  the premium is fair. It is bought on the side the")
    print("  favourite-longshot bias says is overpriced -- the same bias this")
    print("  strategy exists to harvest -- so the expected premium is")
    print("  negative at exactly the rate the main bet is positive.")
    print()
    print("  AND THE DEPTH ABOVE MAY MATTER MORE THAN THE RETURNS. A hedge")
    print("  quoted at 8c with one contract behind it is not a hedge that")
    print("  could have been placed at size.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Buy the cheap NO against a 90c+ favourite and sell at a fixed multiple.

THE RULE, as proposed: when YES is 90c or more at T-9, buy NO (which costs
about 10c) and sell it the moment it has doubled. Risk a few dollars to make
the same again, many times over, instead of risking $9.20 to win 80c.

THIS IS THE SAME TRADE longshot.py measured from the other side, and that
run is the reason this one is worth doing. It found the cheap side reaches a
best bid of 18.5c against a 7.2c entry -- it really does more than double --
and that selling the exact high of every market returned +130% ROI against
-82% for holding. But the exact high is hindsight nobody has, and the honest
next question was what a FIXED rule earns. That is this.

WHAT IS DIFFERENT FROM A HINDSIGHT TEST. The walk is forward, minute by
minute from T-8 to T-1. The first minute the NO bid reaches the target, the
position is sold there and the market is done. No later price is consulted.
Anything that never reaches the target is held to settlement, which is where
the losses live.

SIDES PRICED HONESTLY. Buying NO pays the NO ask, which is one minus the YES
bid. Selling NO receives the NO bid, which is one minus the YES ask. Using
the mid for both would manufacture roughly a cent a leg out of nothing.

THE FEE IS THE REASON TO DOUBT THIS BEFORE THE DATA ARRIVES. At 10c the fee
is 0.07*0.10*0.90 = 0.63c per contract, which is 6.3% of a 10c stake, and an
exit pays it again at the higher price. The favourite trade pays 0.4%.

AND THE THING THIS CANNOT SEE, which killed the same idea from the YES side:
depth. $5 at 10c is fifty contracts, and the book at these prices has been
observed holding four or five. A test that fills fifty contracts at the
quoted bid is describing a trade nobody can place.

Read-only.
"""
import collections, statistics as st, sys, time
import backfill as B
import score as S

# ENTRY OFFSETS SWEPT, because T-9 was never chosen for this trade. It is
# where the FAVOURITE rule enters -- the point at which a 90c price was
# judged informative enough to back. A fade asks a different question: how
# long does the price need to reverse? Nine minutes is an inherited number
# and there is no reason to think it is right here.
#
# The trade-off runs both ways and the sweep is the only way to see it.
# Earlier, the favourite is less extreme so NO costs more and a double is a
# bigger ask. Later, NO is cheaper so a double is easier, but there is less
# time to get there.
ENTRIES = (14, 12, 9, 6, 4)
YES_LO, YES_HI = 0.90, 0.96     # the favourite band, as the live rule used
STAKE = 5.0
TARGETS = (1.5, 2.0, 3.0)
MAX_SPREAD = 0.06
PER_SERIES = 500
BUDGET = int(sys.argv[1]) if len(sys.argv) > 1 else 2400


def fee(c, p):
    return 0.07 * c * p * (1 - p)


def path(series, ticker, close):
    """{offset: (yes_bid, yes_ask)} for T-1..T-12, tradeable quotes only."""
    end = B.ts(close)
    if end is None:
        return {}
    d, _e = B.get(f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
                  start_ts=int(end) - 13 * 60, end_ts=int(end),
                  period_interval=1)
    out = {}
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or t >= end:
            continue

        def g(k):
            v = c.get(k)
            return B._f(v.get("close_dollars")) if isinstance(v, dict) else None

        b, a = g("yes_bid"), g("yes_ask")
        if b is None or a is None or not (0 < b <= a < 1):
            continue
        if (a - b) > MAX_SPREAD:
            continue
        off = int(round((end - t) / 60.0))
        if 1 <= off <= 12:
            out[off] = (b, a)
    return out


def main():
    print("=" * 96)
    print("FADE THE FAVOURITE: buy NO at ~10c, sell at a fixed multiple")
    print(f"  trigger: YES {100*YES_LO:.0f}-{100*YES_HI:.0f}c at T-{ENTRY},"
          f"  ${STAKE:.0f} a bet, walk forward T-8..T-1")
    print("=" * 96)
    mk = []
    for s in S.MAJORS:
        mk.extend((s, m) for m in B.settled_markets(s, PER_SERIES))
    print(f"  {len(mk):,} settled markets; sampling within {BUDGET}s\n")

    started = time.time()
    rows = []
    looked = 0
    for series, m in mk:
        if time.time() - started > BUDGET:
            break
        q = path(series, str(m.get("ticker")), m.get("close_time"))
        if not q:
            continue
        looked += 1
        res = str(m.get("result") or "").lower()
        if res not in ("yes", "no"):
            continue
        won = (res == "no")                     # a NO bet wins if it settles no
        for E in ENTRIES:
            if E not in q:
                continue
            ybid, yask = q[E]
            if not (YES_LO <= yask < YES_HI):
                continue
            no_ask = 1.0 - ybid                 # what buying NO costs
            n = int(STAKE / no_ask)
            if n < 1:
                continue
            cost = n * no_ask + fee(n, no_ask)
            # forward walk from the entry to the close, earliest minute first
            offs = sorted((o for o in q if o < E), reverse=True)
            out = {}
            for tgt in TARGETS:
                want = no_ask * tgt
                sold = None
                for o in offs:
                    nb = 1.0 - q[o][1]          # NO bid = 1 - yes ask
                    if nb >= want:
                        sold = nb
                        break
                if sold is not None:
                    out[tgt] = (n * sold - fee(n, sold)) - cost
                else:
                    out[tgt] = (n * 1.0 if won else 0.0) - cost
            rows.append(dict(E=E, entry=no_ask, n=n, cost=cost, won=won,
                             out=out, hold=(n * 1.0 if won else 0.0) - cost))
        time.sleep(B.PAUSE)

    print(f"  {looked:,} markets examined; {len(rows)} entries across "
          f"{len(ENTRIES)} offsets\n")
    if len(rows) < 40:
        print("  too few to read -- raise the budget.")
        return 0

    def rep(lab, g, key):
        if not g:
            print(f"    {lab:<22} no bets")
            return
        vals = [r["hold"] if key is None else r["out"][key] for r in g]
        risked = sum(r["cost"] for r in g)
        tot = sum(vals)
        hits = ("" if key is None else
                f"   hit {sum(1 for r in g if r['out'][key] != r['hold'])}"
                f"/{len(g)}")
        print(f"    {lab:<22} {len(g):>4} bets  net ${tot:>+8.2f}  "
              f"${tot/len(g):>+7.3f}/bet  ROI {100*tot/risked:>+7.2f}%{hits}")

    for E in ENTRIES:
        g = [r for r in rows if r["E"] == E]
        if not g:
            continue
        print(f"\n  ENTRY T-{E}   mean NO cost "
              f"{100*st.mean([r['entry'] for r in g]):.1f}c, "
              f"NO settled {100*sum(1 for r in g if r['won'])/len(g):.1f}%")
        rep("hold", g, None)
        for tgt in TARGETS:
            rep(f"sell at {tgt:g}x", g, tgt)

    print("\n" + "=" * 96)
    print("  DEPTH IS NOT MODELLED AND IT IS PROBABLY FATAL. $5 at 10c is")
    print("  fifty contracts; the book at these prices has been observed")
    print("  holding four or five. Every exit above fills fifty contracts at")
    print("  the quoted bid, which is a trade nobody can actually place.")
    print("  Read a positive number here as 'the price path allows it', never")
    print("  as 'this is available'.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())

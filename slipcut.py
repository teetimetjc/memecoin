"""Where do the 3.3 points go? Quote to fill, in dollars, per coin.

THE GAP THIS MEASURES. The T-6 scoring run graded the live rule at +2.84%
on QUOTES over its forward window. The live record over 582 real bets is
-0.47%. Something eats roughly three points between the price the scanner
reads and the money that comes back, and that same signature -- clean on
quotes, negative on fills -- is what retired T-3 and oil.

So this does not ask whether the strategy works. It asks WHERE THE MONEY
GOES, and it answers in dollars rather than in points, because a point of
slippage on a 92c favourite is not a point of return.

THE DECOMPOSITION, which is exact rather than modelled:

  what was paid       contracts * FILL price, from the fills endpoint
  what was quoted     contracts * ENTRY price, the scanner's number
  slippage dollars    the difference, per bet, summed

  counterfactual      the same bets, same outcomes, filled at the QUOTE
  realised            what actually happened

The counterfactual is not a hypothesis. Every bet's settlement is known, so
re-pricing the entry and leaving the outcome alone isolates execution from
selection with no assumption in between.

AND THE PART THAT IS NOT SLIPPAGE, said plainly because it limits the whole
exercise: the scored +2.84% and the live -0.47% are not the same bets. The
scorer graded 401 hypothetical $4 bets on every qualifying market in its
window; the live bot placed 582 real bets, at several stakes, over a
slightly different window, capped per window, and halted at times for
funding and outages. So the gap has at least two sources -- execution and
population -- and this file can only measure the first. If slippage does
not account for three points, the remainder is NOT thereby explained; it
means the live bot is selecting a different, worse set of bets than the
scorer, and that is a separate investigation.

PER COIN, because XRP is -$36.24 of the record's -$13.33, which means every
other coin is collectively positive. If XRP's slippage is ordinary, its
losses are variance in outcomes and there is nothing to fix. If XRP's fills
are systematically worse, that is a mechanism, and a mechanism is the only
thing that has ever justified retiring part of this strategy.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import statistics as st
import time

import tally as T
import coincut as C


def load():
    bets = T.rows_since(T.START)
    if not bets:
        return []

    setts, cur, pages = {}, None, 0
    while pages < 60:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = T.get("/portfolio/settlements", **p)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break
        time.sleep(0.15)

    by_ticker = collections.defaultdict(list)
    for b in bets:
        by_ticker[b["ticker"]].append(b)
    share = {}
    for tk, group in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in group) or 1
        for x in group:
            share[id(x)] = ((int(x["contracts"] or 0) / tot)
                            if len(group) > 1 else 1.0)

    fills = T.actual_costs()
    for b in bets:
        f = fills.get(b["oid"])
        want = int(b["contracts"] or 0)
        b["fillpx"] = None
        b["exact"] = False
        if f and f["cost"] and want and abs(f["n"] - want) < 0.01:
            b["cost"] = f["cost"]
            b["exact"] = True
            q = sum(f["qty"]) or 0.0
            b["fillpx"] = ((sum(p * x for p, x in zip(f["px"], f["qty"])) / q)
                           if q else (sum(f["px"]) / len(f["px"])))
        else:
            b["cost"] = b["limit_cost"] or 0.0
        s = setts.get(b["ticker"])
        b["rev"] = (s["rev"] * share.get(id(b), 1.0)) if s else None
    return bets


def report(label, g):
    """Exact decomposition for one group of bets."""
    # ONLY BETS WITH A READABLE FILL. A bet costed at the limit has no
    # measured fill price, so including it would compare the quote against
    # the limit -- a number live.py computed, not one the market gave.
    e = [b for b in g if b["exact"] and b["fillpx"] is not None
         and b["entry"] and b["rev"] is not None]
    if not e:
        print(f"  {label:<10} no bets with a readable fill")
        return None

    n = len(e)
    slips = []
    slip_d = 0.0
    paid = 0.0
    quoted = 0.0
    ret = 0.0
    for b in e:
        c = int(b["contracts"] or 0)
        q = float(b["entry"]) / 100.0          # the scanner's quote, dollars
        f = float(b["fillpx"])                 # what it actually filled at
        slips.append(100.0 * (f - q))          # cents, + means paid MORE
        slip_d += c * (f - q)
        paid += b["cost"] or 0.0
        quoted += c * q
        ret += b["rev"] or 0.0

    med = st.median(slips)
    worse = sum(1 for s in slips if s > 0.001)
    net_real = ret - paid
    # SIGN, worked through rather than guessed -- the first version had it
    # backwards and printed a saving as a cost.
    #   slip_d = contracts * (fill - quote).  Negative means we paid LESS
    #   than quoted, so paid = paid_at_quote + slip_d, and therefore
    #   net_quote = ret - (paid - slip_d) = net_real + slip_d.
    # Execution's effect on the result is net_real - net_quote = -slip_d:
    # a negative slip_d is money the fills SAVED.
    net_quote = net_real + slip_d
    print(f"  {label:<10} n {n:>4}   slip mean {st.mean(slips):+5.2f}c  "
          f"med {med:+5.2f}c   paid over quote on {100.0*worse/n:4.1f}%   "
          f"slip ${slip_d:+7.2f}   real ${net_real:+7.2f}   "
          f"at quote ${net_quote:+7.2f}")
    return dict(n=n, slip_d=slip_d, real=net_real, quote=net_quote,
                staked=paid, mean=st.mean(slips))


def main():
    print("=" * 112)
    print("SLIPPAGE: quote to fill, in dollars, per coin")
    print("  Decomposition is exact -- outcomes are known, only the entry")
    print("  price is re-priced. It isolates EXECUTION, not selection.")
    print("=" * 112)

    bets = load()
    if not bets:
        print("  no bets")
        return 1
    bets, why = C.keep(bets)
    print(f"\n  {len(bets)} T-9 bets on the coins")
    exact = sum(1 for b in bets if b["exact"])
    print(f"  {exact} have a readable fill; {len(bets) - exact} were costed "
          "at the limit and are excluded from the slippage figures")

    print(f"\n{'-' * 112}")
    print("POOLED, then by coin.  'slip' is fill minus quote: POSITIVE means "
          "paid MORE than quoted.")
    print("-" * 112)
    pooled = report("POOLED", bets)

    by = collections.defaultdict(list)
    for b in bets:
        by[C.coin_of(b["ticker"])].append(b)
    print()
    rows = {}
    for coin in sorted(by, key=lambda k: -len(by[k])):
        r = report(coin, by[coin])
        if r:
            rows[coin] = r

    if not pooled:
        return 1

    print(f"\n{'-' * 112}")
    print("WHAT THE SLIPPAGE ACTUALLY COSTS, against the gap it has to "
          "explain")
    print("-" * 112)
    st_ = pooled["staked"] or 1.0
    print(f"  staked               ${st_:.2f}")
    print(f"  realised net         ${pooled['real']:+.2f}   "
          f"({100.0*pooled['real']/st_:+.2f}% of stake)")
    print(f"  net at quoted prices ${pooled['quote']:+.2f}   "
          f"({100.0*pooled['quote']/st_:+.2f}% of stake)")
    eff = -pooled['slip_d']
    verb = "SAVED by" if eff > 0 else "COST by"
    print(f"  {verb} execution     ${eff:+.2f}   "
          f"({100.0*eff/st_:+.2f} points of stake)")
    print()
    print("  THE SCORED FIGURE WAS +2.84% ON QUOTES. If execution did not")
    print("  cost that distance -- or, worse, HELPED -- then execution is")
    print("  not the explanation at all, and the live bot is")
    print("  selecting a worse set of bets than the scorer did -- different")
    print("  window, per-window cap, funding halts, outages. That would be")
    print("  the next thing to measure, and it is not measured here.")

    print(f"\n{'-' * 112}")
    print("IS ANY COIN'S EXECUTION DIFFERENT?")
    print("-" * 112)
    pm = pooled["mean"]
    for coin, r in sorted(rows.items(), key=lambda kv: -kv[1]["slip_d"]):
        d = r["mean"] - pm
        print(f"  {coin:<10} mean slip {r['mean']:+5.2f}c  "
              f"({d:+5.2f}c vs pooled)   cost ${r['slip_d']:+7.2f}   "
              f"on {r['n']} bets")
    print()
    print("  A coin losing money with ORDINARY slippage is losing on")
    print("  outcomes, which is variance and not a mechanism. A coin with")
    print("  worse slippage than the others has a mechanism, and that is")
    print("  the only thing that has ever justified retiring part of this.")
    print("=" * 112)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

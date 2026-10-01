"""Sell cheap contracts as the MAKER, on 70 days of data.

Every strategy tested here has been a taker: cross the spread, pay the ask,
lose the spread. The one direction never tested at scale is the other side
of the trade we understand best -- longshot BUYERS lost $20,322 in three
days, and somebody collected that by resting quotes rather than crossing.

WHAT MAKING ACTUALLY CHANGES. Buying NO as a taker costs 1 minus the YES
bid. Resting instead, and being crossed into, fills at 1 minus the YES ASK
-- better by the whole spread. On a 5c longshot that is about two cents a
contract, which is the entire margin in question.

WHAT THE FEE ACTUALLY IS, MEASURED 2026-09-28. This file used to say the
fee was charged to both sides, so making dodged nothing. That was an
assumption, and it was wrong. Splitting 192 real fills by the exchange's own
is_taker flag:

    is_taker=True    n=174   implied rate 0.07001   (the taker rate, exactly)
    is_taker=False   n=18    implied rate 0.00000   (resting fills, NO fee)

So the earlier verdict -- 0.50c of half-spread against a 1.75c fee -- was
scored against a cost the resting side may never have owed, which is the
whole margin in a 1-2c market.

EIGHTEEN FILLS IS NOT PROOF, and that is why the multiplier is a parameter
rather than a constant. Three models are printed side by side: 0.00 (what
was measured), 0.25 (what secondary sources claim, unverifiable from this
runner -- Kalshi's fee schedule is blocked by the network policy) and 1.00
(the old assumption). A conclusion that only survives at 0.00 is a
conclusion about a fee we have seen eighteen times, and must be labelled
that way.

THE FEE IS ALSO NO LONGER CEILED to the cent. That was measured to overstate
by a median of 0.38c, which is most of a half-spread here.

TWO FILL MODELS, AND THE GAP BETWEEN THEM IS THE ANSWER.

  OPTIMISTIC: every resting order fills at our price, no selection. This is
  an upper bound and cannot be achieved. If it loses, the idea is dead and
  nothing else needs checking.

  ADVERSE: an order fills only when the market later trades through it --
  the bid rises to touch our ask. That is what really happens, and it is
  also exactly when we are wrong: we sold YES cheap and the market went up.
  A maker strategy that works optimistically and dies here is a strategy
  whose profits are imaginary.

Read-only.
"""

import collections
import math
import statistics as st
import sys

import predictor as P

OFFSETS = (14, 12, 9, 6, 3, 1)
MIN_N = 120
STAKE = 10.0


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


# Set per run from the command line. 1.00 reproduces every earlier number.
MAKER_MULT = 1.0


def fee_for(c, price, mult=1.0):
    """Unrounded, because ceil-to-the-cent overstated by 0.38c a bet."""
    return mult * 0.07 * c * price * (1 - price)


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def load(sh, name):
    rows = sh.worksheet(name).get_all_values()
    hi = {h: i for i, h in enumerate(rows[0])}
    out = []
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        def c(k):
            i = hi.get(k)
            return r[i] if i is not None and i < len(r) else ""
        res = str(c("Result")).lower().strip()
        if res not in ("yes", "no"):
            continue
        path = {}
        for o in OFFSETS:
            b, a = _f(c(f"bid{o}")), _f(c(f"ask{o}"))
            if b is not None and a is not None and 0 < b <= a < 1:
                path[o] = (b, a)
        if len(path) < 2:
            continue
        out.append(dict(close=str(c("Close Time")), ser=str(c("Series")),
                        yes=(res == "yes"), path=path,
                        # SERIES-LEVEL DESCRIPTION ONLY. In M15H this field
                        # is read at backfill time, AFTER settlement, so
                        # selecting individual bets on it is lookahead --
                        # exactly how a +$23/bet pocket passed a t-test, a
                        # day check, a shuffle AND a holdout before being
                        # thrown out. It is used here to say how liquid a
                        # SERIES is, never to choose which bets to take.
                        vol=_f(c("Volume")) or 0.0))
    return out


def report(bets, label):
    if len(bets) < MIN_N:
        print(f"  {label:<34} n={len(bets):<6} too few")
        return
    g = collections.defaultdict(list)
    for v, ct in bets:
        g[ct].append(v)
    n = len(bets)
    m = sum(v for v, _ in bets) / n
    se = cse(g, n)
    by = sorted((sum(v) for v in g.values()), reverse=True)
    minus2 = sum(by[2:])
    share = 100 * sum(1 for x in by if x > 0) / len(by)
    t = m / se if se else 0.0
    flag = ""
    if m > 0:
        flag = "  <== POSITIVE" if (t > 2.0 and minus2 > 0) else "  (positive, fails a check)"
    print(f"  {label:<34} n={n:<6} ${m:>+6.2f} +/-{se:>4.2f} t={t:>+5.1f} "
          f"days+ {share:>3.0f}% total {sum(v for v, _ in bets):>+9.0f}{flag}")


def by_series(M, bands, model, mult):
    """Per-series results, with liquidity beside them.

    Pooling twenty series hides the only thing that matters when new ones
    are added: a 15-minute palladium market that trades four contracts a
    day can look wonderful and cannot be traded for a cent. Volume is a
    property of the SERIES here, not a per-bet feature.
    """
    bysers = collections.defaultdict(list)
    for m in M:
        bysers[m["ser"]].append(m)
    print(f"\n  PER SERIES  ({model} fills, resting fee "
          f"{mult:.2f}x)")
    print(f"    {'series':<18} {'mkts':>6} {'medvol':>8} {'bets':>6} "
          f"{'$/bet':>8} {'t':>6}")
    print("    " + "-" * 60)
    for ser in sorted(bysers, key=lambda k: -len(bysers[k])):
        items = bysers[ser]
        bets = []
        for m in items:
            for i, off in enumerate(OFFSETS[:-1]):
                q = m["path"].get(off)
                if not q:
                    continue
                b, a = q
                if not (0.01 <= a < 0.35):
                    continue
                later = [m["path"][o] for o in OFFSETS[i + 1:]
                         if o in m["path"]]
                if model == "adverse" and not any(lb >= a for lb, la in later):
                    continue
                cost = 1.0 - a
                c = int(STAKE / cost)
                if c < 1:
                    continue
                f = fee_for(c, cost, mult)
                won = not m["yes"]
                bets.append((((c if won else 0) - (c * cost + f)), m["close"]))
                break
        vols = sorted(x["vol"] for x in items)
        medvol = vols[len(vols) // 2] if vols else 0.0
        if len(bets) < 40:
            print(f"    {ser:<18} {len(items):>6} {medvol:>8.0f} "
                  f"{len(bets):>6}   too few")
            continue
        g = collections.defaultdict(list)
        for v, ct in bets:
            g[ct].append(v)
        n = len(bets)
        mean = sum(v for v, _ in bets) / n
        se = cse(g, n)
        t = (mean / se) if se else 0.0
        flag = ""
        if mean > 0 and t > 2.0:
            flag = "  <== positive" + ("" if medvol > 0 else
                                       " BUT ZERO VOLUME -- untradeable")
        print(f"    {ser:<18} {len(items):>6} {medvol:>8.0f} {n:>6} "
              f"{mean:>+8.3f} {t:>+6.1f}{flag}")


def main():
    global MAKER_MULT
    tab = sys.argv[1] if len(sys.argv) > 1 else "M15H"
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    M = load(sh, tab)
    print("=" * 100)
    print(f"MAKER TEST on {tab}: {len(M)} markets with two-sided quotes")
    print("=" * 100)
    print("We SELL yes (buy no) by resting, so our cost is 1 - yes_ask.")
    print("The taker doing the same trade pays 1 - yes_bid.\n")

    bands = ((0.01, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 0.35))
    for MAKER_MULT in (0.0, 0.25, 1.0):
      globals()["MAKER_MULT"] = MAKER_MULT
      print("\n" + "#" * 100)
      print(f"# RESTING FEE = {MAKER_MULT:.2f} x the taker rate"
            + ("   <-- MEASURED on 18 real resting fills" if MAKER_MULT == 0
               else "   <-- claimed by secondary sources, unverified"
               if MAKER_MULT == 0.25 else
               "   <-- the old assumption, now known to be wrong"))
      print("#" * 100)
      for model in ("optimistic", "adverse"):
        print("=" * 100)
        print(f"{model.upper()} FILLS")
        print("=" * 100)
        for lo, hi in bands:
            bets = []
            for m in M:
                for i, off in enumerate(OFFSETS[:-1]):
                    q = m["path"].get(off)
                    if not q:
                        continue
                    b, a = q
                    if not (lo <= a < hi):
                        continue
                    later = [m["path"][o] for o in OFFSETS[i + 1:]
                             if o in m["path"]]
                    if model == "adverse":
                        # filled only if someone later bids up to our ask
                        if not any(lb >= a for lb, la in later):
                            continue
                    cost = 1.0 - a            # we buy NO at 1 - yes_ask
                    c = int(STAKE / cost)
                    if c < 1:
                        continue
                    f = fee_for(c, cost, MAKER_MULT)
                    won = not m["yes"]        # NO wins when the market says no
                    bets.append((((c if won else 0) - (c * cost + f)),
                                 m["close"]))
                    break                     # one resting order per market
            report(bets, f"sell yes at {lo:.2f}-{hi:.2f}")

    # The taker doing the identical trade, for contrast.
    print("=" * 100)
    print("THE SAME TRADE AS A TAKER  (pays 1 - yes_bid instead)")
    print("=" * 100)
    for lo, hi in bands:
        bets = []
        for m in M:
            for off in OFFSETS:
                q = m["path"].get(off)
                if not q:
                    continue
                b, a = q
                if not (lo <= a < hi):
                    continue
                cost = 1.0 - b
                c = int(STAKE / cost)
                if c < 1:
                    continue
                f = fee_for(c, cost)
                won = not m["yes"]
                bets.append((((c if won else 0) - (c * cost + f)), m["close"]))
                break
        report(bets, f"sell yes at {lo:.2f}-{hi:.2f}")

    # The per-series view, on the model that matters and the fee that was
    # measured. Twelve series were added to M15H having never been analysed
    # by anything, so a pooled number would average them into the eight
    # already known to fail.
    globals()["MAKER_MULT"] = 0.0
    by_series(M, bands, "adverse", 0.0)
    by_series(M, bands, "optimistic", 0.0)

    print("\n" + "=" * 100)
    print("If OPTIMISTIC loses, the idea is dead -- it is an upper bound that")
    print("assumes free fills and no selection. If optimistic wins and ADVERSE")
    print("loses, the profit was imaginary: the fills arrive precisely when the")
    print("market is moving against the quote.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())

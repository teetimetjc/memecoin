"""The round trip: rest, get filled, get OUT, and count the money.

slowfill measured the price obtained against the mid just after the fill and
found it positive -- about +1.3c a contract in slow markets, which disproved
the adverse assumption we had been carrying from 15-minute crypto. It did not
show a profit, for three reasons it stated plainly, and this file exists to
remove all three.

  A MID IS NOT CASH. You cannot sell at the mid. Closing a position means
  either crossing the spread -- paying the other touch AND the taker fee --
  or resting again and waiting. slowfill scored neither. Both are scored
  here, and the aggressive exit is the conservative one.

  "88% FILLED" MEANT EVENTUALLY. slowfill let an order sit until the
  market's data ran out, sometimes days. A fill rate with no deadline is not
  a fill rate. Here the wait is bounded and an order that misses its window
  is cancelled: no trade, counted against the fill rate, not scored as a
  loss.

  OVERLAPPING ATTEMPTS INFLATED THE t. An attempt began at every book row,
  so dozens shared a single fill and the errors were not independent. Here a
  market yields at most one attempt at a time, and after one resolves there
  is a cooldown before the next, so attempts never share a fill.

THE TRADE, EXACTLY.

  Resting an ASK at a is selling YES. Closing it means BUYING YES back:
  aggressively at the later ask plus the taker fee, or passively by resting
  a bid and waiting for someone to sell into it.

  Resting a BID at b is buying YES. Closing it means SELLING YES: at the
  later bid plus the fee, or passively at a rested ask.

  Entry is always a maker fill, which the exchange's own fills measured at
  zero fee (18 of them -- few, so the taker rate is also printed). The
  aggressive exit is a taker fill and pays 0.07 * C * P * (1-P), verified
  against 174 real fills.

WHAT WOULD STILL NOT BE PROVEN BY A POSITIVE NUMBER HERE. This is the data
the idea was found in. The fallen favourite looked like +3.71pp on its
discovery set, was frozen precisely because it looked good, and came back
-$1.29 a bet on 2,355 held-out bets. A positive result here earns a frozen
spec and a holdout, nothing more.

Read-only.
"""

import collections
import math
import statistics as st
import sys

from slowfill import CUTOFF, load, mid_at

STAKE = 10.0
MAX_WAIT_S = 1800        # bounded wait for the entry fill
HOLD_S = 900             # how long to hold before trying to get out
EXIT_WINDOW_S = 1800     # how long a passive exit may wait before crossing
COOLDOWN_S = 3600        # after an attempt resolves, before the next starts
MAKER_RATE = 0.0         # measured on 18 real resting fills
TAKER_RATE = 0.07        # verified on 174 real crossing fills
MIN_N = 40


def fee(c, px, rate):
    return rate * c * px * (1 - px)


def cse(groups, n):
    G = len(groups)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in groups.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in groups.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def fill_from(seq, i0, side, price, queue, deadline):
    """Walk forward for a fill of OUR order at `price`, back of the queue.

    Returns (index, event) of the filling print, or (None, None) if the
    deadline passes first. The deadline is what makes this a fill rate
    rather than a statement that everything fills eventually.
    """
    ahead = queue
    want = "yes" if side == "ask" else "no"
    for j in range(i0 + 1, len(seq)):
        e = seq[j]
        if e["t"] > deadline:
            return None, None
        if e["kind"] != "trade" or not e["cts"]:
            continue
        if not e["side"].startswith(want):
            continue
        ahead -= e["cts"]
        if ahead < 0:
            return j, e
    return None, None


def book_at(seq, i_from, want):
    """(bid, ask) from the last book state at or before `want`, or None."""
    out = None
    for j in range(i_from, len(seq)):
        e = seq[j]
        if e["t"] > want:
            break
        if e["bid"] is not None and e["ask"] is not None:
            out = (e["bid"], e["ask"])
    if out is None:
        return None
    return out if seq[-1]["t"] >= want else None


def round_trip(seq, side, passive_exit):
    """One pass over a market, non-overlapping attempts, full round trips.

    Yields dollar results per $10 position, plus the counters needed to tell
    a missed fill from a scored loss.
    """
    res = []
    tried = filled = no_fill = unresolved = 0
    i = 0
    n = len(seq)
    while i < n:
        e0 = seq[i]
        if e0["bid"] is None or e0["ask"] is None:
            i += 1
            continue
        entry = e0["ask"] if side == "ask" else e0["bid"]
        queue = e0["asz"] if side == "ask" else e0["bsz"]
        if entry is None or queue is None or not (0.02 < entry < 0.98):
            i += 1
            continue
        tried += 1
        j, hit = fill_from(seq, i, side, entry, queue, e0["t"] + MAX_WAIT_S)
        if j is None:
            no_fill += 1
            # Nothing was traded, so nothing is scored. Move past the window.
            nxt = e0["t"] + MAX_WAIT_S + COOLDOWN_S
            while i < n and seq[i]["t"] < nxt:
                i += 1
            continue

        c = int(STAKE / entry)
        if c < 1:
            i = j + 1
            continue
        # Entry is a maker fill.
        pnl = (c * entry - fee(c, entry, MAKER_RATE)) if side == "ask" \
            else -(c * entry + fee(c, entry, MAKER_RATE))

        t_exit = hit["t"] + HOLD_S
        closed = False
        if passive_exit:
            # Close by RESTING on the other side: a short YES is closed by a
            # resting bid, a long YES by a resting ask.
            xside = "bid" if side == "ask" else "ask"
            bk = book_at(seq, j, t_exit)
            if bk is not None:
                # find the index at/after t_exit to start the exit walk
                k = j
                while k < n and seq[k]["t"] < t_exit:
                    k += 1
                if k < n:
                    xprice = bk[0] if xside == "bid" else bk[1]
                    xqueue = None
                    for e in seq[max(0, k - 1):k + 1]:
                        xqueue = (e["bsz"] if xside == "bid" else e["asz"])
                        if xqueue is not None:
                            break
                    if xprice is not None and xqueue is not None:
                        kk, xhit = fill_from(seq, k, xside, xprice, xqueue,
                                             t_exit + EXIT_WINDOW_S)
                        if kk is not None:
                            pnl += (-(c * xprice
                                      + fee(c, xprice, MAKER_RATE))
                                    if side == "ask"
                                    else (c * xprice
                                          - fee(c, xprice, MAKER_RATE)))
                            closed = True
                            j = kk
        if not closed:
            # Cross to get out: pay the other touch plus the taker fee. This
            # is the conservative branch and the only one always available.
            bk = book_at(seq, j, t_exit + (EXIT_WINDOW_S if passive_exit
                                           else 0))
            if bk is None:
                unresolved += 1      # cannot observe an exit; DROP, not zero
                nxt = hit["t"] + COOLDOWN_S
                while i < n and seq[i]["t"] < nxt:
                    i += 1
                continue
            b, a = bk
            if side == "ask":
                pnl += -(c * a + fee(c, a, TAKER_RATE))   # buy YES back
            else:
                pnl += (c * b - fee(c, b, TAKER_RATE))    # sell YES out
        filled += 1
        res.append(pnl)
        nxt = seq[min(j, n - 1)]["t"] + COOLDOWN_S
        while i < n and seq[i]["t"] < nxt:
            i += 1
    return res, tried, filled, no_fill, unresolved


def rep(vals, keys, label, extra=""):
    n = len(vals)
    if n < MIN_N:
        print(f"    {label:<44} n={n:<5} too few{extra}")
        return
    g = collections.defaultdict(list)
    for v, k in zip(vals, keys):
        g[k].append(v)
    m = sum(vals) / n
    se = cse(g, n)
    t = (m / se) if se else 0.0
    days = collections.defaultdict(float)
    for v, k in zip(vals, keys):
        days[k] += v
    by = sorted(days.values(), reverse=True)
    best2 = sum(by[2:])
    worst2 = sum(by[:-2]) if len(by) > 2 else 0.0
    flag = ""
    if m > 0:
        flag = ("  <== POSITIVE" if (t > 2.0 and best2 > 0 and worst2 > 0)
                else "  (positive, fails a check)")
    print(f"    {label:<44} n={n:<5} ${m:>+7.3f}/trip +/-{se or 0:>5.3f} "
          f"t={t:>+5.1f}{flag}{extra}")


def main():
    ev = load()
    if not ev:
        print("no SLOWBOOK rows")
        return 0
    print("\n" + "=" * 110)
    print("THE ROUND TRIP: rest, fill, and get out.  $10 a position, "
          "maker fee 0, taker fee 0.07 on the exit")
    print("=" * 110)
    print(f"  wait up to {MAX_WAIT_S//60}min for the entry, hold "
          f"{HOLD_S//60}min, passive exit may wait "
          f"{EXIT_WINDOW_S//60}min before crossing,")
    print(f"  {COOLDOWN_S//60}min cooldown so no two attempts share a fill.\n")

    for passive in (False, True):
        how = ("RESTING, crossing only if it does not fill" if passive
               else "CROSSING THE SPREAD")
        print(f"  EXIT BY {how}")
        for side in ("ask", "bid"):
            vals, keys = [], []
            T = F = NF = UR = 0
            for tk, seq in ev.items():
                r, t_, f_, nf, ur = round_trip(seq, side, passive)
                vals += r
                keys += [tk] * len(r)
                T, F, NF, UR = T + t_, F + f_, NF + nf, UR + ur
            fr = (100.0 * F / T) if T else 0.0
            rep(vals, keys,
                f"rest {side} ({'sell' if side == 'ask' else 'buy'} YES), "
                f"fill {fr:.0f}%",
                extra=f"   [{NF} never filled, {UR} unresolvable]")
        print()

    print("=" * 110)
    print("This is the data the idea was found in. A positive number here")
    print("earns a FROZEN SPEC and held-out data, nothing more: the fallen")
    print("favourite showed +3.71pp on discovery and -$1.29 a bet on 2,355")
    print("held-out bets. Nothing is tradeable until it survives that.")
    print("=" * 110)
    return 0


if __name__ == "__main__":
    sys.exit(main())

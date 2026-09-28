"""The liquid slow markets, deeply: is the price wrong, and can resting pay?

exchange.py found where volume actually is -- daily city temperatures, the
Fed decision, weekly oil -- but sampled 22 markets a series, so exactly one
family cleared the minimum to be scored. That is a sampling depth problem,
not an answer. These series have years of history and books quoting 1-2c on
tens of thousands of contracts, so this takes 200 markets each and asks both
questions properly.

WHY BOTH QUESTIONS IN ONE PASS. They need the same expensive thing -- the
per-minute bid/ask history of every market -- and one candlestick call buys
both. Asking them separately would double the runtime for nothing.

  QUESTION 1, THE PRICE. ONE quote per market, at the midpoint of its life.
  The first version of this file took four per market and handed all four to
  the noise floor, which assumes independent draws -- four reads of the same
  market are one market, so the floor came out too small and the excess too
  large. Depth here has to come from more MARKETS, not more looks at each.

  AND A CALIBRATION CAN BE VACUOUS. These are strike ladders: most strikes
  on a temperature market are hopeless, so nearly every quote sits at 2c or
  98c, where p*(1-p) is almost zero and both the error and the floor round
  to 0.00pp. The first run printed "market is beatable here" off a +0.00pp
  excess built entirely out of that. The price spread across buckets is now
  printed, and a sample concentrated in fewer than three buckets is called
  vacuous rather than scored.

  QUESTION 2, RESTING. The same optimistic-versus-adverse pair maker70 runs
  on crypto. The crypto answer was unambiguous -- optimistic +$0.24, adverse
  -$2.41, and free fees moved that by eight cents -- but it was measured on
  markets that live fifteen minutes and are quoted by bots. A daily
  temperature market is the opposite kind of venue, which is the only reason
  to ask again.

FEES. Taker 0.07 * C * P * (1-P), verified on 174 real fills. Resting 0.00,
measured on 18 -- few enough that the resting column is labelled as resting
on eighteen observations, and printed at 0.25x as well.

THREE TRAPS, ALL PAID FOR ALREADY IN THIS PROJECT.

  A settled last_price is the price AFTER the answer is known. Never read.

  A one-sided book is not a price: these candles carry a 99c ask against a
  0c bid whenever nobody is quoting, and averaging it in manufactures both a
  mispricing and a spread.

  Volume is volume_fp. Reading `volume` returns zero for every market on the
  exchange, which looks exactly like a venue where nothing trades.

Read-only. Places no orders, writes no tabs.
"""

import collections
import math
import statistics as st
import sys
import time

import backfill as B
import calib_survey as CS
from exchange import _vol, pick_interval

# The families exchange.py measured as actually trading, highest volume
# first. Crypto monthlies are included deliberately: same underlying as the
# dead 15-minute strategies, opposite horizon.
DEFAULT = ["KXHIGHLAX", "KXBTCMINMON", "KXFEDCOMBO", "KXHIGHAUS",
           "KXWTIW", "KXHIGHPHIL", "KXHIGHTBOS", "KXHIGHTATL",
           "KXBTCMAXMON", "KXTRUMPACT"]
MKT = 200
BUDGET_S = 2400
PAUSE = 0.05
STAKE = 10.0
MIN_N = 150   # per POOLED band; a single slow family never reaches it
BANDS = ((0.02, 0.10), (0.10, 0.25), (0.25, 0.45), (0.45, 0.60),
         (0.60, 0.80), (0.80, 0.95))


def fee(c, p, mult=1.0):
    return mult * 0.07 * c * p * (1 - p)


def cse(g, n):
    G = len(g)
    if G < 2 or not n:
        return None
    m = sum(sum(v) for v in g.values()) / n
    ss = sum((sum(v) - len(v) * m) ** 2 for v in g.values())
    return math.sqrt((G / (G - 1.0)) * ss / (n * n))


def book(series, ticker, open_s, close_s):
    """Every two-sided quote in a market's life, oldest first.

    Returns [(ts, bid, ask)]. A bar closing at or after the market does is
    dropped: that is the price once the answer is known, and it is the
    single mistake that would make every number here look perfect.
    """
    life = close_s - open_s
    if life <= 0:
        return []
    iv = pick_interval(life)
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(open_s), end_ts=int(close_s), period_interval=iv)
    out = []
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or t >= close_s:
            continue

        def side(k):
            v = c.get(k)
            return B._f(v.get("close_dollars")) if isinstance(v, dict) else None

        bid, ask = side("yes_bid"), side("yes_ask")
        if bid is None or ask is None:
            continue
        if not (0 < bid < ask < 1) or (ask - bid) > 0.25:
            continue        # nobody quoting, not a 50c market
        out.append((t, bid, ask))
    out.sort()
    return out


def at_fraction(bk, open_s, close_s, f):
    """The quote nearest a given fraction through the market's life."""
    if not bk:
        return None
    want = open_s + (close_s - open_s) * f
    best, bd = None, 1e18
    for t, b, a in bk:
        g = abs(t - want)
        if g < bd:
            best, bd = (t, b, a), g
    return best


def rest_trade(bk, lo, hi, model, mult):
    """Rest an ask inside a price band; hold to settlement if filled.

    Selling YES by resting means our fill price is the yes ASK -- better
    than the bid a taker would pay by the whole spread. In the adverse
    model the order fills only when someone later bids up to touch it,
    which is also exactly when the market has moved against us.
    """
    for i, (t, b, a) in enumerate(bk):
        if not (lo <= a < hi):
            continue
        if model == "adverse":
            if not any(nb >= a for _, nb, _ in bk[i + 1:]):
                continue
        cost = 1.0 - a          # we are buying NO at 1 - yes_ask
        c = int(STAKE / cost)
        if c < 1:
            continue
        return c, cost, fee(c, cost, mult)
    return None


def report(bets, label):
    if len(bets) < MIN_N:
        print(f"    {label:<30} n={len(bets):<6} too few")
        return
    g = collections.defaultdict(list)
    for v, k in bets:
        g[k].append(v)
    n = len(bets)
    m = sum(v for v, _ in bets) / n
    se = cse(g, n)
    t = (m / se) if se else 0.0
    by = sorted((sum(v) for v in g.values()), reverse=True)
    # Both directions: deleting the best days catches a lucky streak,
    # deleting the worst catches pennies in front of a steamroller.
    best2 = sum(by[2:])
    worst2 = sum(by[:-2])
    share = 100 * sum(1 for x in by if x > 0) / len(by)
    flag = ""
    if m > 0:
        flag = ("  <== POSITIVE" if (t > 2.0 and best2 > 0 and worst2 > 0)
                else "  (positive, fails a robustness check)")
    print(f"    {label:<30} n={n:<6} ${m:>+6.3f} +/-{se or 0:>5.3f} "
          f"t={t:>+5.1f}  days+ {share:>3.0f}%{flag}")


def main():
    only = [a for a in sys.argv[1:] if a.strip()] or DEFAULT
    t0 = time.time()
    print("=" * 104)
    print(f"THE LIQUID SLOW MARKETS, {MKT} settled markets per series")
    print("=" * 104)
    data = {}
    for ser in only:
        if time.time() - t0 > BUDGET_S:
            print("  (time budget reached)")
            break
        mk = B.settled_markets(ser, MKT)
        rows, bks, vols, spreads = [], [], [], []
        for m in mk:
            if time.time() - t0 > BUDGET_S:
                break
            res = str(m.get("result") or "").lower()
            o, c = B.ts(m.get("open_time")), B.ts(m.get("close_time"))
            if res not in ("yes", "no") or o is None or c is None:
                continue
            bk = book(ser, m["ticker"], o, c)
            time.sleep(PAUSE)
            if not bk:
                continue
            vols.append(_vol(m))
            key = str(m.get("close_time"))[:10]
            # ONE calibration observation per market. Four reads of one
            # market are one market, and the noise floor cannot know that.
            q = at_fraction(bk, o, c, 0.50)
            if q:
                rows.append(((q[1] + q[2]) / 2.0, res == "yes"))
                spreads.append(q[2] - q[1])
            bks.append((bk, res == "yes", key))
        if not bks:
            print(f"\n{ser:<16} no usable book history")
            continue
        data[ser] = (rows, bks, vols, spreads)
        buckets, err, n, floor = CS.calibrate(rows)
        ex = ((err - floor) / n) if n else None
        used = sum(1 for _, (cnt, _, _) in buckets.items() if cnt >= 5)
        print(f"\n{ser:<16} markets {len(bks):<5} quotes {len(rows):<6} "
              f"median volume {st.median(vols) if vols else 0:>8.0f}  "
              f"median spread {100*st.median(spreads):>4.1f}c")
        print(f"  Q1 price: calibration error "
              f"{100*err/n if n else 0:.2f}pp vs noise floor "
              f"{100*floor/n if n else 0:.2f}pp on n={n}  -> excess "
              + (f"{100*ex:+.2f}pp" if ex is not None else "n/a")
              + f"  across {used} price buckets"
              + ("   VACUOUS -- nearly every quote sits at an extreme, "
                 "where error and floor both round to zero" if used < 3
                 else "   <== worth a closer look" if (ex or 0) > 0.005
                 else "   nothing found"))

    # ------------------------------------------------------------------
    # Q2 IS POOLED ACROSS SERIES. A slow family lists a few hundred markets
    # in its entire history and one resting order per market, so per-series
    # every band came back "too few" -- which is not an answer, it is the
    # sample size of a daily market. The question is whether resting pays in
    # slow markets at all, so the trades go in one pile, clustered by close
    # date so two markets closing the same day are not counted as two
    # independent draws.
    # ------------------------------------------------------------------
    allbks = [(bk, yes, f"{ser}:{key}")
              for ser, (_, bks, _, _) in data.items() for bk, yes, key in bks]
    print("\n" + "=" * 104)
    print(f"QUESTION 2, POOLED: resting across {len(data)} slow families, "
          f"{len(allbks)} markets")
    print("=" * 104)
    for mult, note in ((0.0, "resting fee 0.00x, measured on 18 real fills"),
                       (0.25, "resting fee 0.25x, unverified claim")):
        print(f"\n  {note}")
        for model in ("optimistic", "adverse"):
            for lo, hi in BANDS:
                bets = []
                for bk, yes, key in allbks:
                    tr = rest_trade(bk, lo, hi, model, mult)
                    if not tr:
                        continue
                    c, cost, f_ = tr
                    bets.append(((c if not yes else 0) - (c * cost + f_), key))
                report(bets, f"{model:<11} sell yes {lo:.2f}-{hi:.2f}")

    print("\n" + "=" * 104)
    print("On 15-minute crypto the resting answer was optimistic +$0.24 and")
    print("adverse -$2.41, and a free fee moved it eight cents: the fills")
    print("arrive exactly when the quote is wrong. If ADVERSE survives in a")
    print("slow market, that is the first genuinely new result in this")
    print("project. If it does not, resting is dead everywhere and the")
    print("remaining hope is Q1 -- which needs a POSITIVE excess, on volume.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())

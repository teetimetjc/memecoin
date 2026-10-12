"""What would the NO side have done today, window by window?

THE QUESTION. The bot bets YES only. On 2026-10-12 it sat idle for most of
the day because nothing reached the 90-96c YES band -- the five majors were
quoting 9c, 23c, 12c, 9c, 29c at T-9, which is 71-91c on the NO side. So the
favourites were there; they were just on the side ALLOW_DOWN keeps switched
off. This replays the day with both sides turned on and settles them.

WHAT IT DOES. For every major window closed in the last N hours it reads the
T-9 candle, takes the yes bid and the yes ask, and prices both sides exactly
as score.py does:

    YES price = yes ask            wins if the market settled yes
    NO  price = 1 - yes bid        wins if the market settled no

Anything landing in 90-96c is a bet, $10 flat, contracts = int(10/price),
fee 0.07*C*P*(1-P) on entry, held to settlement.

FOUR REASONS THIS IS ILLUSTRATION AND NOT EVIDENCE, stated here because a
single good day is exactly the kind of number that starts a bad decision:

  ONE DAY, AND A DAY PICKED FOR BEING UNUSUAL. The sample is whatever closed
  since midnight. It was chosen because YES did badly in it, which is the
  definition of a result you cannot generalise from.

  THE REAL TEST ALREADY RAN AND SAID ZERO. The NO-side favourite measured
  +0.03% over 2,052 bets, t=0.04, and a regime-conditional re-test agreed.
  Nothing one day prints can overturn that; at most it can illustrate it.

  THESE ARE QUOTES, NOT FILLS. Every bet here is priced at the quote, which
  is the assumption that cost real money on 2026-10-12 when three orders
  quoted at 90-94c filled at 61-67c. A replay cannot slip.

  THE MAPPING IS UNCONFIRMED. live.py records buying NO as selling YES and
  flags it as an inference, which is why ALLOW_DOWN is off. If it is
  backwards, every NO row below is on the wrong side of the market.

Read-only: candles and market metadata, no orders, no writes.
"""

import sys
import time

import backfill as B
import score as S


MAJORS = list(S.MAJORS)
ENTRY = S.ENTRY
STAKE = S.STAKE
LO, HI = 0.90, 0.96
HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
PER_SERIES = 120
# The live cap, applied per close window after sorting the way favlive sorts.
MAX_PER_WINDOW = 2


def quote_at(series, ticker, close, offset):
    """(yes_bid, yes_ask) as fractions at `offset` minutes before close."""
    end = B.ts(close)
    if end is None:
        return None, None
    d, err = B.get(
        f"/trade-api/v2/series/{series}/markets/{ticker}/candlesticks",
        start_ts=int(end) - (offset + 3) * 60, end_ts=int(end),
        period_interval=1)
    if not d:
        return None, None
    for c in (d or {}).get("candlesticks") or []:
        t = B._f(c.get("end_period_ts"))
        if t is None or int(round((end - t) / 60.0)) != offset:
            continue

        def side(k):
            v = c.get(k)
            return B._f(v.get("close_dollars")) if isinstance(v, dict) else None

        b, a = side("yes_bid"), side("yes_ask")
        if b is None or a is None or not (0 < b <= a < 1):
            return None, None
        return b, a
    return None, None


def pnl(price, won):
    c = int(STAKE / price)
    if c < 1:
        return 0.0, 0
    return (c if won else 0) - (c * price + S.fee(c, price)), c


def main():
    now = time.time()
    cutoff = now - HOURS * 3600.0
    print("=" * 96)
    print(f"BOTH SIDES REPLAYED OVER THE LAST {HOURS:.0f} HOURS")
    print(f"  90-96c at T-{ENTRY}, ${STAKE:.0f} flat, held to settlement")
    print("=" * 96)

    rows = []
    for s in MAJORS:
        for m in B.settled_markets(s, PER_SERIES):
            ct = m.get("close_time")
            t = B.ts(ct)
            if t is None or t < cutoff or t > now:
                continue
            res = str(m.get("result") or "").lower()
            if res not in ("yes", "no"):
                continue
            b, a = quote_at(s, str(m.get("ticker")), ct, ENTRY)
            if b is None:
                continue
            rows.append(dict(t=t, series=s, bid=b, ask=a, yes=(res == "yes")))
            time.sleep(B.PAUSE)

    if not rows:
        print("\n  no settled windows with a readable quote in range.")
        return 0
    rows.sort(key=lambda r: (r["t"], r["series"]))
    print(f"  {len(rows)} windows with a readable T-{ENTRY} quote\n")

    sigs = []
    for r in rows:
        for side in ("YES", "NO"):
            px = r["ask"] if side == "YES" else (1.0 - r["bid"])
            if not (LO <= px < HI):
                continue
            won = r["yes"] if side == "YES" else (not r["yes"])
            v, c = pnl(px, won)
            sigs.append(dict(t=r["t"], series=r["series"], side=side,
                             px=px, won=won, v=v, c=c))

    if not sigs:
        print("  nothing qualified on either side.")
        return 0

    print(f"  {'closed (UTC)':<18} {'series':<10} {'side':<5} {'price':>7} "
          f"{'ct':>4}  {'result':<8} {'P&L':>8}")
    for s in sigs:
        when = time.strftime("%H:%M", time.gmtime(s["t"]))
        print(f"  {when:<18} {s['series']:<10} {s['side']:<5} "
              f"{100*s['px']:>6.1f}c {s['c']:>4}  "
              f"{'WON' if s['won'] else 'lost':<8} {s['v']:>+8.2f}")

    print("\n" + "=" * 96)
    for side in ("YES", "NO"):
        g = [x for x in sigs if x["side"] == side]
        if not g:
            print(f"  {side:<4} no signals")
            continue
        w = sum(1 for x in g if x["won"])
        tot = sum(x["v"] for x in g)
        print(f"  {side:<4} {len(g):>3} signal(s), {w} won "
              f"({100.0*w/len(g):.0f}%), net ${tot:+.2f}")

    # The live cap changes the answer and pretending otherwise overstates it.
    byw = {}
    for s in sigs:
        byw.setdefault(s["t"], []).append(s)
    capped = []
    for t in sorted(byw):
        g = sorted(byw[t], key=lambda x: (x["series"], x["side"]))
        capped.extend(g[:MAX_PER_WINDOW])
    print()
    print(f"  with the live cap of {MAX_PER_WINDOW} bets per close window, "
          f"both sides on:")
    w = sum(1 for x in capped if x["won"])
    print(f"    {len(capped)} bet(s), {w} won, net "
          f"${sum(x['v'] for x in capped):+.2f}")
    print("=" * 96)
    print("  ONE DAY, PICKED BECAUSE IT WAS A BAD ONE FOR YES. The NO side")
    print("  measured +0.03% over 2,052 bets (t=0.04) and a regime-")
    print("  conditional re-test agreed. Whatever is above illustrates that")
    print("  null, it does not overturn it -- and every price here is a")
    print("  quote, which is the assumption that cost real money today when")
    print("  three orders quoted at 90-94c filled at 61-67c.")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The live record cut by COIN. Real money, real fills, honest about n.

THE HOLE THIS CLOSES. Every report so far has cut the record by strategy,
by price band, by stake and by hour -- never by underlying. So if one coin
has been carrying the others, or dragging them, nobody here would know. That
is worth closing on its own, whatever the answer turns out to be.

WHY THE LIVE RECORD AND NOT THE CANDLES. The question asked was about poor
results over time -- actual money -- and the thing that killed T-3 and oil
was never visible in quotes. Both cleared a quote test and lost on FILLS.
So this reads settlements and fills, at the cost of a much smaller sample.
It also keeps the Kalshi API quiet enough to run alongside the live trader.

THE SAMPLE IS THE WHOLE PROBLEM, and it is stated before the numbers rather
than after. The record holds roughly six hundred placed bets spread over
five coins, so a typical coin has somewhere near a hundred -- BELOW the 150
the band tests had to clear, and far below what it takes to separate two
coins whose true edge differs by a point or two. At these sizes the gap
between the best and worst coin will look large even if every coin is
identical, because that is what sampling noise does to five groups.

So this reports the confidence interval beside every rate, and the pass bar
is the one already used on the bands:

  1. n >= 150 settled bets, or the coin is reported as TOO THIN and no
     conclusion is drawn from it either way.
  2. Net return measured against THAT COIN'S own mean cost per contract.
     A coin whose entries were cheaper is not thereby better; a winning
     contract pays exactly $1 and the hurdle moves with the price.
  3. The sign must hold in BOTH chronological halves.
  4. The gap from the pooled rate must exceed the interval around it.

AND THE RULE SET BEFORE LOOKING: a coin is not dropped on this evidence
alone. Oil and T-3 were retired because there was a MECHANISM -- clean
quotes, bad fills, the same shape twice -- not because a number was
negative. If a coin fails here, the next step is to look for its mechanism
in the slippage, not to switch it off. A subgroup search over five coins
will always hand back a worst one.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import statistics as st
import time

import tally as T

START = T.START
MIN_N = 150

# ONLY THE LIVE STRATEGY, AND ONLY THE CRYPTO SERIES.
#
# The first run of this file pooled everything placed since 7 October: five
# stake sizes, the retired T-3 experiment and the retired oil session. That
# answers a question nobody asked. The live strategy is T-9 on the coins, so
# that is what gets measured.
#
# THE RECORD CAN ONLY PARTLY SAY, and the two cases are counted separately
# rather than blended. live.py has written a Strategy column since
# 2026-10-23; every row before that is blank because the column did not
# exist yet.
#
#   rows WITH a strategy  -- filtered on the record. Exact.
#   rows WITHOUT one      -- filtered by inference, and the inference is
#                            narrow and stated: oil is identifiable by its
#                            ticker, since WTI is not a coin, and T-3 by its
#                            $3 stake, which no other session used.
#
# The inference only EXCLUDES what is positively known to be something else.
# It does not guess which of the remainder was T-9, because a guess written
# into a result is indistinguishable from a measurement afterwards.
LIVE_STRATEGY = "v6"
T3_STAKE = 3.0
CRYPTO = ("BTC", "ETH", "SOL", "XRP", "DOGE")


def coin_of(ticker):
    """KXSOL15M-26NOV01... -> SOL. The series prefix IS the underlying."""
    tk = str(ticker or "").upper()
    if not tk.startswith("KX"):
        return "?"
    head = tk.split("-")[0][2:]
    return head[:-3] if head.endswith("15M") else head


def keep(bets):
    """T-9 on the coins only. Returns (kept, reasons) and explains itself."""
    out = []
    why = collections.Counter()
    for b in bets:
        coin = coin_of(b["ticker"])
        if coin not in CRYPTO:
            why[f"not a coin ({coin})"] += 1
            continue
        strat = (b.get("strategy") or "").strip().lower()
        if strat:
            if strat != LIVE_STRATEGY:
                why[f"strategy={strat} on the record"] += 1
                continue
            why["kept: strategy on the record"] += 1
        else:
            # Blank strategy: exclude only what is positively identifiable.
            if T.stake_of(b) == T3_STAKE:
                why["blank strategy, $3 stake -> T-3"] += 1
                continue
            why["kept: blank strategy, inferred T-9"] += 1
        out.append(b)
    return out, why


def load():
    """Bets with cost and revenue attached -- tally's logic, not a new one."""
    bets = T.rows_since(START)
    if not bets:
        return [], {}

    setts, cur, pages, got = {}, None, 0, 0
    while pages < 60:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = T.get("/portfolio/settlements", **p)
        rows = (d or {}).get("settlements") or []
        got += len(rows)
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break
        time.sleep(0.15)
    print(f"  read {got} settlement(s) across {pages} page(s)")

    # ONE SETTLEMENT PER MARKET, SHARED BY CONTRACT COUNT. Two orders have
    # reached one market before, and both rows claiming the whole payout is
    # how the headline once read +$12.93 while the truth was negative.
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
    est = 0
    for b in bets:
        f = fills.get(b["oid"])
        want = int(b["contracts"] or 0)
        if f and f["cost"] and want and abs(f["n"] - want) < 0.01:
            b["cost"] = f["cost"]
        else:
            # Costed at the LIMIT, which overstates spend. Counted and said
            # out loud rather than passed off as exact.
            b["cost"] = b["limit_cost"] or 0.0
            est += 1
        s = setts.get(b["ticker"])
        b["rev"] = (s["rev"] * share.get(id(b), 1.0)) if s else None
    if est:
        print(f"  NOTE: {est} bet(s) had no complete fill set and were costed "
              "at the limit, which overstates what they spent.")
    return bets, setts


def cell(group):
    """(n, won%, mean cost/contract, net $, ROI%, 95% half-width)."""
    n = len(group)
    if not n:
        return None
    wins = sum(1 for b in group if (b["rev"] or 0) > 0)
    staked = sum(b["cost"] or 0 for b in group)
    returned = sum(b["rev"] or 0 for b in group)
    net = returned - staked
    roi = 100.0 * net / staked if staked else 0.0
    # Entry is ALREADY IN CENTS on the sheet. The first version multiplied
    # by 100 again and printed "9192.95c" for a 91.93c entry. Cosmetic --
    # nothing below is computed from it -- but a wrong number in a report is
    # a wrong number, so it is fixed rather than explained in a footnote.
    px = [float(b["entry"]) for b in group if b["entry"]]
    mean_px = st.mean(px) if px else 0.0
    w = wins / n
    # Standard error on the win rate, as percentage points.
    se = 100.0 * math.sqrt(max(w * (1 - w), 0.0) / n)
    return n, 100.0 * w, mean_px, net, roi, 1.96 * se


def show(label, c, flag=""):
    if c is None:
        print(f"  {label:<10} no settled bets")
        return
    n, won, px, net, roi, ci = c
    thin = "  << TOO THIN" if n < MIN_N else ""
    print(f"  {label:<10} n {n:>4}   won {won:6.2f}% +/-{ci:5.2f}   "
          f"mean entry {px:5.2f}c   net ${net:+8.2f}   ROI {roi:+6.2f}%"
          f"{thin}{flag}")


def main():
    print("=" * 104)
    print(f"THE T-9 RECORD BY COIN, since {START} UTC")
    print("  T-9 on the five coins only. Oil and T-3 are excluded, and the")
    print("  filtering is itemised below so the exclusions are auditable.")
    print("  Real fills and real settlements. The sample is small and the")
    print("  intervals below are the point, not the decoration.")
    print("=" * 104)

    bets, setts = load()
    if not bets:
        print("  no placed bets in the record")
        return 1

    before = len(bets)
    bets, why = keep(bets)
    print(f"\n  {before} placed bets in the record; "
          f"{len(bets)} are T-9 on a coin")
    for k, v in sorted(why.items(), key=lambda kv: -kv[1]):
        print(f"      {v:>4}  {k}")
    if not bets:
        print("  nothing left after filtering")
        return 1

    settled = [b for b in bets if b["rev"] is not None]
    pending = [b for b in bets if b["rev"] is None]
    print(f"\n  {len(bets)} placed, {len(settled)} settled, "
          f"{len(pending)} still open")

    by = collections.defaultdict(list)
    for b in settled:
        by[coin_of(b["ticker"])].append(b)

    pooled = cell(settled)
    print(f"\n{'-' * 104}")
    print("EVERY COIN, against the pooled rate")
    print("-" * 104)
    show("POOLED", pooled)
    print()
    for coin in sorted(by, key=lambda k: -len(by[k])):
        show(coin, cell(by[coin]))

    # CONDITION 3. A coin positive overall and negative in one half is a
    # coin with a good week, not a coin with an edge.
    print(f"\n{'-' * 104}")
    print("BOTH CHRONOLOGICAL HALVES -- a sign that flips between them is "
          "noise")
    print("-" * 104)
    for coin in sorted(by, key=lambda k: -len(by[k])):
        g = sorted(by[coin], key=lambda b: b["ts"])
        if len(g) < 2 * (MIN_N // 3):
            print(f"  {coin:<10} too thin to halve ({len(g)} settled)")
            continue
        mid = len(g) // 2
        a, b2 = cell(g[:mid]), cell(g[mid:])
        same = (a[4] > 0) == (b2[4] > 0)
        print(f"  {coin:<10} first {a[4]:+6.2f}%  ({a[0]} bets)   "
              f"second {b2[4]:+6.2f}%  ({b2[0]} bets)   "
              f"{'SAME SIGN' if same else '*** FLIPS'}")

    # CONDITION 4. Is any coin actually distinguishable from the pool?
    print(f"\n{'-' * 104}")
    print("IS ANY COIN ACTUALLY DIFFERENT FROM THE POOL?")
    print("-" * 104)
    pw, pci = pooled[1], pooled[5]
    anything = False
    for coin in sorted(by, key=lambda k: -len(by[k])):
        c = cell(by[coin])
        gap = c[1] - pw
        band = math.sqrt(c[5] ** 2 + pci ** 2)
        verdict = "inside the noise"
        if abs(gap) > band:
            verdict = "*** OUTSIDE the interval"
            anything = True
        print(f"  {coin:<10} win rate {gap:+6.2f} points vs pooled, "
              f"interval +/-{band:5.2f}   {verdict}")
    if not anything:
        print("\n  NOTHING SEPARATES THEM. On this sample every coin is the")
        print("  same coin, and the spread between best and worst is what")
        print("  five groups of this size look like when they are identical.")

    print("\n" + "=" * 104)
    print("  WHAT THIS DOES AND DOES NOT LICENCE.")
    print()
    print("  It does not licence dropping a coin. Oil and T-3 were retired")
    print("  because there was a MECHANISM -- a clean quote and a bad fill,")
    print("  the same shape twice -- not because a number came back")
    print("  negative. A search across five coins always returns a worst")
    print("  one, and acting on that is how a record gets fitted.")
    print()
    print("  If a coin fails the bar above, the next step is to look for")
    print("  its mechanism in the slippage and the book depth. If it fails")
    print("  and there is no mechanism, the honest reading is variance.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

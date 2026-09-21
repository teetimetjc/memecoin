"""Search every rule in a grid, honestly.

THE WHOLE POINT IS THE DISCIPLINE, NOT THE SEARCH. Finding the best-looking
rule in a thousand is trivial and meaningless: with a thousand tries, the
best one looks excellent even when every rule is worthless. So this does
three things that a naive search does not.

  1. It PICKS on the training windows only, and scores the winner ONCE on
     later windows it never saw. One look, no going back.

  2. It measures the SELECTION COST -- how much the winner's training
     number overstates its holdout number. That gap is the size of the
     self-deception the search generates, and it is reported whether it
     flatters the result or not.

  3. It runs the same search on SHUFFLED data, where any real edge has been
     destroyed by construction but the number of combinations is unchanged.
     Whatever the best rule scores there is what "nothing" looks like after
     a search this wide. A winner that cannot beat that bar has not been
     shown to exist, however good its raw number is.

Step 3 is the one usually missing, and it is the one that answers the
question actually being asked: is this rule real, or is it the luckiest of
a thousand coin flips?

Reads the published payload, so it needs no credentials and touches
nothing. Places no orders.
"""

import json
import pathlib
import random
import sys

PAYLOAD = "dashboard/path_data.js"
STAKE = 10.0
TRAIN_FRAC = 0.6

COINS = ["ALL", "BTC", "DOGE", "ETH", "SOL", "XRP"]
LOS = [0, 5, 10, 15, 20, 25, 30]
HIS = [20, 25, 30, 35, 40, 45]
ENTRIES = [60.0, 120.0, 180.0]
TAKES = [1.5, 2.0, 2.5, 3.0]


def fee(n, p):
    import math
    return math.ceil(0.07 * n * p * (1 - p) * 100) / 100


def load():
    s = pathlib.Path(PAYLOAD).read_text()
    return json.loads(s[s.index("{"):s.rindex("}") + 1])


def setup(t, entry_s):
    """The trade this row would be at a given entry time, or None.

    Rebuilt from the RAW book rather than the payload's precomputed entry,
    because a rule that moves the entry time must be able to; otherwise
    every such rule silently trades nothing and reports a clean zero, which
    reads exactly like "tried it, no good".
    """
    ya, yb = t.get("ya"), t.get("yb")
    f, st = t.get("f"), t.get("st")
    if not ya or not yb or not f or not st:
        return None
    off = (entry_s - f) / st
    if off < 0 or abs(off - round(off)) > 0.01:
        return None
    i = int(round(off))
    if i >= len(ya) or i >= len(yb):
        return None
    a, b = ya[i], yb[i]
    if a is None or b is None:
        return None
    no_ask = 100.0 - b
    cheap_yes = a <= no_ask
    entry = a if cheap_yes else no_ask
    if entry <= 0 or entry >= 100:
        return None
    series = []
    for k in range(i, min(len(ya), len(yb))):
        bb, aa = yb[k], ya[k]
        series.append(None if (bb is None or aa is None)
                      else (bb if cheap_yes else 100.0 - aa))
    ab = t.get("ab")
    win = None if ab is None else (ab if cheap_yes else (not ab))
    return entry, series, win


def pnl(entry, series, win, take):
    e = entry / 100.0
    n = STAKE / e
    cost = STAKE + fee(n, e)
    target = entry * take
    if target < 100:
        for k in range(1, len(series)):
            v = series[k]
            if v is None:
                continue
            if v >= target:
                x = v / 100.0
                return n * x - cost - fee(n, x)
    if win is None:
        return None
    return (n if win else 0.0) - cost


def score(trades, coin, lo, hi, entry_s, take):
    """Per-trade mean and the windows it traded, for one rule."""
    by_win = {}
    tot, cnt = 0.0, 0
    for t in trades:
        if coin != "ALL" and t["s"] != coin:
            continue
        s = t.get("_e" + str(int(entry_s)))
        if s is None:
            continue
        entry, series, win = s
        if not (lo <= entry < hi):
            continue
        v = pnl(entry, series, win, take)
        if v is None:
            continue
        by_win.setdefault(t["t"], []).append(v)
        tot += v
        cnt += 1
    if not cnt:
        return None
    return {"ev": tot / cnt, "n": cnt, "total": tot, "windows": by_win}


def ci(res):
    """95% interval clustered BY WINDOW.

    Five coins in one window share a market move, so treating their trades
    as independent would shrink the interval by roughly the square root of
    five and manufacture significance that is not there.
    """
    cl = [sum(v) / len(v) for v in res["windows"].values()]
    if len(cl) < 2:
        return None, None
    m = res["ev"]
    var = sum(len(v) * (sum(v) / len(v) - m) ** 2
              for v in res["windows"].values()) / (len(cl) - 1)
    half = 1.96 * (var / len(cl)) ** 0.5
    return m - half, m + half


def search(trades, min_trades):
    """Best rule in the grid, plus every rule's score."""
    out = []
    for coin in COINS:
        for lo in LOS:
            for hi in HIS:
                if hi <= lo:
                    continue
                for entry_s in ENTRIES:
                    for take in TAKES:
                        r = score(trades, coin, lo, hi, entry_s, take)
                        if r and r["n"] >= min_trades:
                            r["rule"] = (coin, lo, hi, entry_s, take)
                            out.append(r)
    out.sort(key=lambda r: -r["ev"])
    return out


def label(rule):
    coin, lo, hi, entry_s, take = rule
    return (f"{coin} {lo}-{hi}c at +{entry_s/60:.0f}min, sell {take}x")


def main():
    D = load()
    trades = D["trades"]
    # Precompute each row's setup at each entry time once, not once per rule.
    for t in trades:
        for e in ENTRIES:
            t["_e" + str(int(e))] = setup(t, e)

    wins = sorted({t["t"] for t in trades})
    cut = wins[int(len(wins) * TRAIN_FRAC)]
    train = [t for t in trades if t["t"] < cut]
    hold = [t for t in trades if t["t"] >= cut]
    print(f"payload built {D.get('built')}")
    print(f"{len(wins)} windows, split at {cut}: "
          f"{len(train)} train rows, {len(hold)} holdout rows\n")

    ranked = search(train, min_trades=25)
    print(f"searched {len(ranked)} rules with 25+ training trades\n")
    if not ranked:
        print("nothing with enough trades yet.")
        return 0

    print("TOP 10 ON TRAINING DATA -- and what each did on the holdout")
    print(f"  {'rule':<38} {'train':>8} {'holdout':>9} {'n':>5}")
    kept = 0
    for r in ranked[:10]:
        h = score(hold, *r["rule"])
        hv = f"{h['ev']:+8.2f}" if h else "       -"
        if h and h["ev"] > 0:
            kept += 1
        print(f"  {label(r['rule']):<38} {r['ev']:+8.2f} {hv} "
              f"{h['n'] if h else 0:>5}")

    best = ranked[0]
    h = score(hold, *best["rule"])
    print(f"\nTHE WINNER, scored once on data it never saw")
    print(f"  {label(best['rule'])}")
    print(f"  train    {best['ev']:+.2f} a trade over {best['n']} trades")
    if h:
        lo, hi = ci(h)
        print(f"  holdout  {h['ev']:+.2f} a trade over {h['n']} trades"
              + (f"   95% CI {lo:+.2f} to {hi:+.2f}" if lo is not None else ""))
        print(f"  selection cost: {best['ev'] - h['ev']:+.2f} a trade "
              f"-- how much the search flattered it")

    # WHAT "NOTHING" LOOKS LIKE AFTER A SEARCH THIS WIDE.
    #
    # Shuffling the P&L between windows destroys any real edge while
    # leaving the number of combinations, the trade counts and the spread
    # untouched. The best rule found in that world is pure selection. If
    # the real winner is not clearly above it, the search has not found
    # anything -- it has just found its luckiest combination.
    print("\nTHE SAME SEARCH ON SHUFFLED DATA (any real edge destroyed)")
    # HOW THIS SHUFFLE HAS TO WORK, and how the first attempt got it wrong.
    #
    # The obvious shuffle -- permuting window labels -- changes nothing: a
    # rule's average is its total over its count, and neither depends on
    # which window a trade is filed under. It returned the winner's exact
    # score every time, which is the tell.
    #
    # What has to be destroyed is the link between WHICH rule selects a
    # trade and HOW that trade turns out. So each trade keeps its entry
    # price -- that fixes the economics, the band it falls in and the
    # target it needs -- but is handed the OUTCOME of a different trade
    # that entered at a similar price. After that, "DOGE at +2min" selects
    # a set of outcomes no different from any other rule's, while the grid,
    # the trade counts and the payoff structure stay exactly as they were.
    #
    # Whatever the best of 1,188 rules scores in that world is what pure
    # selection buys at this search width.
    rng = random.Random(7)
    nulls = []
    for trial in range(12):
        fake = [dict(t) for t in train]
        for e in ENTRIES:
            k = "_e" + str(int(e))
            buckets = {}
            for idx, t in enumerate(fake):
                s = t.get(k)
                if s is None:
                    continue
                buckets.setdefault(int(s[0] // 5), []).append(idx)
            for idxs in buckets.values():
                donors = idxs[:]
                rng.shuffle(donors)
                outcomes = [(fake[d][k][1], fake[d][k][2]) for d in donors]
                for idx, (series, win) in zip(idxs, outcomes):
                    fake[idx][k] = (fake[idx][k][0], series, win)
        rk = search(fake, min_trades=25)
        if rk:
            nulls.append(rk[0]["ev"])
    if nulls:
        nulls.sort()
        print(f"  best rule in a world with NO edge: "
              f"{sum(nulls)/len(nulls):+.2f} a trade on average, "
              f"up to {nulls[-1]:+.2f}")
        print(f"  our winner's training score:       {best['ev']:+.2f}")
        verdict = ("INSIDE the noise -- not evidence of anything"
                   if best["ev"] <= nulls[-1]
                   else "above every shuffled run -- worth a forward test")
        print(f"  -> {verdict}")

    print(f"\n{kept} of the top 10 stayed positive on the holdout "
          f"(5 is what a coin flip gives)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

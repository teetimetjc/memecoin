"""Test the cash-out idea on the 30-second price paths.

THE IDEA. A 15-minute market that lurches away from its strike in the first
minutes prices the far side cheap. Buy that cheap side, and sell it back the
moment it recovers to some multiple of what you paid -- take the profit
rather than waiting to see who wins at the close.

This is a bet on the PATH, which is why v6 and v7 say nothing about it. They
asked who wins at settlement; a take-profit never waits for settlement.

THE TRAP THIS FILE IS BUILT AROUND. There are two free parameters -- how
cheap is cheap, and how much profit to take -- plus when to enter. Sweeping
them produces dozens of combinations, and on a few hundred windows the best
of dozens will look excellent through luck alone. That is precisely how v6's
"edge" was manufactured: a cut was chosen because it looked good on the same
data it was then judged on.

So the parameters are chosen on the EARLIER 70% of windows and the winner is
then run once on the LATER 30%, which it has never seen. Only the holdout
number is a result. The training number is reported beside it purely so the
gap between them is visible, because that gap is the size of the self-
deception on offer.

EVERY PRICE IS THE ONE YOU WOULD ACTUALLY GET.
  entry  = the ASK (you pay the offer)
  exit   = the BID (you sell into the bid)
  fees   = charged TWICE, entering and exiting
A mid price on either side would invent an edge out of half a spread, and on
contracts this cheap the spread is most of the supposed profit.

UNFILLED TAKE-PROFITS ARE NOT WINS. If the level is never touched, the
position is held to settlement and wins or loses there -- which is where a
strategy like this actually bleeds, and leaving those rows out would be the
same mistake in a new costume.

Read-only. Places nothing.
"""

import math
import sys
from collections import defaultdict

SHEET = "Path"
PRED_H = []   # filled from predictor.ALL_HEADERS at run time

# The grid. Deliberately small: every extra combination is another lottery
# ticket in the search for a false positive.
# In SECONDS into the window, not sample index. A run dispatched late starts
# its series further in, so an index would mean a different moment on
# different rows -- and comparing +60s on one window to +7min on another is
# exactly the kind of quiet mismatch that produced the last false edge.
ENTRY_AT = (60, 120, 180)
MAX_ENTRY = (15.0, 25.0, 35.0)    # cents -- how cheap counts as cheap
TAKE = (1.5, 2.0, 3.0)            # sell at this multiple of what was paid


def _fee(n, p):
    return math.ceil(round(0.07 * n * p * (1 - p), 9) * 100) / 100.0


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(path_rows, pred_rows, headers):
    """Path rows joined to the settlement outcome for each window."""
    pi = {h: i for i, h in enumerate(headers) if h}

    def pn(r, k):
        i = pi.get(k, -1)
        return _f(r[i]) if 0 <= i < len(r) else None

    # Did the coin finish ABOVE the strike? The market's own YES/NO, so each
    # side of each trade can be resolved from it.
    above = {}
    for r in pred_rows[1:]:
        tgt, ev = pn(r, "K Target"), pn(r, "Price at Eval")
        if tgt and ev:
            ts = str(r[pi["Timestamp"]]).replace(" UTC", "").strip()
            above[(ts, r[pi["Symbol"]])] = ev > tgt
    if not path_rows or len(path_rows) < 2:
        return []
    hi = {h: i for i, h in enumerate(path_rows[0])}

    def cell(r, k):
        i = hi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    out = []
    for r in path_rows[1:]:
        ts = str(cell(r, "Timestamp")).replace(" UTC", "").strip()
        sym = cell(r, "Symbol")
        up = above.get((ts, sym))
        if up is None:
            continue                      # window not graded yet
        bids = [_f(x) for x in str(cell(r, "Yes Bids")).split(",")]
        asks = [_f(x) for x in str(cell(r, "Yes Asks")).split(",")]
        if len(bids) < 6 or len(asks) < 6:
            continue
        first = _f(cell(r, "First Offset s")) or 30.0
        step = _f(cell(r, "Step s")) or 30.0
        out.append({"ts": ts, "sym": sym, "above": up, "bids": bids,
                    "asks": asks, "first": first, "step": step})
    return out


def trade(row, entry_s, max_entry, take):
    """One window, one rule. Returns net P&L at $10, or None if no entry.

    Both sides are considered, and the CHEAP one is the setup: a market that
    ran away from its strike early prices one side down, and that is the side
    the idea is about. If both are cheap the book is wide and neither is a
    real opportunity, so it is skipped.
    """
    bids, asks = row["bids"], row["asks"]
    # Which sample IS the requested moment. A row that began after it simply
    # has no entry there and is skipped, rather than silently using its first
    # available price as though it were that moment.
    off = (entry_s - row["first"]) / row["step"]
    if off < 0 or abs(off - round(off)) > 0.01:
        return None
    entry_i = int(round(off))
    if entry_i >= len(asks):
        return None
    yes_ask, yes_bid = asks[entry_i], bids[entry_i]
    if yes_ask is None or yes_bid is None:
        return None
    no_ask = 100.0 - yes_bid          # buying NO means selling YES at the bid
    cheap_yes = yes_ask <= max_entry
    cheap_no = no_ask <= max_entry
    if cheap_yes == cheap_no:
        return None                    # neither, or both -- not the setup

    side_yes = cheap_yes
    entry = yes_ask if side_yes else no_ask
    if entry <= 0:
        return None
    stake = 10.0
    n = stake / (entry / 100.0)
    cost = stake + _fee(n, entry / 100.0)

    target = entry * take
    if target >= 100.0:
        return None                    # unreachable by construction

    for k in range(entry_i + 1, len(bids)):
        b, a = bids[k], asks[k]
        if b is None or a is None:
            continue
        # Exit sells into the BID of whichever side is held.
        exit_px = b if side_yes else (100.0 - a)
        if exit_px >= target:
            gross = n * exit_px / 100.0
            return round(gross - cost - _fee(n, exit_px / 100.0), 4)

    # Never touched: held to settlement, and this is where it bleeds.
    won = row["above"] if side_yes else (not row["above"])
    return round((n if won else 0.0) - cost, 4)


def score(rows, entry_s, max_entry, take):
    nets, keys = [], []
    for r in rows:
        v = trade(r, entry_s, max_entry, take)
        if v is None:
            continue
        nets.append(v)
        keys.append(r["ts"])
    if not nets:
        return None
    g = defaultdict(list)
    for v, k in zip(nets, keys):
        g[k].append(v)
    cl = [sum(v) / len(v) for v in g.values()]
    nb = len(nets)
    m = sum(nets) / nb
    if len(cl) < 2:
        return {"n": nb, "windows": len(cl), "ev": m, "ci": None, "total": sum(nets)}
    va = sum(len(v) * (c - m) ** 2 for v, c in zip(g.values(), cl)) / (len(cl) - 1)
    se = math.sqrt(va / len(cl))
    return {"n": nb, "windows": len(cl), "ev": round(m, 4),
            "ci": [round(m - 1.96 * se, 4), round(m + 1.96 * se, 4)],
            "total": round(sum(nets), 2)}


def run(path_rows, pred_rows, headers):
    rows = load(path_rows, pred_rows, headers)
    if len(rows) < 100:
        return {"error": f"only {len(rows)} graded path rows so far",
                "rows": len(rows)}

    wins = sorted({r["ts"] for r in rows})
    cut = wins[int(len(wins) * 0.7)]
    tr = [r for r in rows if r["ts"] < cut]
    te = [r for r in rows if r["ts"] >= cut]
    if len(te) < 40:
        return {"error": "holdout too small", "rows": len(rows)}

    # Choose on train only.
    best, grid = None, []
    for ei in ENTRY_AT:
        for me in MAX_ENTRY:
            for tk in TAKE:
                s = score(tr, ei, me, tk)
                if not s or s["n"] < 20:
                    continue
                grid.append({"entry_s": ei, "max_entry": me, "take": tk, **s})
                if best is None or s["ev"] > best["ev"]:
                    best = {"entry_s": ei, "max_entry": me, "take": tk, **s}
    if not best:
        return {"error": "no combination produced enough trades", "rows": len(rows)}

    held = score(te, best["entry_s"], best["max_entry"], best["take"])
    out = {
        "rows": len(rows), "windows": len(wins), "cut": cut,
        "n_train": len(tr), "n_test": len(te), "tested": len(grid),
        "best": {k: best[k] for k in ("entry_s", "max_entry", "take")},
        "train": {k: best[k] for k in ("n", "windows", "ev", "ci", "total")},
        "holdout": held,
    }
    if held and held["ci"]:
        out["verdict"] = ("EDGE" if held["ci"][0] > 0
                          else "NOTHING" if held["ci"][1] < 0 else "UNPROVEN")
    else:
        out["verdict"] = "TOO FEW TRADES"
    out["grid"] = sorted(grid, key=lambda g: -g["ev"])[:8]
    return out


def selftest():
    """Plant a bounce, and plant none, and require the right answer to both.

    A scorer that cannot see a real bounce would report "no edge" just as
    confidently as one that correctly finds nothing, which is the failure
    mode that let v6 run for weeks. So the null result only counts if the
    same code finds a planted effect.

    The FIRST version of this test was itself wrong, in the direction that
    matters: it random-walked the PROBABILITY and clamped it to [2c, 98c].
    Clamping is mean reversion -- a cheap side cannot drift below 2c, so it
    gets pushed back up -- and the "no edge" world quietly contained a
    planted bounce worth $1.50 a trade. A test that fabricates the effect it
    is checking for is worse than no test.

    So the price is now derived, not walked: simulate the COIN, and price the
    contract as the true probability of finishing above the strike given the
    time left. That is a martingale by construction, with no boundary to
    bounce off. World B then adds mean reversion to the coin itself, which is
    a real bounce rather than an artefact of the simulation.
    """
    import random
    rng = random.Random(11)
    PH = ["Timestamp", "Symbol", "Ticker", "Strike", "Spot at Open",
          "First Offset s", "Step s", "Samples", "Yes Bids", "Yes Asks", "Note"]
    ph = {h: i for i, h in enumerate(PRED_H) if h}

    def ncdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def world(revert, n_win=900, vol=0.0012, spread=1.0):
        prows, preds = [PH], [PRED_H]
        for w in range(n_win):
            ts = f"2026-02-{w//96+1:02d} {(w%96)//4:02d}:{(w%4)*15:02d}"
            for c in range(5):
                # Strike offset from spot, in the same units as the walk.
                x = rng.gauss(0, vol * 3)
                bids, asks = [], []
                for k in range(27):
                    left = max(27 - k, 1) / 30.0        # fraction of window left
                    sd = vol * math.sqrt(left * 30)
                    p = ncdf(-x / sd) if sd > 0 else (1.0 if x < 0 else 0.0)
                    mid = p * 100.0
                    bids.append(round(max(1.0, mid - spread / 2), 1))
                    asks.append(round(min(99.0, mid + spread / 2), 1))
                    # advance the coin; `revert` pulls it back toward the strike
                    x += rng.gauss(0, vol) - revert * x
                up = x < 0            # finished above the strike
                prows.append([ts, f"C{c}", "T", 1.0, "", 30, 30, 27,
                              ",".join(str(v) for v in bids),
                              ",".join(str(v) for v in asks), ""])
                r = [""] * len(PRED_H)
                r[ph["Timestamp"]] = ts
                r[ph["Symbol"]] = f"C{c}"
                r[ph["K Target"]] = 1.0
                r[ph["Price at Eval"]] = 1.1 if up else 0.9
                preds.append(r)
        return prows, preds

    ok = True
    a_ = run(*world(0.0), PRED_H)
    if a_.get("verdict") == "EDGE":
        print(f"  FAIL: found an edge in a fair market "
              f"(holdout EV ${a_['holdout']['ev']:+.3f})")
        ok = False
    else:
        print(f"  pass: fair market -> {a_.get('verdict')} "
              f"(holdout EV ${a_['holdout']['ev']:+.3f})")
    b_ = run(*world(0.25), PRED_H)
    if b_.get("verdict") != "EDGE":
        print(f"  FAIL: missed a planted bounce -> {b_.get('verdict')} "
              f"(holdout EV ${b_['holdout']['ev']:+.3f})")
        ok = False
    else:
        print(f"  pass: mean-reverting coin -> EDGE "
              f"(holdout EV ${b_['holdout']['ev']:+.3f})")
    print("  SELFTEST " + ("OK" if ok else "FAILED"))
    return 0 if ok else 1


def main():
    import predictor as P
    globals()["PRED_H"] = P.ALL_HEADERS
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        return selftest()
    client = P._get_client()
    sh = client.open_by_key(P.SPREADSHEET_ID)
    try:
        path_rows = sh.worksheet(SHEET).get_all_values()
    except Exception:
        print("  No Path tab yet -- nothing sampled.")
        return 1
    pred_rows = P.open_pred_sheet(client).get_all_values()
    res = run(path_rows, pred_rows, P.ALL_HEADERS)

    print("=" * 68)
    print("CASH-OUT TEST -- buy the cheap side early, sell into a bounce")
    print("=" * 68)
    if "error" in res:
        print(f"  {res['error']}")
        return 0
    print(f"  {res['rows']} graded paths over {res['windows']} windows")
    print(f"  chose on the first {res['n_train']}, judged on the last {res['n_test']}"
          f" (split {res['cut']})")
    b = res["best"]
    print(f"\n  BEST ON TRAIN of {res['tested']} combinations:")
    print(f"    enter at +{b['entry_s']:.0f}s · only if under {b['max_entry']:.0f}c"
          f" · sell at {b['take']:.1f}x")
    t = res["train"]
    print(f"    train   EV ${t['ev']:+.3f}/trade over {t['n']} trades")
    h = res["holdout"]
    if h:
        ci = f"95% CI ${h['ci'][0]:+.3f} to ${h['ci'][1]:+.3f}" if h["ci"] else "no CI"
        print(f"    HOLDOUT EV ${h['ev']:+.3f}/trade over {h['n']} trades  {ci}")
        print(f"            total ${h['total']:+.2f} across {h['windows']} windows")
    print(f"\n  VERDICT: {res['verdict']}")
    print("\n  Only the holdout counts. The train figure is shown so the gap")
    print("  between them is visible -- that gap is the self-deception on offer.")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Sweep the two order guards against the real order log. Read-only.

THE TWO LEVERS, and why these two and not another dozen ideas.

Every strategy variation tried lately has come back at zero -- hedging,
95/5 reversion, the NFL line gap, T-10, raising coverage, market making.
What has NOT come back at zero, twice now, is SELECTION: which bets you
end up holding matters far more than what you pay for them. The maker test
put a number on it, 15.8 points of settle rate between the windows a
resting bid would have filled and the ones it would have missed.

Both guards below are selection controls, so both are worth a sweep.

  SLIP_BUFFER_CENTS -- how far above the quote we will chase a RISING ask.
    65 of 229 orders once filled nothing because the ask moved up past a
    limit of quote+1c, and those markets settled YES 98.4% against 95.8%
    for the orders that did fill. A favourite whose price is climbing is a
    favourite getting stronger, and the bot was systematically missing
    them. Raised 1 -> 3 for that reason and never pushed further.

  MAX_DROP_CENTS -- how far the ask may have FALLEN before we refuse.
    The mirror case: a collapsing ask is a favourite the market is giving
    up on. Set at 5, and the recent damage clusters at drops of 4-5c,
    right at the threshold.

HOW A COUNTERFACTUAL FILL IS DECIDED, which is the part that makes this
measurable rather than a guess. live.py records `Presubmit c` on every
order: the executable ask read immediately before the order was sent. That
is exactly the number a limit has to reach.

    limit = min(MAX_LIMIT, ceil(entry + buffer))
    the order fills if limit >= presubmit
    and an IOC fills at the BOOK's price, so it pays presubmit, not limit

So for any buffer, every row in the log can be re-decided from numbers the
log already holds. Nothing is simulated about the market.

THIS IS IN-SAMPLE AND THAT IS A REAL LIMIT, stated here rather than in a
footnote. Both thresholds were chosen by looking at this same record, so a
sweep over it will flatter whatever is already set and any improvement it
finds is an upper bound. What the sweep is good for is DIRECTION and
SHAPE: a lever that improves monotonically across a wide range is a
different thing from one with a lucky spike at a single value.

WHAT IT CANNOT SEE. A larger limit might sweep deeper into the book and
pay worse than the presubmit ask on the marginal contracts; only the top
of book is recorded, so a chased fill is costed at the best offer and the
chase is therefore slightly flattered. And rows whose presubmit read
failed carry no counterfactual at all and are reported separately rather
than assumed to have filled.

Read-only. Places no order, writes no row, changes no setting.
"""
import collections
import math
import os
import time

import predictor as P
import live
import tally as T
import oddsmath as M
import coincut as CC

BUFFERS = tuple(float(x) for x in
                (os.environ.get("SWEEP_BUFFERS") or "1,2,3,4,5,6,8").split(",")
                if x.strip())
DROPS = tuple(float(x) for x in
              (os.environ.get("SWEEP_DROPS") or "2,3,4,5,7,10,99").split(",")
              if x.strip())
MAX_LIMIT = float(os.environ.get("MAX_LIMIT_CENTS") or 97.0)
STAKES = tuple(float(x) for x in
               (os.environ.get("SWEEP_STAKES") or "4,5").split(",") if x.strip())
MIN_N = int(os.environ.get("SWEEP_MIN_N") or 80)


def f(v):
    try:
        x = float(str(v).replace("$", "").strip())
        return x if x == x else None
    except (TypeError, ValueError, AttributeError):
        return None


def load():
    """Order rows with entry, presubmit, stake and the market's outcome."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        return [], 0, 0
    h = {k: i for i, k in enumerate(rows[0])}

    def g(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    # Settlements by ticker: what the market actually did.
    setts, cur, pages = {}, None, 0
    while pages < 60:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = T.get("/portfolio/settlements", **p)
        rs = (d or {}).get("settlements") or []
        T._ingest_settlements(rs, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rs:
            break
        time.sleep(0.15)

    out, no_presub, no_sett = [], 0, 0
    for r in rows[1:]:
        tk = str(g(r, "Ticker")).strip()
        if not tk or T.norm_ts(str(g(r, "Timestamp"))) < T.norm_ts(CC.START):
            continue
        # T-9 on a coin only, by the same rule coincut uses.
        if CC.coin_of(tk) not in CC.CRYPTO:
            continue
        strat = str(g(r, "Strategy")).strip().lower()
        if strat and strat != CC.LIVE_STRATEGY:
            continue
        side = str(g(r, "Side")).strip().upper()
        entry = f(g(r, "Entry ¢"))
        presub = f(g(r, "Presubmit ¢"))
        contracts = f(g(r, "Contracts")) or 0
        limit_c = f(g(r, "Limit ¢"))
        if entry is None:
            continue
        # The stake this row was placed at, recovered the way tally does.
        stake = None
        if contracts and limit_c:
            stake = round(contracts * limit_c / 100.0, 2)
        # Only the sizes the live rule runs.
        cand = [s for s in STAKES
                if stake is not None and abs(stake - s) < 0.75]
        stake = cand[0] if cand else None
        if stake is None:
            continue
        if presub is None:
            no_presub += 1
            continue
        s = setts.get(tk)
        if not s:
            no_sett += 1
            continue
        won_yes = str(s.get("result", "")).lower() == "yes"
        out.append(dict(tk=tk, entry=entry, presub=presub, stake=stake,
                        side=side,
                        won=(won_yes if side != "DOWN" else not won_yes)))
    return out, no_presub, no_sett


def score(bets, buffer_c, drop_c):
    """Replay every order under one (buffer, drop) pair."""
    n = fills = wins = 0
    staked = returned = 0.0
    for b in bets:
        # GUARD 1: refuse a collapse. The live test is strictly greater.
        if b["presub"] < b["entry"] - drop_c:
            continue
        n += 1
        limit = min(MAX_LIMIT, 99.0, math.ceil(b["entry"] + buffer_c))
        # GUARD 2: an IOC only fills if the limit reaches the book's ask.
        if limit < b["presub"]:
            continue
        # It fills AT THE BOOK, not at the limit.
        px = b["presub"]
        k = int(b["stake"] / (limit / 100.0))
        if k < 1:
            continue
        fills += 1
        cost = k * px / 100.0 + M.fee_cents(k, px / 100.0) / 100.0
        staked += cost
        if b["won"]:
            wins += 1
            returned += k * 1.0
    net = returned - staked
    roi = 100.0 * net / staked if staked else 0.0
    wr = 100.0 * wins / fills if fills else 0.0
    return dict(considered=n, fills=fills, wr=wr, staked=staked,
                net=net, roi=roi)


def main():
    print("=" * 100)
    print("GUARD SWEEP -- how far to chase a rising ask, how far to refuse "
          "a falling one")
    print("  Replayed on the real order log, using the presubmit ask each "
          "order recorded.")
    print("  IN-SAMPLE: both thresholds were set by looking at this record. "
          "Read the shape, not the peak.")
    print("=" * 100)

    bets, no_presub, no_sett = load()
    print(f"\n  {len(bets)} order(s) usable")
    print(f"  {no_presub} excluded: no presubmit ask recorded (the column "
          f"only exists from 2026-10-15)")
    print(f"  {no_sett} excluded: no settlement for the ticker")
    if len(bets) < MIN_N:
        print(f"\n  BELOW the {MIN_N} minimum set before the run. No verdict.")
        return 0

    live_b = float(os.environ.get("SLIP_BUFFER_CENTS") or 3.0)
    live_d = float(os.environ.get("MAX_DROP_CENTS") or 5.0)
    base = score(bets, live_b, live_d)
    print(f"\n  LIVE SETTINGS buffer {live_b:.0f}c, drop {live_d:.0f}c:  "
          f"{base['fills']} fill(s), won {base['wr']:.2f}%, "
          f"net ${base['net']:+,.2f}, ROI {base['roi']:+.2f}%")

    print(f"\n{'-' * 100}")
    print(f"1. CHASING A RISING ASK -- drop guard held at {live_d:.0f}c")
    print("-" * 100)
    print(f"  {'buffer':>8}{'fills':>8}{'won %':>9}{'staked $':>12}"
          f"{'net $':>11}{'ROI':>9}")
    for b in BUFFERS:
        r = score(bets, b, live_d)
        mark = "  <- live" if abs(b - live_b) < 0.01 else ""
        print(f"  {b:>7.0f}c{r['fills']:>8}{r['wr']:>9.2f}"
              f"{r['staked']:>12,.2f}{r['net']:>+11,.2f}{r['roi']:>+8.2f}%"
              f"{mark}")

    print(f"\n{'-' * 100}")
    print(f"2. REFUSING A FALLING ASK -- buffer held at {live_b:.0f}c")
    print("   (99c means the guard is effectively off)")
    print("-" * 100)
    print(f"  {'drop':>8}{'kept':>8}{'fills':>8}{'won %':>9}{'staked $':>12}"
          f"{'net $':>11}{'ROI':>9}")
    for d in DROPS:
        r = score(bets, live_b, d)
        mark = "  <- live" if abs(d - live_d) < 0.01 else ""
        print(f"  {d:>7.0f}c{r['considered']:>8}{r['fills']:>8}"
              f"{r['wr']:>9.2f}{r['staked']:>12,.2f}{r['net']:>+11,.2f}"
              f"{r['roi']:>+8.2f}%{mark}")

    # A lever worth believing moves smoothly. A single lucky cell does not.
    print(f"\n{'-' * 100}")
    print("BOTH HALVES, at each setting -- a value that only works in one "
          "half is noise")
    print("-" * 100)
    bets.sort(key=lambda b: b["tk"])
    mid = len(bets) // 2
    halves = (("first", bets[:mid]), ("second", bets[mid:]))
    print(f"  {'buffer':>8}" + "".join(f"{h:>14}" for h, _ in halves))
    for b in BUFFERS:
        cells = "".join(f"{score(part, b, live_d)['roi']:>13.2f}%"
                        for _, part in halves)
        print(f"  {b:>7.0f}c" + cells)
    print(f"\n  {'drop':>8}" + "".join(f"{h:>14}" for h, _ in halves))
    for d in DROPS:
        cells = "".join(f"{score(part, live_b, d)['roi']:>13.2f}%"
                        for _, part in halves)
        print(f"  {d:>7.0f}c" + cells)

    print("\n" + "=" * 100)
    print("  HOW TO READ IT.")
    print()
    print("  IN-SAMPLE. Both numbers now live were chosen off this record, so")
    print("  the sweep cannot be evidence that they are right -- it can only")
    print("  show whether the surface around them is flat, sloped or spiky.")
    print()
    print("  A SLOPE ACROSS SEVERAL ADJACENT VALUES is worth acting on. A")
    print("  SINGLE GOOD CELL beside bad neighbours is the shape noise makes,")
    print("  and this project has already been fooled by one of those.")
    print()
    print("  THE CHASE IS SLIGHTLY FLATTERED: only the top of book is")
    print("  recorded, so a larger limit is costed at the best offer even")
    print("  though it might have swept deeper and paid more.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

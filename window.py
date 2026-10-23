"""The same record read from several start dates. Read-only.

WHY. gate.py counts from 2026-10-14, when betting resumed YES-only after the
pause, and reports about 200 settled bets. The obvious objection is that the
live record starts a week earlier, on 10-07, and a sample this short needs
every bet it can get -- roughly 1,250 are required before the interval
separates a 1.4-point gap from break-even.

The defence for the later start is that the earlier week was a DIFFERENT
CONFIGURATION: it included DOWN bets and a larger stake era, both since
switched off. Pooling two configurations measures neither.

BUT THAT DEFENCE IS ALSO HOW A RECORD GETS FLATTERED. The switches were
thrown after seeing the results, so a start date chosen to begin just after
them is a decision made with knowledge of the outcome -- the same shape as
dropping the DOWN bets because they lost. And today's band work showed these
numbers move materially with the measurement window, which makes any single
chosen window suspicious on its own.

So this file does not choose. It prints the record from every plausible
start, with the configuration present in each, and lets the disagreement be
visible:

  the FULL record from tally.START
  the week before the restart, on its own
  the measurement run from the restart
  YES-only and all-sides for each, side by side

WHAT TO LOOK FOR. If the windows broadly agree, the start date never
mattered and the larger sample is simply better -- use it. If the earlier
week is much worse, that is either the old configuration showing up, or the
edge being unstable in time, and those two have very different
consequences. The side-by-side is what tells them apart: if YES-only in the
early week is fine and only the all-sides number is bad, the configuration
explains it; if YES-only is bad too, the window does.

Break-even is the group's own mean cost per contract, since a winning
contract pays exactly $1.00. Intervals are Wilson, as everywhere else.

Read-only.
"""
import collections

import tally as T

RESTART = "2026-10-14 00:00"
T9_STAKES = ("2", "4", "5", "6", "7")


def load(start):
    bets = T.rows_since(start)
    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        rows = (d or {}).get("settlements") or []
        T._ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break

    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    for _tk, g in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in g) or 1
        for x in g:
            x["_share"] = (int(x["contracts"] or 0) / tot) if len(g) > 1 else 1.0

    fills = T.actual_costs()
    for b in bets:
        f = fills.get(b["oid"])
        b["cost"] = (f["cost"] if (f and f["cost"]) else (b["limit_cost"] or 0.0))
        s = setts.get(b["ticker"])
        b["rev"] = (s["rev"] * b.get("_share", 1.0)) if s else None
        b["stake"] = str(T.stake_of(b) or "?")
    return bets


def score(bets, yes_only, t9_only=True):
    g = bets
    if yes_only:
        g = [b for b in g if str(b.get("side") or "").upper() in ("UP", "YES")]
    if t9_only:
        g = [b for b in g if b["stake"] in T9_STAKES]
    g = [b for b in g if b["rev"] is not None]
    if not g:
        return None
    n = len(g)
    wins = sum(1 for b in g if (b["rev"] or 0) > 0)
    staked = sum(b["cost"] or 0.0 for b in g)
    contracts = sum(int(b["contracts"] or 0) for b in g)
    net = sum(b["rev"] or 0.0 for b in g) - staked
    be = (100.0 * staked / contracts) if contracts else float("nan")
    wr = 100.0 * wins / n
    lo, hi = T.wilson(wins, n)
    if 100 * lo > be:
        verd = "EXCLUDES break-even -- separated"
    elif 100 * hi < be:
        verd = "EXCLUDES on the LOSING side"
    else:
        verd = "contains break-even -- unknown"
    return dict(n=n, wr=wr, be=be, gap=wr - be, net=net, staked=staked,
                lo=100 * lo, hi=100 * hi, verd=verd)


def line(label, s):
    if s is None:
        print(f"  {label:<34} no settled bets")
        return
    print(f"  {label:<34} {s['n']:>4} {s['wr']:>7.2f}% {s['be']:>8.2f}% "
          f"{s['gap']:>+7.2f} ${s['net']:>+7.2f} {s['lo']:>6.1f}-{s['hi']:<6.1f} "
          f"{s['verd']}")


def main():
    print("=" * 118)
    print("THE SAME RECORD FROM EVERY PLAUSIBLE START DATE")
    print("  The start date is a CHOICE. This prints the ones available so "
          "the choice is visible.")
    print("=" * 118)

    full = load(T.START)
    early = [b for b in full if T.norm_ts(b["ts"]) < RESTART]
    late = [b for b in full if T.norm_ts(b["ts"]) >= RESTART]

    print(f"\n  full record from {T.START}: {len(full)} bets placed")
    print(f"  before the restart ({RESTART}): {len(early)}")
    print(f"  from the restart onward: {len(late)}\n")

    # What configuration each window actually contains, so pooling can be
    # judged rather than assumed.
    def config(bets, name):
        sides = collections.Counter(
            str(b.get("side") or "?").upper() for b in bets)
        stakes = collections.Counter(b["stake"] for b in bets)
        print(f"  {name}")
        print(f"    sides : " + ", ".join(f"{k} {v}" for k, v in
                                          sides.most_common()))
        print(f"    stakes: " + ", ".join(f"${k} {v}" for k, v in
                                          sorted(stakes.items())))
    print("-" * 118)
    print("WHAT CONFIGURATION EACH WINDOW CONTAINS")
    print("-" * 118)
    config(early, f"before {RESTART}")
    config(late, f"from {RESTART}")
    print()

    hdr = (f"  {'window':<34} {'n':>4} {'won':>8} {'needs':>9} {'gap':>7} "
           f"{'net':>9} {'95% interval':<14} verdict")
    print("-" * 118)
    print("YES SIDE ONLY, frozen-spec stakes -- the configuration running now")
    print("-" * 118)
    print(hdr)
    y_full = score(full, True)
    y_early = score(early, True)
    y_late = score(late, True)
    line(f"FULL from {T.START[:10]}", y_full)
    line(f"early week only", y_early)
    line(f"from {RESTART[:10]} (the gate)", y_late)
    print()

    print("-" * 118)
    print("ALL SIDES, frozen-spec stakes -- includes the DOWN bets")
    print("-" * 118)
    print(hdr)
    line(f"FULL from {T.START[:10]}", score(full, False))
    line(f"early week only", score(early, False))
    line(f"from {RESTART[:10]}", score(late, False))
    print()

    print("=" * 118)
    print("  HOW TO READ IT")
    print()
    if y_full and y_late:
        d = y_full["gap"] - y_late["gap"]
        print(f"  Using the FULL record instead of the gate window changes "
              f"the gap by {d:+.2f} points")
        print(f"  and the sample from {y_late['n']} to {y_full['n']} bets.")
        print()
        if y_early is None:
            print("  The early week has no settled YES bets at a frozen-spec")
            print("  stake, so there is nothing to pool and the start date")
            print("  makes no difference. The gate window IS the record.")
        elif abs(d) < 1.0:
            print("  The two windows broadly AGREE, so the start date was")
            print("  never doing any work. Prefer the full record: same")
            print("  answer, more bets, and no date chosen after the fact.")
        else:
            print("  The two windows DISAGREE materially. Check the")
            print("  configuration block above before pooling: if the early")
            print("  week's YES-only number is the bad one, the old")
            print("  configuration does NOT explain it and the edge is")
            print("  moving in time -- which is the more serious reading and")
            print("  matches what the band work found today.")
    print()
    print("  Either way the sample is far short of the ~1,250 settled bets")
    print("  a 1.4-point gap needs, so no window here settles anything. The")
    print("  point of this file is to stop the start date being a hidden")
    print("  assumption, not to find a friendlier number.")
    print("=" * 118)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

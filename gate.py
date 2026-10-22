"""How far is the measurement run from its 200-bet gate? Read-only.

WHY A DEDICATED FILE. Every other reader starts at tally.START, 2026-10-07,
which is the whole live record. The measurement run is a narrower thing: it
began on 2026-10-14 when betting resumed YES-only after the pause, and it is
the ONLY cohort the 200-bet gate was ever about. Counting the whole record
against that gate would declare it reached days early, on bets from the $10
era the run exists to leave behind.

WHAT THE GATE IS FOR, since it is easy to mistake for a finish line. It is a
pre-declared point at which to read the confidence interval, nothing more.
The gap is positive now, but its interval still contains break-even, which
means the sample cannot yet tell a real four-point edge from a lucky run
around zero. More bets narrow the interval. At 200, IF the edge is near four
points, break-even should fall outside it.

THAT IS NOT A GUESS -- this record already contains a worked example. The
$4/$7 YES group reached 190 bets at 95.79% against a 91.64% break-even and
its interval, 91.9% to 97.9%, EXCLUDES break-even. So a four-point gap does
separate at roughly this sample size.

AND THE LIMIT, stated so the gate is not over-read: 200 bets resolves a gap
of about four points. It cannot resolve one of one or two points; that needs
thousands. If the run lands at 200 with a small positive gap and an interval
that still spans break-even, the honest conclusion is "still unknown", not
"close enough".

WHAT COUNTS. Settled bets only -- an unsettled bet has no outcome to score
-- on the YES side, since that is the configuration being measured. The T-3
session is excluded by its $3 stake; it is a different rule and its bets
belong to its own record.

Read-only.
"""
import collections

import tally as T

# When betting resumed, YES-only, after the pause. The gate counts from here
# and from nowhere else.
RESTART = "2026-10-14 00:00"
GATE = 200
# The T-3 test bets $3 and is a different strategy; the frozen spec has used
# $4, $5, $6 and its $2 ladder rung.
T9_STAKES = ("2", "4", "5", "6", "7")


def main():
    bets = T.rows_since(RESTART)
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

    up = [b for b in bets if str(b.get("side") or "").upper() in ("UP", "YES")]
    t9 = [b for b in up if b["stake"] in T9_STAKES]
    settled = [b for b in t9 if b["rev"] is not None]
    pending = len(t9) - len(settled)

    print("=" * 94)
    print(f"THE 200-BET GATE -- the measurement run only, from {RESTART}")
    print("=" * 94)
    print(f"  {len(bets)} bets placed since the restart")
    print(f"  {len(t9)} of them on the YES side at a frozen-spec stake "
          f"({'/'.join('$' + s for s in T9_STAKES)})")
    print(f"  {len(settled)} settled, {pending} still open\n")

    done = len(settled)
    bar = int(40.0 * min(1.0, done / GATE))
    print(f"  [{'#' * bar}{'.' * (40 - bar)}]  {done} / {GATE}")
    if done >= GATE:
        print("  THE GATE IS REACHED. Read the interval below and decide.\n")
    else:
        print(f"  {GATE - done} to go.\n")

    if not settled:
        print("  nothing settled yet")
        return 0

    n = len(settled)
    wins = sum(1 for b in settled if (b["rev"] or 0) > 0)
    staked = sum(b["cost"] or 0.0 for b in settled)
    net = sum(b["rev"] or 0.0 for b in settled) - staked
    contracts = sum(int(b["contracts"] or 0) for b in settled)
    be = (100.0 * staked / contracts) if contracts else float("nan")
    wr = 100.0 * wins / n
    lo, hi = T.wilson(wins, n)
    print(f"  won {wr:.2f}%   needs {be:.2f}%   gap {wr-be:+.2f}   "
          f"net ${net:+.2f}   ROI {100*net/staked if staked else 0:+.2f}%")
    print(f"  95% interval {100*lo:.1f}% to {100*hi:.1f}%  -- "
          + ("EXCLUDES break-even: the edge has separated"
             if 100 * lo > be else
             ("EXCLUDES break-even on the losing side" if 100 * hi < be
              else "CONTAINS break-even: still cannot tell")))
    print()

    print("  BY STAKE ERA, because the stake changed mid-run and the eras are")
    print("  also calendar periods -- read these as 'when', not as 'how big'.")
    g = collections.defaultdict(list)
    for b in settled:
        g[b["stake"]].append(b)
    for k in sorted(g):
        gg = g[k]
        w = 100.0 * sum(1 for b in gg if (b["rev"] or 0) > 0) / len(gg)
        st_ = sum(b["cost"] or 0.0 for b in gg)
        c_ = sum(int(b["contracts"] or 0) for b in gg)
        b_ = (100.0 * st_ / c_) if c_ else float("nan")
        nt = sum(b["rev"] or 0.0 for b in gg) - st_
        print(f"    ${k:<3} {len(gg):>4} bets   won {w:>6.2f}%   "
              f"needs {b_:>6.2f}%   gap {w-b_:>+6.2f}   net ${nt:>+7.2f}")

    print()
    print("  AT THE GATE, THE QUESTION IS THE INTERVAL AND NOTHING ELSE. A")
    print("  positive gap whose interval still spans break-even has not shown")
    print("  an edge; it has shown a sample too small to tell, and the right")
    print("  answer then is 'keep measuring', not 'raise the stake'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

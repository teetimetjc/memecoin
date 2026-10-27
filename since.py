"""The record from a given UTC moment to now: what just happened, hour by hour.

WHY THIS EXISTS. Every other reporting tool here splits the record by strategy
or by stake, and the stake eras ARE calendar periods, so a bad night shows up
smeared across a row that also contains the good days before it. When the
question is "what happened in the last twelve hours", those tools answer a
different question and answer it reassuringly.

So this takes a start timestamp and reports only after it:

    per strategy      so a bad window can be attributed to T-9, oil or the
                      paper shadow rather than to "the bot"
    per hour          because losses in this strategy cluster by close time
    per close window  the unit of risk: legs sharing a window resolve
                      together about 96% of the time, so three losses in one
                      window is ONE bad event, not three
    every loss listed with quote, fill and slippage

AND THE COMPARISON THAT STOPS A PANIC BEING A FINDING. A twelve-hour window
holds roughly a dozen bets. At a 4% loss rate the expected number of losses
is under one, so TWO losses is a normal night and FOUR is a bad one that is
still inside what variance does. The report therefore prints, beside the
window's result, what the same number of bets did on average across the whole
record -- because "we are down $18 since midnight" and "something has
changed" are different claims, and only the second one justifies touching a
setting.

THE OUTCOME IS KALSHI'S OWN SETTLEMENT, never re-derived from a price, and
the fill is the volume-weighted price actually paid from the fills endpoint.
Unsettled bets are reported separately as OPEN rather than counted as wins or
losses, because a 15-minute market placed ten minutes ago has no result yet
and guessing one would manufacture the number being asked about.

Read-only. Places nothing, changes no setting.
"""
import collections
import sys
import time

import tally as T

# Default: the last 12 hours. Override with a 'YYYY-MM-DD HH:MM' first arg.
HOURS = 12.0


def parse_start():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()[:16]
    return time.strftime("%Y-%m-%d %H:%M",
                         time.gmtime(time.time() - HOURS * 3600))


def cdt(ts):
    """Chicago time. UTC-5 while CDT holds."""
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
        return time.strftime("%m-%d %H:%M", time.gmtime(t - 5 * 3600))
    except Exception:
        return ts[:16]


def main():
    start = parse_start()
    # The whole record is pulled too, because the window means nothing on its
    # own -- it is only legible next to the rate it is being compared against.
    FULL = "2026-10-07 15:49"
    bets = T.rows_since(FULL)

    setts, cur, pages = {}, None, 0
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
    rows, open_rows = [], []
    for b in bets:
        f = fills.get(b["oid"])
        vwap = None
        if f and f.get("qty") and sum(f["qty"]):
            qs = sum(f["qty"])
            vwap = 100.0 * sum(p * n for p, n in zip(f["px"], f["qty"])) / qs
        cost = f["cost"] if (f and f.get("cost")) else (b["limit_cost"] or 0)
        rec = dict(ts=b["ts"], strategy=(b.get("strategy") or "").strip() or "?",
                   stake=str(T.stake_of(b) or "?"),
                   series=str(b["ticker"]).split("-")[0],
                   window=b["ticker"].rsplit("-", 1)[0],
                   entry=b["entry"], fill=vwap,
                   slip=(vwap - b["entry"]) if (vwap is not None and b["entry"])
                   else None,
                   cost=cost)
        s = setts.get(b["ticker"])
        if s is None:
            if b["ts"] >= start:
                open_rows.append(rec)
            continue
        rec["rev"] = s["rev"] * b.get("_share", 1.0)
        rec["pnl"] = rec["rev"] - cost
        rec["won"] = rec["rev"] > 0
        rows.append(rec)

    win = [r for r in rows if r["ts"] >= start]
    # The paper shadow is excluded from every money figure below. It places
    # nothing, so counting its rows as P&L would invent losses or profits
    # that no account ever saw.
    paper = [r for r in win if r["strategy"] == "low"]
    win = [r for r in win if r["strategy"] != "low"]
    real_all = [r for r in rows if r["strategy"] != "low"]

    print("=" * 100)
    print(f"SINCE {start} UTC  ({cdt(start + ':00')} CDT)   -- now "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC")
    print("=" * 100)
    if not win:
        print("  no settled real-money bets in this window.")
        if open_rows:
            print(f"  {len(open_rows)} bet(s) placed but not yet settled.")
        return 0

    net = sum(r["pnl"] for r in win)
    cost = sum(r["cost"] for r in win)
    nl = sum(1 for r in win if not r["won"])
    print(f"  {len(win)} settled bets, {len(win) - nl} won, {nl} lost")
    print(f"  staked ${cost:.2f}   net ${net:+.2f}   "
          f"ROI {(100.0 * net / cost if cost else 0):+.2f}%")
    if open_rows:
        print(f"  plus {len(open_rows)} bet(s) still OPEN -- no result yet, "
              "not counted above")
    if paper:
        pn = sum(r["pnl"] for r in paper)
        print(f"  ({len(paper)} paper shadow bets excluded from every dollar "
              f"here; they would have been {pn:+.2f})")
    print()

    # IS THIS UNUSUAL, OR JUST A SMALL SAMPLE? The comparison that decides
    # whether anything is wrong.
    print("-" * 100)
    print("IS THIS ACTUALLY UNUSUAL?")
    print("-" * 100)
    base_n = len(real_all)
    base_nl = sum(1 for r in real_all if not r["won"])
    base_rate = base_nl / base_n if base_n else 0
    base_roi = (100.0 * sum(r["pnl"] for r in real_all)
                / sum(r["cost"] for r in real_all)) if base_n else 0
    exp_l = base_rate * len(win)
    print(f"  whole record     {base_n} bets, loss rate "
          f"{100 * base_rate:.2f}%, ROI {base_roi:+.2f}%")
    print(f"  this window      {len(win)} bets, loss rate "
          f"{100.0 * nl / len(win):.2f}%, ROI "
          f"{(100.0 * net / cost if cost else 0):+.2f}%")
    print(f"  expected losses in {len(win)} bets at the record's own rate: "
          f"{exp_l:.1f}; actual {nl}")
    print()
    print("  A window this short cannot separate bad luck from a broken rule.")
    print("  At the record's loss rate a dozen bets expect under one loss, so")
    print("  two is an ordinary night and four is a bad one that variance")
    print("  still produces regularly. What WOULD be diagnostic: losses")
    print("  concentrated in one close window (one event, not many), fills")
    print("  far below quote (a moving book, see the slippage column), or a")
    print("  strategy whose row below is alone in being negative.")
    print()

    print("-" * 100)
    print("BY STRATEGY")
    print("-" * 100)
    print(f"  {'strategy':<10} {'bets':>5} {'lost':>5} {'staked':>9} "
          f"{'net':>9} {'ROI':>9}")
    g = collections.defaultdict(list)
    for r in win:
        g[r["strategy"]].append(r)
    for k in sorted(g, key=lambda x: sum(r["pnl"] for r in g[x])):
        v = g[k]
        c = sum(r["cost"] for r in v)
        n = sum(r["pnl"] for r in v)
        print(f"  {k:<10} {len(v):>5} {sum(1 for r in v if not r['won']):>5} "
              f"{c:>9.2f} {n:>+9.2f} "
              f"{(100.0 * n / c if c else 0):>+8.2f}%")
    print()

    print("-" * 100)
    print("BY CLOSE WINDOW -- the unit of risk, worst first")
    print("-" * 100)
    w = collections.defaultdict(list)
    for r in win:
        w[r["window"]].append(r)
    worst = sorted(w, key=lambda x: sum(r["pnl"] for r in w[x]))
    print(f"  {len(w)} distinct close windows in this period")
    for k in worst[:8]:
        v = w[k]
        n = sum(r["pnl"] for r in v)
        if n >= 0:
            continue
        print(f"  {v[0]['ts'][:16]}  {cdt(v[0]['ts']):<12} {len(v)} leg(s)  "
              f"{n:>+8.2f}   {', '.join(r['series'] for r in v)}")
    print()

    print("-" * 100)
    print("EVERY LOSS IN THE WINDOW")
    print("-" * 100)
    L = [r for r in win if not r["won"]]
    if not L:
        print("  none")
    else:
        print(f"  {'when (UTC)':<17} {'CDT':<12} {'strat':<7} {'series':<10} "
              f"{'quote':>6} {'fill':>7} {'slip':>7} {'lost':>8}")
        for r in sorted(L, key=lambda x: x["ts"]):
            sl = f"{r['slip']:+.1f}c" if r["slip"] is not None else "   --"
            fl = f"{r['fill']:.1f}c" if r["fill"] is not None else "   --"
            print(f"  {r['ts'][:16]:<17} {cdt(r['ts']):<12} "
                  f"{r['strategy']:<7} {r['series']:<10} "
                  f"{r['entry']:>5.1f}c {fl:>7} {sl:>7} "
                  f"${r['pnl']:>+7.2f}")
    print()

    print("-" * 100)
    print("BY HOUR, Chicago time")
    print("-" * 100)
    h = collections.defaultdict(lambda: [0, 0, 0.0])
    for r in win:
        try:
            t = time.mktime(time.strptime(r["ts"][:19], "%Y-%m-%d %H:%M:%S"))
            k = time.strftime("%m-%d %H", time.gmtime(t - 5 * 3600))
        except Exception:
            continue
        h[k][0] += 1
        if not r["won"]:
            h[k][1] += 1
        h[k][2] += r["pnl"]
    for k in sorted(h):
        n, nl, p = h[k]
        print(f"  {k}:00 CDT  {n:>3} bets  {nl:>2} lost  {p:>+8.2f}")
    print()
    print("=" * 100)
    print("  THE FAST STOP, if this is to be stopped: set the Control tab to")
    print("  HALTED. It takes effect within a poll and needs no commit, no")
    print("  dispatch and no help from here.")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

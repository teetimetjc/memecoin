"""Every losing $4/$5 bet, beside the winners, looking for what they share.

THE QUESTION. The $4 and $5 eras are the profitable part of the record --
256 bets, about +$41 -- and they still contain ten losses. At 90-96c a loss
costs roughly twenty times what a win pays, so those ten are most of the
variance in the whole run. If they have something in common that is visible
BEFORE the bet is placed, the rule can avoid them. If they do not, they are
the price of doing business and the right response is to stop looking.

WHAT IS COMPARED, and the comparison is the point: every column is shown for
the losses AND for the wins. A property that looks striking in ten losses is
worth nothing until it is checked against the two hundred and forty-odd bets
that won -- "most losses were quoted near 90c" means nothing if most WINS
were too.

  ENTRY QUOTE. The band is 90-96c. A favourite at 90c is a different animal
  from one at 96c: it needs about 90.5% to break even against 96.5%, and it
  is by construction the least confident thing the rule will buy.

  SLIPPAGE, the fill minus the quote. A fill well BELOW the quote is not a
  bargain, it is the book gapping down between the decision and the order --
  buying a collapse. The $10 era lost on exactly this and MAX_DROP_CENTS
  exists because of it, so the question here is whether the guard is tight
  enough at 5c.

  HOUR OF DAY, in UTC and in the operator's Chicago time, because thin
  overnight books were a live hypothesis once and were later withdrawn on a
  recut of the data. Worth re-asking on this cohort.

  WINDOW CLUSTERING. Legs that share a close time resolve together about
  96% of the time, so two losses in one window are one loss, not two. A
  count of distinct windows says how many independent bad events there
  really were.

HOW TO READ THE RESULT, stated before the numbers so it cannot be bent after
them: ten losses is a tiny sample, and this file searches several properties
across it. Finding one that "explains" them is EXPECTED even if nothing is
there. Anything this turns up is a hypothesis to be pre-registered and tested
on data it was not found in -- not a rule change.

Read-only.
"""
import collections
import statistics as st
import time

import tally as T

START = "2026-10-07 15:49"
STAKES = ("4", "5")


def main():
    bets = T.rows_since(START)
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
    rows = []
    for b in bets:
        if str(T.stake_of(b) or "?") not in STAKES:
            continue
        s = setts.get(b["ticker"])
        if s is None:
            continue
        f = fills.get(b["oid"])
        vwap = None
        if f and f.get("qty") and sum(f["qty"]):
            qs = sum(f["qty"])
            vwap = 100.0 * sum(p * n for p, n in zip(f["px"], f["qty"])) / qs
        rows.append(dict(
            ts=b["ts"], series=str(b["ticker"]).split("-")[0],
            ticker=b["ticker"], entry=b["entry"], fill=vwap,
            slip=(vwap - b["entry"]) if (vwap is not None and b["entry"]) else None,
            won=(s["rev"] * b.get("_share", 1.0)) > 0,
            cost=(f["cost"] if (f and f.get("cost")) else (b["limit_cost"] or 0)),
            rev=s["rev"] * b.get("_share", 1.0)))

    L = [r for r in rows if not r["won"]]
    W = [r for r in rows if r["won"]]
    print("=" * 104)
    print(f"THE ${'/$'.join(STAKES)} COHORT: {len(rows)} settled bets, "
          f"{len(W)} won, {len(L)} lost")
    print("=" * 104)
    if not L:
        print("  no losses to inspect")
        return 0

    def cdt(ts):
        # The sheet stamps UTC. Chicago is UTC-5 in October (CDT).
        try:
            t = time.mktime(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
            return time.strftime("%m-%d %H:%M", time.gmtime(t - 5 * 3600))
        except Exception:
            return ts[:16]

    print("-" * 104)
    print("EVERY LOSS")
    print("-" * 104)
    print(f"  {'when (UTC)':<17} {'when (CDT)':<12} {'series':<8} "
          f"{'quote':>6} {'fill':>6} {'slip':>7} {'lost':>8}")
    for r in sorted(L, key=lambda x: x["ts"]):
        sl = f"{r['slip']:+.1f}c" if r["slip"] is not None else "   --"
        fl = f"{r['fill']:.1f}c" if r["fill"] is not None else "  --"
        print(f"  {r['ts'][:16]:<17} {cdt(r['ts']):<12} {r['series']:<8} "
              f"{r['entry']:>5.1f}c {fl:>6} {sl:>7} "
              f"${r['rev'] - r['cost']:>+7.2f}")
    print()

    def compare(name, fn, fmt="{:.2f}"):
        lv = [fn(r) for r in L if fn(r) is not None]
        wv = [fn(r) for r in W if fn(r) is not None]
        if not lv or not wv:
            return
        print(f"  {name:<28} losses {fmt.format(st.mean(lv)):>8}   "
              f"wins {fmt.format(st.mean(wv)):>8}   "
              f"diff {fmt.format(st.mean(lv) - st.mean(wv)):>8}")

    print("-" * 104)
    print("LOSSES AGAINST WINS -- a property only matters if it SEPARATES them")
    print("-" * 104)
    compare("entry quote (c)", lambda r: r["entry"])
    compare("fill price (c)", lambda r: r["fill"])
    compare("slippage fill-quote (c)", lambda r: r["slip"])
    print()

    print("-" * 104)
    print("ENTRY QUOTE, BUCKETED -- is the bottom of the band the problem?")
    print("-" * 104)
    print(f"  {'bucket':<12} {'bets':>5} {'lost':>5} {'loss rate':>10}")
    for lo in (90, 91, 92, 93, 94, 95):
        hi = lo + 1
        g = [r for r in rows if r["entry"] is not None and lo <= r["entry"] < hi]
        if not g:
            continue
        nl = sum(1 for r in g if not r["won"])
        print(f"  {lo}-{hi}c{'':<6} {len(g):>5} {nl:>5} "
              f"{100.0 * nl / len(g):>9.1f}%")
    print()

    print("-" * 104)
    print("SLIPPAGE BUCKETS -- is the drop guard at 5c tight enough?")
    print("-" * 104)
    print(f"  {'bucket':<16} {'bets':>5} {'lost':>5} {'loss rate':>10}")
    for lab, lo, hi in (("fill <= -4c", -99, -4), ("-4c..-2c", -4, -2),
                        ("-2c..0c", -2, 0), ("0c..+2c", 0, 2),
                        ("above +2c", 2, 99)):
        g = [r for r in rows if r["slip"] is not None and lo <= r["slip"] < hi]
        if not g:
            continue
        nl = sum(1 for r in g if not r["won"])
        print(f"  {lab:<16} {len(g):>5} {nl:>5} {100.0 * nl / len(g):>9.1f}%")
    print()

    print("-" * 104)
    print("WHEN -- hour of day, Chicago time")
    print("-" * 104)
    hrs = collections.defaultdict(lambda: [0, 0])
    for r in rows:
        try:
            t = time.mktime(time.strptime(r["ts"][:19], "%Y-%m-%d %H:%M:%S"))
            h = int(time.strftime("%H", time.gmtime(t - 5 * 3600)))
        except Exception:
            continue
        hrs[h][0] += 1
        if not r["won"]:
            hrs[h][1] += 1
    for h in sorted(hrs):
        n, nl = hrs[h]
        bar = "#" * nl
        print(f"  {h:02d}:00 CDT   {n:>4} bets  {nl:>2} lost  {bar}")
    print()

    wins_by_series = collections.Counter(r["series"] for r in rows)
    loss_by_series = collections.Counter(r["series"] for r in L)
    print("-" * 104)
    print("BY SERIES")
    print("-" * 104)
    for s in sorted(wins_by_series, key=lambda x: -loss_by_series[x]):
        n, nl = wins_by_series[s], loss_by_series[s]
        print(f"  {s:<12} {n:>4} bets  {nl:>2} lost  {100.0*nl/n:>5.1f}%")
    print()

    wins_w = len({r["ticker"].rsplit("-", 1)[0] for r in L})
    print(f"  the {len(L)} losses fall in {wins_w} distinct close window(s) -- "
          "legs sharing a window resolve together ~96% of the time, so that")
    print("  is the number of independent bad events.")
    print()
    print("=" * 104)
    print("  HOW TO READ IT. Ten losses, several properties searched. Finding")
    print("  one that separates them is EXPECTED even when nothing is there.")
    print("  Anything here is a hypothesis to pre-register and test on data it")
    print("  was not found in -- not a reason to move the band today.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

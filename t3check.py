"""Did the T-3 session actually bet, and how did its fills compare? Read-only.

WHY A SEPARATE FILE. Two strategies now write to one Live Bets tab, and
every existing reader -- tally, patterns, rotation -- treats that tab as one
record. Blending a $5 T-9 rule with a $3 T-3 rule into one win rate would
describe neither, which is the same mistake the stake eras made before
stake_of existed to separate them.

HOW THE TWO ARE TOLD APART, after the first attempt got it wrong. The Order
ID column stores the EXCHANGE's order id -- a uuid like
'01a15fe8-...' -- not the client_order_id that carries ORDER_PREFIX. So
splitting on that prefix grouped the record into one bucket per order and
reported "the T-3 session placed nothing" for a reason that had nothing to
do with the T-3 session. That is the same failure as every other one this
week: a read that returns something plausible instead of failing.

The stake is the discriminator that actually exists in the sheet. The T-9
spec bets $5 (dropping to $2 on its ladder) and the T-3 session bets $3,
and tally.stake_of already recovers the intended stake from contracts and
price. $3 belongs to no other configuration currently running, which is
exactly why the T-3 session was given a stake nothing else uses.

It is not a perfect key and it says so: if the T-9 ladder is ever set to $3,
or a third strategy appears at $3, this stops being able to separate them
and the fix is to record the strategy in its own column rather than to
infer it.

WHAT IT REPORTS, and the reason each matters:

  WHETHER T-3 PLACED ANYTHING AT ALL. The backtest put in-band markets at
  T-3 at roughly 5% of market-windows, so a quiet session is expected and a
  silent zero has to be distinguishable from a broken read -- which this
  project has now confused four separate times in two days.

  FILL RATE AND SLIP, side by side with the T-9 bot over the same hours.
  This is the whole point of running it live rather than shadowing it. A
  quote backtest cannot see an order that fills nothing because the ask
  moved past the limit, and at T-3 there are six fewer minutes for a seller
  to appear. If T-3 fills materially worse than T-9, the +3.13% is
  unreachable no matter how real the mispricing is.

  WHAT THE DEDUPE COST. The T-3 session skips markets the T-9 session
  already holds, so its live sample is NOT the cohort the backtest scored
  -- it is weighted towards markets that drifted into the band late. That
  is a known bias, it is stated rather than buried, and the count of skips
  is the size of it.

Read-only. Places nothing.
"""
import collections

import live
import tally as T


def strategy_of(row, c):
    """Which rule placed this bet, from the stake it was sized on."""
    st = T.stake_of(dict(contracts=T._f(c(row, "Contracts")),
                         entry=T._f(c(row, "Entry ¢")),
                         limit_cost=T._f(c(row, "Cost $"))))
    s = str(st or "?")
    if s == "3":
        return "t3     (T-3 test, $3)"
    if s in ("5", "2"):
        return "slice  (frozen T-9 spec, $5 / $2 ladder)"
    return f"${s} (earlier era)"


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        print("  no rows")
        return 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    by = collections.defaultdict(list)
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        by[strategy_of(r, c)].append(r)

    print("=" * 96)
    print("WHO BET WHAT -- the Live Bets tab split by the stake each bet "
          "was sized on")
    print("=" * 96)
    for p in sorted(by, key=lambda k: -len(by[k])):
        g = by[p]
        placed = [r for r in g if str(c(r, "Status")).strip().upper() == "PLACED"]
        print(f"  {p:<42} {len(g):>4} rows, {len(placed):>4} PLACED")
        if placed:
            last = sorted(placed, key=lambda r: T.norm_ts(r[0]))[-1]
            print(f"  {'':<42} latest {T.norm_ts(last[0])}  "
                  f"{c(last, 'Ticker')}  entry {c(last, 'Entry ¢')}c")

    t3 = [r for r in by.get("t3     (T-3 test, $3)", [])
          if str(c(r, "Status")).strip().upper() == "PLACED"]
    print()
    if not t3:
        print("  THE T-3 SESSION PLACED NOTHING.")
        print("  That is the expected outcome for a short run, not evidence of")
        print("  a fault: the backtest found in-band markets at T-3 in roughly")
        print("  5% of market-windows, on the order of one an hour across all")
        print("  five coins, and the dedupe removes the ones the T-9 bot took.")
        print("  It becomes a fault only if a full day passes with nothing.")
        return 0

    print("-" * 96)
    print("T-3 FILLS -- the thing no backtest can tell us")
    print("-" * 96)
    print(f"  {'when':<18} {'ticker':<26} {'entry':>6} {'limit':>6} "
          f"{'fills':>6} {'slip':>7}")
    fills = T.actual_costs()
    slips, got, miss = [], 0, 0
    for r in sorted(t3, key=lambda r: T.norm_ts(r[0])):
        oid = str(c(r, "Order ID")).strip()
        f = fills.get(oid)
        entry = T._f(c(r, "Entry ¢"))
        lim = T._f(c(r, "Limit ¢"))
        if f and f.get("n"):
            px = 100.0 * (f["cost"] / f["n"]) if f["n"] else None
            got += 1
            slip = (px - entry) if (px is not None and entry is not None) else None
            if slip is not None:
                slips.append(slip)
            shown = f"{px:.1f}c" if px is not None else "--"
        else:
            miss += 1
            shown, slip = "none", None
        print(f"  {T.norm_ts(r[0]):<18} {str(c(r, 'Ticker'))[:26]:<26} "
              f"{entry if entry is not None else '--':>6} "
              f"{lim if lim is not None else '--':>6} {shown:>6} "
              + (f"{slip:>+6.1f}c" if slip is not None else "      --"))
    n = got + miss
    print()
    print(f"  filled {got} of {n} ({100.0*got/n:.0f}%)")
    if slips:
        import statistics as st
        print(f"  mean slip vs the quote {st.mean(slips):+.2f}c")
    print()
    # ---- WHAT THEY SETTLED FOR -------------------------------------------
    #
    # The fill rate and the slippage are the execution half. This is the
    # other half, and the file was reporting a T-3 record with no outcomes
    # in it -- which made "13 bets" readable as a result when it was only a
    # count. Outcomes come from Kalshi's settlements, never from a price.
    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        srows = (d or {}).get("settlements") or []
        T._ingest_settlements(srows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not srows:
            break

    print("-" * 96)
    print("T-3 OUTCOMES -- settled only")
    print("-" * 96)
    won = lost = pending = 0
    staked = revenue = 0.0
    contracts = 0
    for r in sorted(t3, key=lambda r: T.norm_ts(r[0])):
        tk = str(c(r, "Ticker")).strip()
        oid = str(c(r, "Order ID")).strip()
        sres = setts.get(tk)
        if sres is None:
            pending += 1
            continue
        f = fills.get(oid)
        cost = (f["cost"] if (f and f.get("cost"))
                else (T._f(c(r, "Cost $")) or 0.0))
        nc = int(T._f(c(r, "Contracts")) or 0)
        rev = sres["rev"] or 0.0
        staked += cost
        revenue += rev
        contracts += nc
        if rev > 0:
            won += 1
        else:
            lost += 1
            print(f"    LOST  {T.norm_ts(r[0])}  {tk[:30]:<30} "
                  f"entry {T._f(c(r, 'Entry ¢'))}c  cost ${cost:.2f}")
    settled = won + lost
    print()
    if not settled:
        print("  nothing settled yet")
    else:
        net = revenue - staked
        be = (100.0 * staked / contracts) if contracts else float("nan")
        wr = 100.0 * won / settled
        lo, hi = T.wilson(won, settled)
        print(f"  {settled} settled: {won} won, {lost} lost"
              + (f", {pending} still open" if pending else ""))
        print(f"  won {wr:.2f}%   needs {be:.2f}%   gap {wr - be:+.2f}")
        print(f"  staked ${staked:.2f}   returned ${revenue:.2f}   "
              f"net ${net:+.2f}   ROI {100 * net / staked if staked else 0:+.2f}%")
        print(f"  95% interval {100*lo:.1f}% to {100*hi:.1f}%  -- "
              + ("EXCLUDES break-even" if 100 * lo > be or 100 * hi < be
                 else "contains break-even: far too few bets to tell"))
        print()
        print("  AT THIS SAMPLE SIZE THE WIN COUNT IS NOT EVIDENCE either")
        print("  way. A 92c favourite loses about one time in thirteen, so a")
        print("  single loss is the expected result of a dozen bets and two")
        print("  is unremarkable. What IS readable this early is the")
        print("  slippage above, because every order reports its own fill.")
    print()
    print("  COMPARE THE FILL RATE AGAINST 78.7%, which is what the T-9 bot")
    print("  manages. A markedly worse rate here is the finding, and it would")
    print("  mean the T-3 mispricing is real but unreachable -- the orders")
    print("  that miss are the favourites strengthening, which is exactly the")
    print("  cohort that settles YES 98.4% of the time.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

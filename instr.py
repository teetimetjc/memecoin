"""What the new instrumentation has collected so far.

Three tabs, added 14-15 Oct: the execution columns on Live Bets, the Shadow
counterfactual log, and the Aborts log. This reads all three and reports
what they say, including when they say nothing yet -- an empty tab is a
finding about deployment, not an absence of news.

Read-only.
"""
import collections
import statistics as st
import tally as T
import live


def num(v):
    return T._f(v)


def tab(sh, name):
    try:
        return sh.worksheet(name).get_all_values()
    except Exception:
        return []


def pct(vals, f):
    s = sorted(vals)
    return s[min(len(s) - 1, int(f * len(s)))]


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)

    # ---- Live Bets execution columns -----------------------------------
    rows = tab(sh, live.LIVE_SHEET)
    h = {k: i for i, k in enumerate(rows[0])} if rows else {}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    body = [r for r in rows[1:] if r and r[0]]
    have = [r for r in body if num(c(r, "Presubmit ¢")) is not None]
    print("=" * 88)
    print("1. EXECUTION COLUMNS on Live Bets")
    print("=" * 88)
    print(f"  {len(have)} of {len(body)} rows carry them "
          "(they start when the instrumented session did)\n")
    if have:
        ages = [num(c(r, "Quote age s")) for r in have]
        ages = [a for a in ages if a is not None]
        acks = [num(c(r, "Ack ms")) for r in have]
        acks = [a for a in acks if a is not None]
        drops = []
        for r in have:
            e, p = num(c(r, "Entry ¢")), num(c(r, "Presubmit ¢"))
            if e is not None and p is not None:
                drops.append((e - p, str(c(r, "Status")), r))
        if ages:
            print(f"  quote age   median {st.median(ages):.1f}s   "
                  f"p90 {pct(ages,.9):.1f}s   max {max(ages):.1f}s")
        if acks:
            print(f"  ack latency median {st.median(acks):.0f}ms  "
                  f"p90 {pct(acks,.9):.0f}ms  max {max(acks):.0f}ms")
        if drops:
            d = [x[0] for x in drops]
            print(f"  drop at send: median {st.median(d):+.1f}c  "
                  f"worst {max(d):+.1f}c  "
                  f"over 3c: {sum(1 for v in d if v > 3)}  "
                  f"over 5c (declined): {sum(1 for v in d if v > 5)}")
            print()
            print("  VISIBLE DROP AT SEND vs WHAT HAPPENED:")
            for lab, lo, hi in (("<=0c (ask rose or flat)", -99, 0.001),
                                ("0-1c", 0.001, 1), ("1-3c", 1, 3),
                                ("3-5c", 3, 5), (">5c (declined)", 5, 99)):
                g = [x for x in drops if lo <= x[0] < hi]
                placed = [x for x in g if x[1] == "PLACED"]
                print(f"    {lab:<24} {len(g):>3} signals, "
                      f"{len(placed):>3} placed")

    # ---- Shadow ---------------------------------------------------------
    srows = tab(sh, "Shadow")
    print()
    print("=" * 88)
    print("2. SHADOW counterfactual log")
    print("=" * 88)
    if not srows:
        print("  tab not created yet")
    else:
        sh_h = {k: i for i, k in enumerate(srows[0])}

        def sc(r, k):
            i = sh_h.get(k)
            return r[i] if i is not None and i < len(r) else ""
        sb = [r for r in srows[1:] if r and r[0]]
        print(f"  {len(sb)} rows\n")
        sp = [num(sc(r, "Spread ¢")) for r in sb]
        sp = [x for x in sp if x is not None]
        dp = [num(sc(r, "Ask depth")) for r in sb]
        dp = [x for x in dp if x is not None]
        if sp:
            print(f"  spread     median {st.median(sp):.1f}c  "
                  f"p90 {pct(sp,.9):.1f}c  max {max(sp):.1f}c")
        if dp:
            print(f"  ask depth  median {st.median(dp):,.0f} contracts  "
                  f"p10 {pct(dp,.1):,.0f}  min {min(dp):,.0f}")
        lv = [num(sc(r, "Levels to fill")) for r in sb]
        lv = [x for x in lv if x is not None]
        if lv:
            print(f"  levels to fill a 4-lot: "
                  + ", ".join(f"{k}:{v}" for k, v in
                              sorted(collections.Counter(lv).items())))
        print()
        for rule in ("R_spread", "R_depth", "R_crossed", "R_askmove"):
            cc = collections.Counter(
                (str(sc(r, rule)).split(":")[0] or "blank") for r in sb)
            print(f"    {rule:<12} {dict(cc)}")
        ab = [(num(sc(r, "Abort bid ¢")), num(sc(r, "Abort net ¢")),
               num(sc(r, "Book ¢ ask"))) for r in sb]
        ab = [x for x in ab if x[0] is not None]
        if ab:
            print(f"\n  abort bid recorded on {len(ab)} filled row(s); "
                  f"median net {st.median([x[1] for x in ab]):.1f}c")

    # ---- Aborts ---------------------------------------------------------
    arows = tab(sh, "Aborts")
    print()
    print("=" * 88)
    print("3. ABORTS log (the sell path, still unarmed)")
    print("=" * 88)
    if not arows:
        print("  tab not created yet -- no instrumented live session has")
        print("  reached the abort check, or no order has filled under it.")
    else:
        a_h = {k: i for i, k in enumerate(arows[0])}

        def ac(r, k):
            i = a_h.get(k)
            return r[i] if i is not None and i < len(r) else ""
        ab2 = [r for r in arows[1:] if r and r[0]]
        print(f"  {len(ab2)} rows")
        cc = collections.Counter(str(ac(r, "Decision")) for r in ab2)
        print(f"  decisions: {dict(cc)}")
        for r in ab2[-8:]:
            print(f"    {ac(r,'Timestamp')} {str(ac(r,'Ticker'))[:22]:<22} "
                  f"quote {ac(r,'Quote ¢')} fill {ac(r,'Fill ¢')} "
                  f"bid {ac(r,'Bid ¢')} net {ac(r,'Sell net ¢')} "
                  f"edge {ac(r,'Edge vs hold ¢')} -> {ac(r,'Decision')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

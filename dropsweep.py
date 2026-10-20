"""Where should the pre-send drop guard sit? Swept against real outcomes.

THE FILTER PEOPLE WANT IS NOT AVAILABLE. "Only take fills above 85c" is a
rule about the fill, and the fill is decided after the order is sent. A buy
limit caps what you pay; nothing puts a floor under it. Deleting bad fills
from a dashboard describes a record, not a strategy.

WHAT IS AVAILABLE is the drop seen immediately BEFORE sending -- entry minus
the pre-submission ask. That number is now recorded on every bet, so the
threshold can be chosen from outcomes instead of argument.

For each candidate threshold this reports what would have been declined,
what those bets actually did, and what the kept record looks like. A guard
that blocks losers is worth having; one that blocks winners is a tax.

Read-only.
"""
import tally as T
import live


def main():
    sh = T.P._get_client().open_by_key(T.P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    setts = {}
    cur, pages = None, 0
    while pages < 60:
        q = dict(limit=200)
        if cur:
            q["cursor"] = cur
        d = T.get("/portfolio/settlements", **q)
        batch = (d or {}).get("settlements") or []
        T._ingest_settlements(batch, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not batch:
            break

    fills = T.actual_costs()
    bets = []
    for r in rows[1:]:
        if not r or not r[0] or str(c(r, "Status")).strip() != "PLACED":
            continue
        pre = T._f(c(r, "Presubmit ¢"))
        ent = T._f(c(r, "Entry ¢"))
        tk = str(c(r, "Ticker"))
        if pre is None or ent is None or tk not in setts:
            continue
        oid = str(c(r, "Order ID")).strip()
        n = int(T._f(c(r, "Contracts")) or 0)
        f = fills.get(oid)
        cost = (f["cost"] if f and f["cost"] and abs(f["n"] - n) < 0.01
                else (T._f(c(r, "Cost $")) or 0.0))
        rev = setts[tk]["rev"] if setts[tk]["rev"] > 0 else 0.0
        bets.append(dict(drop=ent - pre, pnl=rev - cost))

    print("=" * 86)
    print("PRE-SEND DROP GUARD, SWEPT AGAINST OUTCOMES")
    print("=" * 86)
    print(f"  {len(bets)} bets carry a pre-submission ask\n")
    if not bets:
        return 0
    base = sum(b["pnl"] for b in bets)
    print(f"  as actually run: ${base:+.2f} over {len(bets)} bets\n")
    print("  %-11s %-10s %-12s %-12s %s" %
          ("threshold", "declined", "they made", "kept net", "vs actual"))
    for thr in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0):
        dec = [b for b in bets if b["drop"] > thr]
        kept = [b for b in bets if b["drop"] <= thr]
        kn = sum(b["pnl"] for b in kept)
        print("  >%-10.0f %-10d $%-11.2f $%-11.2f %+.2f"
              % (thr, len(dec), sum(b["pnl"] for b in dec), kn, kn - base))
    print()
    print("  BY BUCKET -- where each bet's pre-send drop actually sat:")
    print("  %-14s %-6s %-7s %-8s %-11s %s" %
          ("drop at send", "bets", "losses", "win %", "net", "per bet"))
    buckets = [("ask rose / flat", -99, 0.001), ("0 to 1c", 0.001, 1.0),
               ("1 to 2c", 1.0, 2.0), ("2 to 3c", 2.0, 3.0),
               ("3 to 4c", 3.0, 4.0), ("4 to 5c", 4.0, 5.0),
               ("over 5c", 5.0, 99)]
    for lab, lo, hi in buckets:
        g = [b for b in bets if lo <= b["drop"] < hi]
        if not g:
            print("  %-14s %-6d" % (lab, 0))
            continue
        L = [b for b in g if b["pnl"] <= 0]
        net = sum(b["pnl"] for b in g)
        print("  %-14s %-6d %-7d %-8.1f $%-10.2f $%+.3f"
              % (lab, len(g), len(L), 100.0*(len(g)-len(L))/len(g), net,
                 net/len(g)))
    print()
    print("  EVERY LOSS, with the drop that was visible before sending:")
    for b in sorted([x for x in bets if x["pnl"] <= 0],
                    key=lambda x: -x["drop"]):
        print("    drop %+5.1fc at send -> $%+.2f" % (b["drop"], b["pnl"]))
    print()
    print("  'they made' is what the declined bets actually returned. A")
    print("  NEGATIVE figure there means the guard removes losses; a positive")
    print("  one means it throws away profit.")
    print()
    print("  SMALL SAMPLE. The pre-submission ask has only been recorded")
    print("  since 2026-10-15, and the best-looking cell in a sweep is the")
    print("  one most likely to be luck. Treat a threshold as worth testing,")
    print("  never as proven by this table alone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

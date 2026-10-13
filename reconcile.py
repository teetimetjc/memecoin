"""Every fill the exchange has, against every bet our sheet recorded.

WHY. On 2026-10-13 the operator's app showed a DOGE payout of $20.00 while
our record held a single 10-contract DOGE bet that should pay $10.00. Either
the app aggregates differently or REAL BETS ARE MISSING FROM OUR RECORD --
and the second would matter a great deal, because sessions were cancelled and
restarted six times that night and a killed process can place an order and
die before writing the row to the sheet.

The exchange is the authority. This lists every ticker it has fills for, the
total contracts and cost per ticker, and marks any that our snapshot does not
contain. Anything marked MISSING is a bet that happened with real money and
was never scored.

Read-only.
"""
import collections, json, os, sys, urllib.request
import tally

def main():
    d = tally.get("/portfolio/fills", limit=1000)
    fills = (d or {}).get("fills") or []
    print(f"  {len(fills)} fill(s) from the exchange")
    agg = collections.defaultdict(lambda: dict(ct=0.0, cost=0.0, side=set(), n=0))
    for f in fills:
        tk = str(f.get("ticker") or "")
        if not tk: continue
        c = tally._f(f.get("count_fp")) or tally._f(f.get("count")) or 0.0
        side = str(f.get("side") or "")
        px = (tally._f(f.get("no_price_dollars")) if side == "no"
              else tally._f(f.get("yes_price_dollars"))) or 0.0
        a = agg[tk]
        a["ct"] += c; a["cost"] += c * px; a["side"].add(side); a["n"] += 1
    # our record
    url = "https://raw.githubusercontent.com/teetimetjc/memecoin/data/snapshot.json"
    try:
        snap = json.loads(urllib.request.urlopen(url, timeout=30).read())
        ours = {b["ticker"]: b for b in snap.get("bets") or []}
        print(f"  {len(ours)} bet(s) in our snapshot\n")
    except Exception as e:
        print(f"  could not read snapshot: {e}"); ours = {}
    print(f"  {'ticker':<34} {'fills':>5} {'contracts':>10} {'cost':>8} "
          f"{'side':>6}  {'ours':>9}")
    miss = 0
    for tk in sorted(agg, key=lambda t: -agg[t]["ct"]):
        a = agg[tk]
        b = ours.get(tk)
        mark = f"{b['contracts']:>9}" if b else "  MISSING"
        if not b: miss += 1
        print(f"  {tk:<34} {a['n']:>5} {a['ct']:>10.2f} {a['cost']:>8.2f} "
              f"{'/'.join(sorted(a['side'])):>6}  {mark}")
    print()
    print(f"  {len(agg)} ticker(s) traded on the exchange, {miss} not in our record")
    if miss:
        print("  ANY 'MISSING' LINE IS REAL MONEY THAT WAS NEVER SCORED.")
    else:
        print("  Every traded ticker is in our record. If the app shows a")
        print("  larger payout than the contracts here justify, the app is")
        print("  aggregating something, not showing an extra bet.")
    return 0

if __name__ == "__main__": sys.exit(main())

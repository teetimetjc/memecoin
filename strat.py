"""Every strategy's record, split by stake. Read-only.

WHY THIS EXISTS. The dashboard can split the record by stake OR by market,
but not by STRATEGY, and the three live bots are not the same bet: the
frozen spec enters at T-9 on five coins, the T-3 test enters six minutes
later in a narrower band, and the oil session runs the frozen rule on WTI.
Pooling them answers no question anyone has.

HOW EACH BET IS ATTRIBUTED, and the order matters:

  THE STRATEGY COLUMN FIRST, where it exists. live.py has written the
  ORDER_PREFIX since 2026-10-23, so recent rows say outright which bot
  placed them and nothing has to be guessed.

  OTHERWISE INFERRED, because rows older than that column are blank. A WTI
  ticker is the oil session; a $3 stake is the T-3 test; everything else at
  a frozen-spec stake is the T-9 bot. That inference is exactly as good as
  the stakes being distinct, which they are today and were not always, so
  inferred rows are counted separately and the split is printed.

BREAK-EVEN IS EACH GROUP'S OWN MEAN COST PER CONTRACT. A winning contract
pays exactly $1.00, so a group averaging 92.3c needs better than 92.3% plus
the fee to make money. Comparing every group to one number -- or worse to
50% -- is the mistake this whole project keeps circling back to.

COST IS THE FILL, NOT THE LIMIT. The sheet's Cost column holds contracts
times the limit price, which overstates spend by about a cent a contract.
tally.actual_costs reads the fills, pages with a cursor, and refuses any
order whose fill count disagrees with the sheet.

WHAT A STAKE ROW IS NOT. The stake eras are also calendar periods: $4 was
the opening fortnight, $7 is mostly the last two days. A row that looks bad
may be reporting a bad week rather than a bad size. Where a date range is
narrow the row says so.

Read-only.
"""
import collections

import tally as T

START = "2026-10-07 15:49"
T9_STAKES = ("2", "4", "5", "6", "7", "10")


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
    groups = collections.defaultdict(list)
    inferred = 0
    for b in bets:
        s = setts.get(b["ticker"])
        if s is None:
            continue
        who = str(b.get("strategy") or "").strip()
        if not who:
            inferred += 1
            ser = str(b["ticker"]).split("-")[0]
            st = str(T.stake_of(b) or "?")
            if ser.startswith("KXWTI"):
                who = "oil"
            elif st == "3":
                who = "t3"
            else:
                who = "slice"
        f = fills.get(b["oid"])
        cost = (f["cost"] if (f and f.get("cost")) else (b["limit_cost"] or 0.0))
        groups[who].append(dict(
            stake=str(T.stake_of(b) or "?"), cost=cost,
            contracts=int(b["contracts"] or 0),
            rev=(s["rev"] * b.get("_share", 1.0)),
            ts=b["ts"]))

    print("=" * 98)
    print("EVERY STRATEGY, SPLIT BY STAKE")
    print("  break-even is each group's OWN mean cost per contract, because a "
          "winning contract pays $1.00")
    print("=" * 98)
    print(f"  {sum(len(v) for v in groups.values())} settled bets"
          + (f"; {inferred} attributed by inference (no Strategy column yet)"
             if inferred else ""))
    print()

    def line(label, rows, indent="  "):
        if not rows:
            return
        n = len(rows)
        wins = sum(1 for r in rows if r["rev"] > 0)
        staked = sum(r["cost"] for r in rows)
        con = sum(r["contracts"] for r in rows)
        net = sum(r["rev"] for r in rows) - staked
        be = (100.0 * staked / con) if con else float("nan")
        wr = 100.0 * wins / n
        roi = (100.0 * net / staked) if staked else 0.0
        days = f"{min(r['ts'] for r in rows)[5:10]}..{max(r['ts'] for r in rows)[5:10]}"
        print(f"{indent}{label:<16} {n:>4} {wr:>7.2f}% {be:>8.2f}% "
              f"{wr - be:>+7.2f} ${net:>+8.2f} {roi:>+7.2f}%  {days}")

    hdr = (f"  {'':<16} {'n':>4} {'won':>8} {'needs':>9} {'gap':>7} "
           f"{'net':>9} {'ROI':>8}  dates")
    order = [w for w in ("slice", "t3", "oil") if w in groups]
    order += [w for w in sorted(groups) if w not in order]
    for who in order:
        rows = groups[who]
        name = {"slice": "T-9 (frozen spec)", "t3": "T-3 test",
                "oil": "Oil / WTI"}.get(who, who)
        print("-" * 98)
        print(f"{name}")
        print("-" * 98)
        print(hdr)
        line("ALL", rows)
        bys = collections.defaultdict(list)
        for r in rows:
            bys[r["stake"]].append(r)
        for st in sorted(bys, key=lambda x: (len(x), x)):
            line(f"  ${st}", bys[st], indent="  ")
        print()

    print("=" * 98)
    print("  READ THE STAKE ROWS AS 'WHEN' AS MUCH AS 'HOW BIG'. The eras are")
    print("  calendar periods -- the date range on each row shows how narrow.")
    print("  Stake scales an edge; it cannot create or destroy one, so a bad")
    print("  stake row is first evidence about a bad fortnight.")
    print("=" * 98)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

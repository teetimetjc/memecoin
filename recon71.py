"""Where the 71 cents went.

The account reads $124.01. Starting balance $127.98 plus the measured net of
-$4.68 gives $123.30. The difference is small, but after three pagination
bugs in a week a 71c residual is a lead, not a rounding footnote.

The first place to look: tally reads 340 settlements and the sheet holds 271
bets, so 69 settlements have no bet row. Those are either pre-START bets from
retired strategies, manual orders, or something that never got logged -- and
any of them that settled after the starting mark moved the balance without
appearing in the record.

Read-only.
"""
import collections
import tally as T
import live


def all_settlements():
    out, cur, pages = [], None, 0
    while pages < 60:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = T.get("/portfolio/settlements", **p)
        rows = (d or {}).get("settlements") or []
        out += rows
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break
    return out, pages


def main():
    setts, pages = all_settlements()
    print(f"  {len(setts)} settlements over {pages} page(s)")
    bets = T.rows_since(T.START)
    known = {b["ticker"] for b in bets}
    print(f"  {len(bets)} bet rows in the sheet since {T.START}")

    orphan = [s for s in setts if str(s.get("ticker") or "") not in known]
    print(f"  {len(orphan)} settlements with NO matching bet row\n")

    def rev(s):
        r = T._f(s.get("revenue_dollars"))
        if r is None:
            r = T._f(s.get("revenue"))
            r = r / 100.0 if r is not None else 0.0
        return r or 0.0

    def when(s):
        return str(s.get("settled_time") or s.get("determined_time") or "")[:19]

    tot = sum(rev(s) for s in orphan)
    print(f"  their revenue totals ${tot:,.2f}")
    after = [s for s in orphan if when(s) >= "2026-10-07"]
    print(f"  {len(after)} of them settled on or after 2026-10-07 "
          f"(revenue ${sum(rev(s) for s in after):,.2f})")
    print()
    for s in sorted(after, key=when)[:15]:
        print(f"    {when(s):<20} {str(s.get('ticker'))[:30]:<30} "
              f"rev ${rev(s):>7.2f}  {s.get('market_result') or s.get('result')}")
    print()
    by = collections.Counter(str(s.get("ticker") or "").split("-")[0]
                             for s in orphan)
    print("  orphans by series:", dict(by.most_common(8)))
    print()
    print("  NOTE: an orphan that settled BEFORE the record starts is")
    print("  expected -- it belongs to a retired strategy. One that settled")
    print("  DURING the record is money moving with no bet row to explain it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

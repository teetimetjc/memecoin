"""The 43 fills far better than their quote: real sweeps, or an artifact?

WHAT PROMPTED THIS. fillslip found 43 of 823 live orders (5.2%) recorded as
filling more than 5c BELOW the quote the decision was made on, including 16c
against a 42c quote and 67c against a 90c quote. A buy cannot normally
execute below the best offer. fillcheck.py had already flagged ONE such case
-- an ETH order quoted at 90.1c recorded as filled at 54.0c -- and noted it
was nineteen percent of all profit to date and never explained. It is not one
case. It is 43, and the answer changes the books either way:

  REAL SWEEPS of deep resting offers -> that is free money the paper record
  does not capture, and the live strategy is better than measured.
  AN ARTIFACT -> some of the recorded profit is not real, and the live
  record overstates itself.

THE HYPOTHESES, each with a test that can refute it:

  1. PROBE ORDERS, NOT REAL BETS. place() has callers outside the live loop
     -- manual_bet, nocheck, bounce_live_test -- and 18 Sep is recorded in
     live.py as the day order types were probed on the real exchange. For a
     probe, 'Entry c' is not a decision quote at all. TEST: group by date and
     by Strategy. If the anomalies cluster on probe dates, that is the answer.
  2. SIDE CONFUSION. Taking the yes price on a no fill prices an 8c purchase
     at 92c, and the inverse would price a 92c purchase at 8c. TEST: for each
     anomaly, print BOTH legs and the outcome_side, and check whether
     yes+no sums to 100c and whether the complement would be plausible.
  3. MULTI-PIECE FILLS. One order filling in pieces at different prices is
     summarised to one average here; a single tiny piece at an absurd price
     drags it. TEST: count the pieces and show each one.
  4. A GENUINE SWEEP. Then the fill is real and the pieces will show a walk
     DOWN the book, with the size concentrated at plausible prices.

Nothing is computed on top of the raw fields in the per-order dump, because
every previous wrong answer here came from a number that had been through an
assumption first.

READ-ONLY. No order, no row, no setting changed.
"""
import collections
import json
import os
import statistics as st

import reckon as R

GOOD_LIMIT = float(os.environ.get("FILLODD_GOOD_LIMIT_C") or 5.0)
SHOW = int(os.environ.get("FILLODD_SHOW") or 8)
SHEET = "Live Bets"


def client():
    import gspread
    from google.oauth2.service_account import Credentials
    raw = os.environ.get("GOOGLE_CREDENTIALS")
    if not raw:
        raise SystemExit("  no GOOGLE_CREDENTIALS")
    creds = Credentials.from_service_account_info(
        json.loads(raw),
        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    sid = os.environ.get("KALSHI_SHEET_ID")
    if not sid:
        import predictor as P
        sid = P.SPREADSHEET_ID
    return gspread.authorize(creds).open_by_key(sid)


def f(v):
    try:
        x = float(str(v).strip())
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def main():
    print("=" * 108)
    print("THE FILLS THAT CAME IN TOO GOOD -- real sweeps or an artifact?")
    print("=" * 108)
    print(f"  anomaly = filled more than {GOOD_LIMIT:.0f}c below the "
          f"decision quote")
    print("  READ-ONLY: no order, no row, no setting changed.\n")

    sh = client()
    rows = sh.worksheet(SHEET).get_all_values()
    if len(rows) < 2:
        raise SystemExit(f"  tab '{SHEET}': no data rows")
    head = {k: i for i, k in enumerate(rows[0])}
    need = ["Timestamp", "Ticker", "Side", "Entry ¢", "Limit ¢", "Contracts",
            "Status", "Order ID"]
    missing = [k for k in need if k not in head]
    if missing:
        raise SystemExit(f"  '{SHEET}' missing {missing}; found {rows[0]}")

    def g(r, k):
        i = head.get(k)
        return r[i] if i is not None and i < len(r) else ""

    placed = {}
    for r in rows[1:]:
        oid = str(g(r, "Order ID")).strip()
        entry = f(g(r, "Entry ¢"))
        if not oid or entry is None:
            continue
        placed[oid] = dict(
            ts=str(g(r, "Timestamp")), day=str(g(r, "Timestamp"))[:10],
            ticker=str(g(r, "Ticker")).strip(),
            side=str(g(r, "Side")).strip(),
            entry=entry, limit=f(g(r, "Limit ¢")),
            contracts=f(g(r, "Contracts")),
            presubmit=f(g(r, "Presubmit ¢")),
            qage=f(g(r, "Quote age s")),
            strategy=str(g(r, "Strategy")).strip() or "(blank)",
            status=str(g(r, "Status")).strip().upper(),
            detail=str(g(r, "Detail"))[:60] if "Detail" in head else "",
            oid=oid)

    fills, npages = R.pages("/portfolio/fills", "fills", limit=1000)
    print(f"  {len(fills)} fill(s) over {npages} page(s), "
          f"{len(placed)} sheet order(s) with a quote\n")

    by_order = collections.defaultdict(list)
    for x in fills:
        oid = str(x.get("order_id") or "").strip()
        if oid:
            by_order[oid].append(x)

    def piece_price(x):
        side = str(x.get("outcome_side") or x.get("side") or "").lower()
        px = (R.f(x.get("no_price_dollars")) if side == "no"
              else R.f(x.get("yes_price_dollars")))
        return (px * 100.0) if px is not None else None

    def piece_count(x):
        return R.f(x.get("count_fp")) or R.f(x.get("count")) or 0.0

    odd, normal = [], []
    for oid, p in placed.items():
        xs = by_order.get(oid)
        if not xs:
            continue
        num = den = 0.0
        for x in xs:
            pc, n = piece_price(x), piece_count(x)
            if pc is None or not n:
                continue
            num += pc * n
            den += n
        if not den:
            continue
        p["fill"] = num / den
        p["slip"] = p["fill"] - p["entry"]
        p["pieces"] = xs
        (odd if p["slip"] < -GOOD_LIMIT else normal).append(p)

    print(f"  {len(odd)} anomalous, {len(normal)} normal "
          f"({len(odd) + len(normal)} matched in total)")
    if not odd:
        print("\n  NO ANOMALY FOUND in this fill window. That is not a")
        print("  refutation -- the fills endpoint is paginated and the")
        print("  anomalies may be older than the pages fetched.")
        print("=" * 108)
        return 0

    # ----------------------------------- 1. probe orders? by date, strategy
    print(f"\n{'-' * 108}")
    print("HYPOTHESIS 1: THESE ARE PROBES, NOT REAL BETS")
    print("  18 Sep is recorded in live.py as the day order types were")
    print("  probed against the real exchange. For a probe, 'Entry c' is")
    print("  not a decision quote, so a large gap would mean nothing.")
    print("-" * 108)
    od = collections.Counter(p["day"] for p in odd)
    nd = collections.Counter(p["day"] for p in normal)
    print(f"  {'date':<14}{'anomalous':>11}{'normal':>9}{'anom share':>13}")
    for d in sorted(set(od) | set(nd)):
        a, n = od.get(d, 0), nd.get(d, 0)
        if not a:
            continue
        print(f"  {d:<14}{a:>11}{n:>9}{100.0*a/(a+n):>12.1f}%")
    print(f"\n  dates with NO anomaly: "
          f"{len([d for d in nd if d not in od])} of {len(set(od)|set(nd))}")
    print(f"\n  {'strategy':<16}{'anomalous':>11}{'normal':>9}"
          f"{'anom share':>13}")
    osr = collections.Counter(p["strategy"] for p in odd)
    nsr = collections.Counter(p["strategy"] for p in normal)
    for s in sorted(set(osr) | set(nsr)):
        a, n = osr.get(s, 0), nsr.get(s, 0)
        tot = a + n
        print(f"  {s:<16}{a:>11}{n:>9}"
              f"{(100.0*a/tot if tot else 0):>12.1f}%")

    # ------------------------------------------- 2 & 3. sides and pieces
    print(f"\n{'-' * 108}")
    print("HYPOTHESIS 2 AND 3: SIDE CONFUSION, OR MULTI-PIECE AVERAGING")
    print("-" * 108)
    multi = [p for p in odd if len(p["pieces"]) > 1]
    print(f"  anomalies that filled in MORE THAN ONE piece: "
          f"{len(multi)} of {len(odd)}")
    sides = collections.Counter(
        str(x.get("outcome_side") or x.get("side") or "?").lower()
        for p in odd for x in p["pieces"])
    print(f"  outcome_side across anomalous pieces: {dict(sides)}")
    nsides = collections.Counter(
        str(x.get("outcome_side") or x.get("side") or "?").lower()
        for p in normal for x in p["pieces"])
    print(f"  ...and across normal pieces:          {dict(nsides)}")
    bets = collections.Counter(p["side"] for p in odd)
    nbets = collections.Counter(p["side"] for p in normal)
    print(f"  the SHEET's Side on anomalies: {dict(bets)}")
    print(f"  ...and on normal orders:       {dict(nbets)}")

    # THE COMPLEMENT TEST. If the price field were being read from the wrong
    # leg, the complement (100 - fill) would sit near the quote. If instead
    # the fill is a genuine sweep, the complement is nowhere near it.
    comp = [abs((100.0 - p["fill"]) - p["entry"]) for p in odd]
    direct = [abs(p["slip"]) for p in odd]
    print(f"\n  COMPLEMENT TEST, {len(odd)} anomalies:")
    print(f"    median |fill - quote|          {st.median(direct):>8.2f}c")
    print(f"    median |(100-fill) - quote|    {st.median(comp):>8.2f}c")
    if st.median(comp) < st.median(direct):
        print("    the COMPLEMENT is closer to the quote, which is what")
        print("    reading the wrong leg looks like. Side confusion is the")
        print("    leading explanation.")
    else:
        print("    the complement is NOT closer, so these are not simply")
        print("    the wrong leg of the book being read.")

    # ------------------------------------------------- 4. the raw dump
    print(f"\n{'-' * 108}")
    print(f"THE {min(SHOW, len(odd))} WORST, FIELD BY FIELD, NOTHING "
          f"COMPUTED ON TOP")
    print("-" * 108)
    for p in sorted(odd, key=lambda p: p["slip"])[:SHOW]:
        print(f"\n  {p['ticker']}   {p['ts']}   strategy={p['strategy']}")
        print(f"    sheet: side={p['side']} entry={p['entry']}c "
              f"limit={p['limit']}c contracts={p['contracts']} "
              f"presubmit={p['presubmit']} qage={p['qage']}s "
              f"status={p['status']}")
        print(f"    computed fill {p['fill']:.2f}c  "
              f"slip {p['slip']:+.2f}c  pieces {len(p['pieces'])}")
        for i, x in enumerate(p["pieces"]):
            keep = {k: x.get(k) for k in (
                "count", "count_fp", "outcome_side", "side", "action",
                "yes_price_dollars", "no_price_dollars", "yes_price",
                "no_price", "is_taker", "created_time", "trade_id")
                if x.get(k) is not None}
            print(f"      piece {i+1}: {json.dumps(keep, default=str)}")

    print(f"\n{'=' * 108}")
    print("  WHAT WOULD SETTLE IT, in the order the sections above test it:")
    print("  a date/strategy cluster means probes; a closer complement")
    print("  means the wrong leg; many pieces means an averaging artifact;")
    print("  one taker piece at a plausible price means a real sweep.")
    print("  The profit at stake is real either way -- 5% of orders is")
    print("  enough to move the live record in both directions.")
    print("=" * 108)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

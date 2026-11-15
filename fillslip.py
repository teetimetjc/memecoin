"""Did we fill at the quote we decided on, or at the limit? Places nothing.

THE QUESTION, AND WHY IT DECIDES EVERYTHING ELSE. The SOL paper edge is
+$15.83 across 615 contracts -- 2.57c per contract. The bot sends an
immediate-or-cancel LIMIT at ceil(quote + SLIP_BUFFER_CENTS), and that buffer
defaults to 3.0c. So the order will accept a fill up to ~3c worse than the
quote the decision was made on, against an edge worth 2.57c. If fills land at
the quote, the edge survives. If they land at the limit, there was never an
edge to begin with and no amount of further paper trading would have shown it.

WHY THE SHEET CANNOT ANSWER IT. 'Cost $' in the Live Bets tab is computed as
contracts x LIMIT, in place() before the order is even sent -- it is the
worst-case assumption, not the money that left the account. 'Presubmit c' is
the executable ask fetched just before sending, which measures quote staleness
but still is not a fill. Neither column is a fill price, so the actual price
paid has to come from the exchange.

SO THIS JOINS THE SHEET TO THE EXCHANGE'S OWN FILL RECORDS, matching on Order
ID, and reports the distribution of (fill price - decision quote) in cents.
That number, applied to the 2.57c edge, is the answer.

IT IS READ-ONLY. It places no order, writes no row, and changes no setting.
It reads the Live Bets tab and two read-only portfolio endpoints.
"""
import collections
import json
import os
import statistics as st

import tally as T
import reckon as R

# The measured SOL edge this has to be compared against, in cents/contract.
EDGE_CENTS = float(os.environ.get("FILLCHECK_EDGE_CENTS") or 2.57)
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


def pct(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def main():
    print("=" * 104)
    print("DID WE FILL AT THE QUOTE, OR AT THE LIMIT?")
    print("=" * 104)
    print(f"  the edge this has to survive: {EDGE_CENTS:.2f}c per contract")
    print("  READ-ONLY: no order, no row, no setting changed.\n")

    sh = client()
    try:
        rows = sh.worksheet(SHEET).get_all_values()
    except Exception as e:
        raise SystemExit(f"  tab '{SHEET}': UNREADABLE ({str(e)[:90]})")
    if len(rows) < 2:
        raise SystemExit(f"  tab '{SHEET}': no data rows")
    head = {k: i for i, k in enumerate(rows[0])}

    # ADDRESS BY NAME, AND REFUSE IF A NAME IS MISSING. Every other reader of
    # this tab addresses it by POSITION (r[8] for status, r[10] for detail),
    # which is why columns here are only ever end-appended. Reading by name
    # means a reordering breaks loudly here instead of quietly mislabelling.
    need = ["Timestamp", "Ticker", "Entry ¢", "Limit ¢", "Contracts",
            "Status", "Order ID"]
    missing = [k for k in need if k not in head]
    if missing:
        raise SystemExit(
            f"  tab '{SHEET}' is missing column(s): {missing}\n"
            f"  found: {rows[0]}\n"
            f"  refusing rather than guessing at positions.")

    def g(r, k):
        i = head.get(k)
        return r[i] if i is not None and i < len(r) else ""

    placed = []
    status_count = collections.Counter()
    for r in rows[1:]:
        stat = str(g(r, "Status")).strip().upper()
        status_count[stat or "(blank)"] += 1
        oid = str(g(r, "Order ID")).strip()
        entry = f(g(r, "Entry ¢"))
        if not oid or entry is None:
            continue
        placed.append(dict(
            ts=str(g(r, "Timestamp")),
            ticker=str(g(r, "Ticker")).strip(),
            series=(str(g(r, "Ticker")).strip().upper().split("-")[0]
                    or "?"),
            entry=entry,
            limit=f(g(r, "Limit ¢")),
            contracts=f(g(r, "Contracts")),
            presubmit=f(g(r, "Presubmit ¢")),
            qage=f(g(r, "Quote age s")),
            strategy=str(g(r, "Strategy")).strip(),
            status=stat,
            oid=oid))

    print("  STATUS OF EVERY ROW IN THE TAB:")
    for k, n in status_count.most_common():
        print(f"    {k:<28}{n:>6}")
    print(f"\n  {len(placed)} row(s) carry both an Order ID and an "
          f"Entry quote")
    if not placed:
        print("\n  NOTHING TO JOIN. No row has both, so there is no live")
        print("  order whose fill could be looked up. This is not evidence")
        print("  about fills -- it is an absence of live orders.")
        print("=" * 104)
        return 0

    # ----------------------------------------------- the exchange's own fills
    print("\n  fetching fills from the exchange (read-only)...")
    try:
        fills, npages = R.pages("/portfolio/fills", "fills", limit=1000)
    except Exception as e:
        print(f"\n  FILLS COULD NOT BE READ: {str(e)[:140]}")
        print("  Without them the actual price paid is unknown, and the")
        print("  sheet's 'Cost $' is the limit-based assumption, not a")
        print("  measurement. No slippage figure is produced.")
        print("=" * 104)
        return 1
    print(f"  {len(fills)} fill(s) over {npages} page(s)")

    # One order can fill in several pieces at different prices, so the fills
    # are grouped by order and reduced to a size-weighted average price --
    # which is what the order actually cost per contract.
    by_order = collections.defaultdict(list)
    no_oid = 0
    for x in fills:
        oid = str(x.get("order_id") or "").strip()
        if not oid:
            no_oid += 1
            continue
        by_order[oid].append(x)
    if no_oid:
        print(f"  {no_oid} fill(s) carried no order_id and are unmatched")

    def avg_price_cents(xs):
        """Size-weighted price in cents for one order's fills, or None."""
        num = den = 0.0
        for x in xs:
            n = R.f(x.get("count_fp"))
            if n is None:
                n = R.f(x.get("count"))
            if not n:
                continue
            side = str(x.get("outcome_side") or x.get("side") or "").lower()
            px = (R.f(x.get("no_price_dollars")) if side == "no"
                  else R.f(x.get("yes_price_dollars")))
            if px is None:
                continue
            num += n * px * 100.0
            den += n
        return (num / den) if den else None

    matched, unmatched = [], []
    for p in placed:
        xs = by_order.get(p["oid"])
        if not xs:
            unmatched.append(p)
            continue
        ap = avg_price_cents(xs)
        if ap is None:
            unmatched.append(p)
            continue
        p["fill"] = ap
        p["slip"] = ap - p["entry"]
        p["filled_n"] = sum((R.f(x.get("count_fp")) or R.f(x.get("count"))
                             or 0) for x in xs)
        matched.append(p)

    print(f"\n  {len(matched)} order(s) matched to a fill, "
          f"{len(unmatched)} not matched")
    if unmatched:
        print("  An unmatched order is one that did not trade (IOC orders")
        print("  that missed the book are cancelled, not filled) OR one")
        print("  whose fill is older than the pages fetched. Both are")
        print("  counted here; neither is a slippage measurement.")
    if not matched:
        print("\n  NO ORDER COULD BE MATCHED TO A FILL, so the price paid")
        print("  is still unknown. No slippage figure is produced.")
        print("=" * 104)
        return 1

    # ----------------------------------- implausibly GOOD fills, flagged
    # THE DANGER IS NOT ONLY A BAD FILL. fillcheck.py documents an ETH order
    # quoted at 90.1c recorded as filled at 54.0c -- 36c BELOW the touch,
    # where the next-best fill in the whole live record is 11c below. That
    # one bet is nineteen percent of all profit to date and it has never
    # been established whether it is a real sweep or a reporting artifact.
    #
    # A mean or median slippage that quietly includes fills like that would
    # report that filling is wonderful, on the strength of the very rows
    # whose validity is in doubt. So they are separated out, the verdict is
    # computed WITHOUT them, and they are listed.
    GOOD_LIMIT = float(os.environ.get("FILLSLIP_GOOD_LIMIT_C") or 5.0)
    freaks = [p for p in matched if p["slip"] < -GOOD_LIMIT]
    clean = [p for p in matched if p["slip"] >= -GOOD_LIMIT]
    if freaks:
        print(f"\n{'-' * 104}")
        print(f"FILLS MORE THAN {GOOD_LIMIT:.0f}c BETTER THAN THE QUOTE "
              f"-- held out of the verdict")
        print("-" * 104)
        print("  A buy cannot normally execute below the best offer. These")
        print("  are either lucky sweeps of deep resting offers or a")
        print("  reporting artifact, and that has not been established.")
        print(f"\n  {'ticker':<28}{'quote':>8}{'limit':>8}{'fill':>9}"
              f"{'slip':>9}")
        for p in sorted(freaks, key=lambda p: p["slip"])[:10]:
            print(f"  {p['ticker']:<28}{p['entry']:>8.1f}"
                  f"{(p['limit'] if p['limit'] is not None else -1):>8.1f}"
                  f"{p['fill']:>9.2f}{p['slip']:>+9.2f}")
        print(f"\n  {len(freaks)} of {len(matched)} order(s) "
              f"({100.0*len(freaks)/len(matched):.1f}%). The figures below")
        print("  EXCLUDE them, so the result does not rest on rows whose")
        print("  validity is open.")
        if not clean:
            print("\n  ...which leaves nothing. Every matched fill is one of")
            print("  these, so there is no trustworthy slippage measurement")
            print("  here at all.")
            print("=" * 104)
            return 1

    # ------------------------------------------------------- the answer
    matched = clean
    slips = [p["slip"] for p in matched]
    print(f"\n{'-' * 104}")
    print("FILL PRICE MINUS DECISION QUOTE, in cents per contract")
    print("-" * 104)
    print(f"  orders           {len(slips)}")
    print(f"  mean             {st.mean(slips):+.3f}c")
    print(f"  median           {st.median(slips):+.3f}c")
    print(f"  best (cheapest)  {min(slips):+.3f}c")
    print(f"  worst            {max(slips):+.3f}c")
    for q in (0.25, 0.75, 0.90, 0.95):
        print(f"  {int(q*100)}th percentile  {pct(slips, q):+.3f}c")
    worse = sum(1 for s in slips if s > 0)
    atq = sum(1 for s in slips if abs(s) < 0.005)
    better = sum(1 for s in slips if s < -0.005)
    print(f"\n  filled WORSE than the quote: {worse} "
          f"({100.0*worse/len(slips):.1f}%)")
    print(f"  filled AT the quote:         {atq} "
          f"({100.0*atq/len(slips):.1f}%)")
    print(f"  filled BETTER than the quote:{better} "
          f"({100.0*better/len(slips):.1f}%)")

    # Did any fill exceed the limit? That should be impossible on a buy
    # limit, so if it happens the join or the price field is wrong, and
    # saying so is more useful than averaging it in.
    over = [p for p in matched
            if p["limit"] is not None and p["fill"] > p["limit"] + 0.011]
    if over:
        print(f"\n  *** {len(over)} fill(s) priced ABOVE their own limit, "
              f"which a buy limit cannot do.")
        print("  The join or the price field is wrong; treat the figures")
        print("  above as unverified until this is explained.")
        for p in over[:3]:
            print(f"    {p['ticker']}  entry {p['entry']}c  "
                  f"limit {p['limit']}c  fill {p['fill']:.2f}c")

    print(f"\n{'-' * 104}")
    print("BY SERIES")
    print("-" * 104)
    print(f"  {'series':<14}{'orders':>8}{'median slip':>14}"
          f"{'mean slip':>12}{'worst':>10}")
    bys = collections.defaultdict(list)
    for p in matched:
        bys[p["series"]].append(p["slip"])
    for k in sorted(bys, key=lambda k: -len(bys[k])):
        v = bys[k]
        print(f"  {k:<14}{len(v):>8}{st.median(v):>+14.3f}"
              f"{st.mean(v):>+12.3f}{max(v):>+10.3f}")

    # ------------------------------------------------------- the verdict
    med = st.median(slips)
    p90 = pct(slips, 0.90)
    print(f"\n{'=' * 104}")
    print("WHAT THIS MEANS FOR THE SOL EDGE")
    print("=" * 104)
    print(f"  the edge is            {EDGE_CENTS:+.2f}c per contract")
    print(f"  median slippage        {med:+.3f}c")
    print(f"  90th pct slippage      {p90:+.3f}c")
    print(f"  edge after median slip {EDGE_CENTS - med:+.3f}c")
    print(f"  edge after 90th pct    {EDGE_CENTS - p90:+.3f}c")
    if med >= EDGE_CENTS:
        print("\n  THE EDGE DOES NOT SURVIVE ITS OWN FILLS. Typical slippage")
        print("  equals or exceeds the whole edge, so the paper result was")
        print("  an artefact of pricing bets at quotes that were never")
        print("  available. No further paper collection can change this.")
    elif p90 >= EDGE_CENTS:
        print("\n  IT SURVIVES TYPICALLY BUT NOT RELIABLY. The median fill")
        print("  leaves an edge, the bad tail does not. Tightening")
        print("  SLIP_BUFFER_CENTS would refuse the bad tail at the cost of")
        print("  some fills -- which is a measurable trade, not a guess.")
    else:
        print("\n  THE EDGE SURVIVES ITS FILLS. Slippage is small against")
        print("  the edge even in the bad tail, so the quote-priced paper")
        print("  record is not materially optimistic, and the SOL question")
        print("  reduces to waiting out the sample.")
    print("\n  AND THE EDGE ITSELF IS STILL p=0.73 once SOL is treated as")
    print("  the best of six series. This measures whether fills would")
    print("  destroy an edge, not whether the edge is real.")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

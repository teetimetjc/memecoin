"""Did a DOWN order actually buy NO? Settle it from real settled money.

To buy NO, live.py sends side="ask" at price (1 - p) -- it SELLS YES, which
when flat opens a short that pays $1 if NO wins. That is economically buying
NO and it is almost certainly right, but live.py's own comment records it as
an INFERENCE from an error message, and if it is inverted then every NO bet
holds the opposite of what was intended: a 95%-winning rule becomes a
5%-winning one. That is the single bug here that could empty an account
rather than bleed it, so the expensive favourite stays blocked until this is
answered from evidence.

bounce_test.yml ran with LIVE_ALLOW_DOWN=1 on 21 Sep, so settled DOWN orders
may already exist. If they do, this costs nothing to answer.

THE DECISIVE TEST, and why it does not depend on reading any field name
correctly. For each DOWN order on a market that has since settled:

    intending NO, we win if and only if the market resolved NO.

So compare the market's result against whether we were actually PAID. If
"paid" lines up with "resolved NO", the mapping is right. If it lines up with
"resolved YES", the mapping is inverted and the NO side must stay off. This
uses only the exchange's own settlement records -- the result it published and
the money it moved -- so it cannot be fooled by my guessing a field name, which
is how this project has gone wrong six times.

The fill records are also dumped verbatim, because whatever side/action fields
they carry are the direct statement of what position was opened, and a direct
statement beats an inference even when the inference agrees.

Read-only. Places nothing.
"""

import json
import sys

import requests

import live
import predictor as P


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def get(path, **params):
    """GET a portfolio endpoint. `path` is relative, e.g. /portfolio/fills.

    THE SIGNATURE PATH AND THE URL PATH ARE NOT THE SAME STRING, and the first
    run of this file 404'd for assuming they were. P.KALSHI_BASE already ends
    in /trade-api/v2, so appending a full "/trade-api/v2/portfolio/..." to it
    produced a doubled URL -- while the signature, built from that same full
    path, was perfectly correct. A correct signature on a nonexistent URL
    fails as "404 page not found", which reads like a missing endpoint rather
    than a malformed one, and that is exactly the kind of wrong-question error
    that keeps costing this project runs.

    So the prefix is written once here and the two uses are explicit.
    """
    sign_path = "/trade-api/v2" + path
    hdrs = P._kalshi_headers("GET", sign_path)
    if not hdrs:
        return None, "could not sign"
    try:
        r = requests.get(f"{P.KALSHI_BASE}{path}", headers=hdrs,
                         params=params or None, timeout=25)
        if not r.ok:
            return None, f"HTTP {r.status_code} {r.text[:100]}"
        return r.json(), None
    except Exception as e:
        return None, str(e)[:100]


def down_rows():
    """Every DOWN order this project ever logged, with its outcome fields."""
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        return [], 0
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    total = 0
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        total += 1
        if str(c(r, "Side")).strip().upper() != "DOWN":
            continue
        out.append(dict(ts=str(r[0]), ticker=str(c(r, "Ticker")),
                        entry=_f(c(r, "Entry ¢")),
                        contracts=_f(c(r, "Contracts")),
                        cost=_f(c(r, "Cost $")),
                        status=str(c(r, "Status")).strip().upper(),
                        oid=str(c(r, "Order ID")).strip(),
                        detail=str(c(r, "Detail"))))
    return out, total


def by_order_id(placed):
    """What position did each DOWN order actually open, per order?

    THIS IS THE PRIMARY TEST and the settlement comparison below is the weaker
    backup, for two reasons that only became visible once the settlement
    version disagreed on one market out of twenty.

    A settlement is per MARKET and reports the NET outcome. These runs placed
    both UP and DOWN orders, 45 fills across 20 markets, so a market carrying
    one of each nets YES against NO and the revenue describes the combination
    rather than the DOWN order. And keying settlements by ticker silently
    overwrote whenever a market had more than one settlement row, so the one
    compared was arbitrary.

    A fill carries an order_id and its own side. Matching the order id this
    project recorded when it sent the order against the side the exchange
    recorded when it filled is a direct statement about that one order, with
    nothing aggregated and nothing inferred.
    """
    d, err = get("/portfolio/fills", limit=200)
    if not d:
        return None, err
    want = {r["oid"]: r for r in placed if r["oid"]}
    rows = []
    for f in d.get("fills") or []:
        oid = str(f.get("order_id") or "")
        if oid not in want:
            continue
        rows.append(dict(
            oid=oid, ticker=str(f.get("ticker") or ""),
            side=str(f.get("side") or ""),
            outcome=str(f.get("outcome_side") or ""),
            action=str(f.get("action") or ""),
            book=str(f.get("book_side") or ""),
            count=_f(f.get("count_fp")) or _f(f.get("count")),
            no_px=_f(f.get("no_price_dollars")),
            intended=want[oid],
        ))
    return rows, None


def main():
    rows, total = down_rows()
    print("=" * 92)
    print("DID A DOWN ORDER BUY NO? -- read from settled money, not from docs")
    print("=" * 92)
    print(f"  {total} rows in '{live.LIVE_SHEET}', {len(rows)} of them DOWN\n")

    if not rows:
        print("  No DOWN order was ever logged, so there is nothing to confirm")
        print("  from history. The mapping stays unproven and the NO side stays")
        print("  off. Settling it needs one deliberate $4 NO order and a")
        print("  position read-back -- cheap, but a decision to spend, not")
        print("  something to infer.")
        return 0

    placed = [r for r in rows if r["status"] == "PLACED"]
    print(f"  {len(placed)} of the DOWN rows actually FILLED "
          f"(the rest never became positions):")
    for r in rows:
        mark = "FILLED " if r["status"] == "PLACED" else "no fill"
        # Built before the f-string rather than nested inside it: nesting the
        # same quote works on 3.12 and is a SyntaxError on 3.11, and this file
        # should not depend on which the runner happens to have.
        ent = "" if r["entry"] is None else f"{r['entry']:.0f}c"
        print(f"    {r['ts']:<22} {mark} {r['ticker']:<34} {ent:>5} "
              f"{r['detail'][:46]}")
    if not placed:
        print("\n  Every DOWN order was accepted and filled nothing, so no NO")
        print("  position ever existed. Acceptance is not a position: nothing")
        print("  here confirms the mapping.")
        return 0

    tickers = {r["ticker"] for r in placed}

    # ---- PRIMARY: what side did each DOWN order actually open? ----
    frows, ferr = by_order_id(placed)
    print("\n" + "=" * 92)
    print("PRIMARY TEST -- the side the exchange recorded, per order")
    print("=" * 92)
    if frows is None:
        print(f"  could not read fills: {ferr}")
    elif not frows:
        print("  None of the DOWN order ids appears in the last 200 fills, so")
        print("  the positions cannot be read directly. Falling back to the")
        print("  settlement comparison below, which is weaker.")
    else:
        no_side = sum(1 for r in frows if r["side"].lower() == "no")
        yes_side = sum(1 for r in frows if r["side"].lower() == "yes")
        for r in frows:
            ok = r["side"].lower() == "no"
            print(f"  {r['ticker']:<34} action={r['action']:<5} "
                  f"book_side={r['book']:<4} side={r['side']:<4} "
                  f"outcome_side={r['outcome']:<4} "
                  f"-> {'NO as intended' if ok else 'NOT NO -- INVERTED'}")
        print(f"\n  {no_side} of {len(frows)} DOWN fills opened a NO position"
              + (f", {yes_side} opened YES" if yes_side else ""))
        if yes_side == 0 and no_side:
            print("\n  CONFIRMED, by the exchange's own record rather than by")
            print("  inference: sending side='ask' at (1-p) opens NO. This is")
            print("  not a statistical result and needs no sample size -- the")
            print("  mapping is either right or inverted, and these fills say")
            print("  which.")
        elif yes_side:
            print("\n  At least one DOWN order opened a YES position. The NO")
            print("  side must stay off until that is understood.")

    # ---- SECONDARY, and known to aggregate: settlements per market ----
    d, err = get("/portfolio/settlements", limit=200)
    if not d:
        print(f"\n  could not read settlements: {err}")
        return 1
    setts = {}
    for s in d.get("settlements") or []:
        tk = str(s.get("ticker") or "")
        if tk in tickers:
            setts[tk] = s

    print("\n" + "=" * 92)
    print("SECONDARY: intending NO, we are paid if and only if the market "
          "resolved NO")
    print("=" * 92)
    print("  Per MARKET and NET, so a market carrying both an UP and a DOWN")
    print("  order nets them and this row describes neither alone. Read it as")
    print("  corroboration of the per-order test above, not as the verdict.")
    if not setts:
        print("  None of the filled DOWN markets appears in the last 200")
        print("  settlements, so the outcome cannot be read. Unproven.")
        return 0

    agree = disagree = 0
    for tk, s in sorted(setts.items()):
        result = str(s.get("market_result") or s.get("result") or "").lower()
        rev = _f(s.get("revenue_dollars"))
        if rev is None:
            rev = _f(s.get("revenue"))
            if rev is not None and abs(rev) > 100:
                rev = rev / 100.0
        paid = (rev or 0) > 0
        expect_paid = (result == "no")
        ok = (paid == expect_paid)
        agree += ok
        disagree += (not ok)
        print(f"  {tk:<34} resolved {result or '?':<4} "
              f"revenue ${0 if rev is None else rev:>7.2f}  "
              f"paid={'yes' if paid else 'no ':<3}  "
              f"NO-position predicts paid={'yes' if expect_paid else 'no '}  "
              f"-> {'AGREES' if ok else 'CONTRADICTS'}")

    print("\n" + "=" * 92)
    if disagree == 0 and agree > 0:
        print(f"  {agree} settled market(s) consistent with holding NO.")
    elif agree == 0 and disagree > 0:
        print(f"  INVERTED on {disagree} settled market(s), which would")
        print("  contradict the per-order test and must be resolved before the")
        print("  NO side runs at all.")
    else:
        print(f"  {agree} consistent, {disagree} not. Expected where a market")
        print("  carried both an UP and a DOWN order: the settlement nets them,")
        print("  so a NO win can show zero revenue once an offsetting YES")
        print("  position is included. Which markets those are is listed above;")
        print("  the per-order fills are what settles the mapping.")
    print("=" * 92)

    # ---- the direct statement, for completeness ----
    d2, err2 = get("/portfolio/fills", limit=200)
    fills = [f for f in ((d2 or {}).get("fills") or [])
             if str(f.get("ticker") or "") in tickers]
    print(f"\n  fills on those markets: {len(fills)}")
    if fills:
        print("  first record verbatim (whatever side/action fields exist are")
        print("  the exchange stating what position was opened):")
        print(f"    {json.dumps(fills[0])[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

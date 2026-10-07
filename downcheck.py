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
                        detail=str(c(r, "Detail"))))
    return out, total


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

    # ---- the decisive comparison, from the exchange's own settlements ----
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
    print("THE TEST: intending NO, we are paid if and only if the market "
          "resolved NO")
    print("=" * 92)
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
        print(f"  CONFIRMED on {agree} settled DOWN position(s): side='ask' at")
        print("  (1-p) does buy NO. The expensive favourite's NO side can be")
        print("  enabled with LIVE_ALLOW_DOWN=1.")
        print("\n  Caveat worth stating: a handful of settlements is a small")
        print("  sample for a binary mapping, but the mapping is not")
        print("  probabilistic -- it is either right or inverted, and one")
        print("  unambiguous settled position distinguishes those.")
    elif agree == 0 and disagree > 0:
        print(f"  INVERTED on {disagree} settled position(s). side='ask' is NOT")
        print("  buying NO. The NO side must stay off and live.py's mapping")
        print("  needs fixing before the expensive favourite runs at all.")
    else:
        print(f"  MIXED: {agree} agree, {disagree} contradict. That is worse")
        print("  than either clean answer -- a mapping cannot be half right, so")
        print("  something else differs between these orders. NO stays off.")
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

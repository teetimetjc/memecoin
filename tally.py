"""The live record: what was bet, what settled, what it actually made.

A high win rate is the thing this strategy is most likely to be misread by,
and the misreading is not careless -- it is what the numbers look like. The
narrow slice buys at about 93c, so break-even is 92.7%. Winning 95% of your
bets feels like winning almost everything, and it is: the margin between
triumphant and losing is three percentage points wide, and no run of
notifications can show you which side of it you are on. Twenty wins and two
losses reads as a hot streak and is a loss.

So this reports the only three numbers that answer it:

  WIN RATE AGAINST ITS OWN BREAK-EVEN LINE, computed from the prices actually
  paid rather than from 50% or from the historical average. A bet at 95c and a
  bet at 90c need different things to be worth making.

  NET DOLLARS FROM SETTLEMENTS, read from Kalshi rather than inferred from the
  order rows. What an order cost is knowable at entry; what it returned is not,
  and only the exchange knows which markets paid.

  HOW UNCERTAIN BOTH ARE. With a few dozen bets the error bar on the win rate
  is wider than the margin being measured, so the honest output is an interval
  that will contain both "clearly working" and "clearly not" for some time.
  Printing a point estimate alone, at this sample size, would be the same
  mistake as reading the notifications.

NOT A VERDICT ON THE RULE. The frozen specs score on their own schedule and
this changes nothing about them. This is bookkeeping: what the account did.

Read-only.
"""

import base64
import collections
import json
import math
import sys
import time

# How many individual bets the JSON carries. The summary always covers every
# bet; this only bounds the per-bet list.
# Raised from 60 once the scorecard grew a stake filter. That filter re-buys
# every bet in this list at $10 or $20, so a cap BELOW the settled count made
# the modelled columns quietly cover fewer bets than the $4 headline -- at 63
# settled the rows summed to +$4.50 against a headline of +$5.70, and nothing
# on the page said why. A bet is about 150 bytes here and the document limit
# is 256 KiB, so 400 rows is roughly 60 KiB and leaves plenty of room; at ~20
# bets a day that is about three weeks before the cap binds again.
BETS_IN_JSON = 400

import requests

import control
import live
import predictor as P

# When the narrow slice went live. Earlier rows belong to retired strategies
# and mixing them in would describe a bot that no longer exists.
START = "2026-10-07 15:49"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def get(path, **params):
    """Signature path and URL path differ; KALSHI_BASE already has the prefix."""
    hdrs = P._kalshi_headers("GET", "/trade-api/v2" + path)
    if not hdrs:
        return None
    try:
        r = requests.get(f"{P.KALSHI_BASE}{path}", headers=hdrs,
                         params=params or None, timeout=25)
        return r.json() if r.ok else None
    except Exception:
        return None


def wilson(k, n):
    """95% interval for a proportion, which a bare ratio hides at small n.

    Wilson rather than the textbook normal interval: near 95% the normal one
    runs past 1.0 and understates how little a short run tells you, which is
    the specific error this whole file exists to avoid.
    """
    if not n:
        return 0.0, 1.0
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def norm_ts(s):
    """'2026-10-09 7:35:21' -> '2026-10-09 07:35'. Zero-pad the hour.

    THE SHEET WRITES HOURS WITHOUT A LEADING ZERO, and every ordering in this
    file is a STRING sort on this column. '2026-10-09 21:21' sorts BEFORE
    '2026-10-09 7:35' because '2' < '7', so the scorecard's "newest first"
    list put every single-digit hour at the top -- a 07:35 bet appeared above
    a 21:21 one from fourteen hours later. Nothing numeric was wrong; the
    order was simply a lie, and the user spotted it before this did.

    Padding here rather than at each sort site means every consumer -- the
    loss list, the emitted record, the scorecard's table and its day
    grouping -- gets the same corrected value from one place.

    The column is UTC: two bets check out against their tickers, which are
    stamped in ET (KXDOGE15M-26OCT090345 closes 03:45 ET = 07:45 UTC, and
    this row reads 07:35, which is T-9 as the rule specifies).
    """
    s = str(s).strip().rstrip(":")
    if " " not in s:
        return s
    day, _, clock = s.partition(" ")
    parts = clock.split(":")
    if not parts or not parts[0].isdigit():
        return s
    parts[0] = parts[0].zfill(2)
    return day + " " + ":".join(parts)


def rows_since(start):
    sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
    rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    if len(rows) < 2:
        return []
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        # Compared padded, for the same reason it is sorted padded: an
        # unpadded '9:05' reads as LATER than '15:49' to a string compare,
        # so a pre-start bet on the start date would slip through.
        if not r or not r[0] or norm_ts(r[0]) < norm_ts(start):
            continue
        if str(c(r, "Status")).strip().upper() != "PLACED":
            continue
        out.append(dict(ts=norm_ts(r[0]), ticker=str(c(r, "Ticker")),
                        side=str(c(r, "Side")),
                        entry=_f(c(r, "Entry ¢")),
                        contracts=_f(c(r, "Contracts")),
                        oid=str(c(r, "Order ID")).strip(),
                        # The sheet's Cost column is contracts times the LIMIT
                        # price, kept only as a fallback. See actual_costs.
                        limit_cost=_f(c(r, "Cost $"))))
    return out


def actual_costs():
    """What each order really paid, from the fills: price AND fee, per order.

    THE SHEET'S COST COLUMN IS THE LIMIT, NOT THE FILL. live.py computes it as
    contracts times the limit price, and the limit is the quote plus a one-cent
    slip buffer. IOC orders execute at the book's ask, which is normally better
    than that, so costing a bet at its limit overstates what was spent and
    understates the profit.

    This mattered immediately: the first version of this file reported +$4.08
    while Kalshi reported +$4.83, and the whole $0.75 was 60 contracts priced
    1.8c too high. fee_truth.py had already recorded this exact property of the
    Cost column in September -- "the apparent fee is just the one-cent limit
    buffer" -- and this file was built on that column anyway.

    The fill carries the executed price and the fee actually charged, so cost
    is contracts*price + fee with nothing estimated. Keyed by order id so a
    market with several orders does not pool them.
    """
    d = get("/portfolio/fills", limit=200)
    by = collections.defaultdict(lambda: dict(cost=0.0, n=0.0, px=[]))
    for f in ((d or {}).get("fills") or []):
        oid = str(f.get("order_id") or "")
        if not oid:
            continue
        cnt = _f(f.get("count_fp")) or _f(f.get("count")) or 0.0
        # The side bought determines which price was paid: a NO fill pays the
        # no price, a YES fill the yes price. Using the yes price for both
        # would misprice every NO bet by (1 - 2p).
        side = str(f.get("side") or "").lower()
        px = _f(f.get("no_price_dollars")) if side == "no" else \
            _f(f.get("yes_price_dollars"))
        if px is None or not cnt:
            continue
        fee = _f(f.get("fee_cost")) or 0.0
        if fee > 1.5:                      # cents, not dollars
            fee = fee / 100.0
        by[oid]["cost"] += cnt * px + fee
        by[oid]["n"] += cnt
        by[oid]["px"].append(px)
    return by


def stake_of(b):
    """Which stake size this bet was placed at -- $4, $7, whatever comes next.

    NOTHING RECORDS IT. The stake lives in the workflow as FAVLIVE_STAKE, the
    sheet stores only contracts and a price, and nobody thought to write the
    intent down because for the first 73 bets there was only one answer. On
    2026-10-10 it changed from $4 to $7, and a record that cannot tell the two
    apart silently averages them -- the headline return would blend a $4 era
    and a $7 era into one rate belonging to neither.

    So it is recovered from the arithmetic that produced it. The bot buys
    floor(stake / price) whole contracts, so contracts * price is the stake
    minus whatever was too small to buy another contract, and rounding that UP
    to the dollar recovers the original: 4 contracts at 91c is $3.64 -> $4,
    7 at 91c is $6.37 -> $7, 11 at 90c is $9.90 -> $10.

    It is an inference, not a record, and it can only fail by landing on a
    neighbouring dollar -- never by inventing an era that did not exist. If a
    third size is ever added close to an existing one, write the stake into
    the sheet instead of extending this.
    """
    px = b.get("fillpx") or ((b.get("entry") or 0) / 100.0)
    n = int(b.get("contracts") or 0)
    if not px or n <= 0:
        return None
    return int(math.ceil(n * px - 1e-9))


def emit_json(bets, settled, won, setts, staked, returned, net, avg, need):
    """One JSON line carrying the whole record, for the scorecard's store.

    Printed rather than written anywhere: this runs on a throwaway runner with
    no path back to the artifact, so the operator copies it across. Capped at
    the most recent BETS_IN_JSON rows because a document is 256 KiB and the
    list grows by about thirty a day; the summary covers all of them either
    way, so the cap loses detail and never changes a total.
    """
    rows = []
    for b in sorted(settled, key=lambda x: x["ts"], reverse=True)[:BETS_IN_JSON]:
        s = setts[b["ticker"]]
        rows.append(dict(
            # SECONDS ARE KEPT, and they are not decoration. The scorecard
            # offers a "one bet per market" view that drops all but the
            # first order in a market, and the only duplicate on record is a
            # pair 26 seconds apart. Truncated to the minute they tie, the
            # tie-break falls to list order, and the view dropped the
            # EARLIER order -- the one the fixed bot would actually have
            # placed -- while claiming to keep it.
            ts=b["ts"][:19],
            series=b["ticker"].split("-")[0].replace("KX", "").replace("15M", ""),
            ticker=b["ticker"],
            paid=round(b["entry"] or 0, 1),
            fill=round(100 * b["fillpx"], 1) if b.get("fillpx") else None,
            contracts=int(b["contracts"] or 0),
            cost=round(b["cost"], 2),
            stake=stake_of(b),
            result=s["result"],
            rev=round(s["rev"], 2),
            pnl=round(s["rev"] - b["cost"], 2),
        ))
    doc = dict(
        asof=time.strftime("%-d %b %Y, %H:%M UTC", time.gmtime()),
        # The same instant in a form a reader can convert. `asof` above is
        # already formatted for display and prose cannot be re-zoned without
        # parsing English month names, which is how a clock ends up an hour
        # wrong twice a year. The scorecard shows this in Central; anything
        # else that wants a different zone has the raw instant to work from.
        asof_iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        placed=len(bets), settled=len(settled), won=len(won),
        net=round(net, 2), staked=round(staked, 2), returned=round(returned, 2),
        avg_paid_cents=round(100 * avg, 1),
        avg_fill_cents=round(100 * sum(
            b["fillpx"] for b in settled if b.get("fillpx")) /
            max(1, sum(1 for b in settled if b.get("fillpx"))), 1),
        breakeven_pct=round(100 * need, 1),
        bets=rows,
    )
    bal = control.fetch_balance()
    if bal is not None:
        doc["balance"] = round(bal, 2)
    blob = json.dumps(doc, separators=(",", ":"))

    # BASE64, BECAUSE THE PLAIN JSON COMES BACK UNPARSEABLE.
    #
    # GOOGLE_CREDENTIALS is a multi-line JSON secret whose pretty-printed form
    # contains lines that are a bare "{" and a bare "}". GitHub Actions masks
    # every line of a multi-line secret independently, so it redacts those two
    # characters EVERYWHERE in the log -- including here. The record arrives
    # with every brace replaced by "***", which no parser can read, so the
    # operator was retyping all sixty rows by hand to get them into the
    # scorecard. That is thousands of tokens a refresh and a transcription
    # error waiting to happen; one such refresh already landed 7c off the
    # ledger and only an assertion caught it.
    #
    # Base64's alphabet has no braces, so nothing in it is masked and the
    # whole document survives the log intact. One command decodes it.
    #
    # The plain form is NOT printed alongside it. It would sit about twenty
    # lines above this one, inside the tail of the log that gets read, so the
    # cost it was removed to avoid would come straight back. Nothing is lost:
    # the human-readable view is the report printed above, and the plain JSON
    # is one command away from the line below.
    print("\n--- BEGIN JSON64 ---")
    print(base64.b64encode(blob.encode("utf-8")).decode("ascii"))
    print("--- END JSON64 ---")


def main():
    bets = rows_since(START)
    print("=" * 94)
    print(f"LIVE RECORD since {START} UTC -- narrow slice, $4 a bet")
    print("=" * 94)
    if not bets:
        print("  No filled bets recorded yet.")
        return 0

    # Settlements, keyed by ticker, for what each market actually returned.
    d = get("/portfolio/settlements", limit=200)
    setts = {}
    for s in ((d or {}).get("settlements") or []):
        tk = str(s.get("ticker") or "")
        rev = _f(s.get("revenue_dollars"))
        if rev is None:
            rev = _f(s.get("revenue"))
            if rev is not None and abs(rev) > 100:
                rev = rev / 100.0
        setts[tk] = dict(result=str(s.get("market_result")
                                    or s.get("result") or "").lower(),
                         rev=rev or 0.0)

    settled = [b for b in bets if b["ticker"] in setts]
    pending = [b for b in bets if b["ticker"] not in setts]
    won = [b for b in settled if setts[b["ticker"]]["rev"] > 0]
    lost = [b for b in settled if setts[b["ticker"]]["rev"] <= 0]

    fills = actual_costs()
    est = 0
    for b in bets:
        f = fills.get(b["oid"])
        if f and f["cost"]:
            b["cost"] = f["cost"]
            b["fillpx"] = sum(f["px"]) / len(f["px"])
        else:
            # No matching fill readable: fall back to the limit-priced figure
            # and SAY SO, rather than let a pessimistic number pass as exact.
            b["cost"] = b["limit_cost"] or 0.0
            b["fillpx"] = None
            est += 1

    staked = sum(b["cost"] or 0 for b in settled)
    returned = sum(setts[b["ticker"]]["rev"] for b in settled)
    net = returned - staked

    print(f"  bets placed       {len(bets)}")
    print(f"  settled           {len(settled)}"
          + (f"   ({len(pending)} still open)" if pending else ""))
    if not settled:
        print("  Nothing has settled yet, so there is no result to read.")
        return 0

    pxs = [b["entry"] / 100.0 for b in settled if b["entry"]]
    avg = sum(pxs) / len(pxs) if pxs else 0.93
    need = avg + 0.07 * avg * (1 - avg)      # price plus the fee, as a rate
    wr = len(won) / len(settled)
    lo, hi = wilson(len(won), len(settled))

    print(f"\n  won               {len(won)} of {len(settled)}  "
          f"({100*wr:.1f}%)")
    print(f"  break-even needs  {100*need:.1f}%  "
          f"(at the {100*avg:.1f}c average actually paid)")
    fp = [b["fillpx"] for b in settled if b.get("fillpx") is not None]
    print(f"  staked            ${staked:,.2f}   "
          + (f"(fills averaged {100*sum(fp)/len(fp):.1f}c vs the "
             f"{100*avg:.1f}c quote)" if fp else "(from limit prices)"))
    if est:
        print(f"    ^ {est} order(s) had no readable fill, priced at their")
        print("      LIMIT instead, which overstates cost and understates net")
    print(f"  returned          ${returned:,.2f}")
    print(f"  NET               ${net:+,.2f}   "
          f"({100*net/staked:+.2f}% of stake)" if staked else "")

    print(f"\n  95% interval on the win rate: {100*lo:.1f}% to {100*hi:.1f}%")
    if lo > need:
        print("  Even the bottom of that interval clears break-even.")
    elif hi < need:
        print("  Even the TOP of that interval is below break-even.")
    else:
        print(f"  Break-even ({100*need:.1f}%) sits INSIDE the interval, so")
        print("  this record is consistent with a real edge AND with no edge")
        print("  at all. That is not a disappointing result -- it is what a")
        print("  sample this size can say, and it will stay true for a while.")
        n_need = 0
        if 0 < wr < 1 and abs(wr - need) > 1e-9:
            # Rough n for the interval half-width to clear the gap.
            gap = abs(wr - need)
            n_need = int((1.96 ** 2) * wr * (1 - wr) / (gap ** 2))
            print(f"  At the current margin, roughly {n_need:,} settled bets")
            print("  would be needed before the interval excludes break-even.")

    if lost:
        print(f"\n  the {len(lost)} loss(es) -- these are what decide it:")
        for b in lost[:8]:
            print(f"    {b['ts']:<22} {b['ticker']:<34} "
                  f"paid {b['entry']:.0f}c  -${b['cost']:.2f}")

    bal = control.fetch_balance()
    if bal is not None:
        print(f"\n  account balance   ${bal:,.2f}")

    print("\n" + "=" * 94)
    print("  A win rate near 95% is what this rule looks like whether or not")
    print("  it is profitable, because break-even is 92.7%. The notifications")
    print("  cannot tell you which; only the net and the interval can.")
    print("=" * 94)

    emit_json(bets, settled, won, setts, staked, returned, net, avg, need)
    return 0


if __name__ == "__main__":
    sys.exit(main())

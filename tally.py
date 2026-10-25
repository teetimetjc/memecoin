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
import calendar
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
                        limit_cost=_f(c(r, "Cost $")),
                        # WHICH BOT PLACED IT, written by live.py since
                        # 2026-10-23. Blank on older rows, and a reader that
                        # needs it for those must say it is inferring rather
                        # than let a guess pass as the record.
                        strategy=str(c(r, "Strategy")).strip()))
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

    ONE PAGE WAS NOT THE WHOLE HISTORY, AND THE SHORTFALL WAS SILENT.

    This read `get("/portfolio/fills", limit=200)` -- the 200 most recent
    fills, no cursor. Orders fill in several pieces, so as new bets arrived
    an old order would end up with SOME of its fills inside the window and
    some past the edge. The sum then covered part of the order and was
    recorded as the whole cost of it.

    On 2026-10-14 that put the 8 Oct XRP bet at $0.94 for four contracts
    bought near 92c -- about one contract's worth -- and showed it as
    +$3.06 instead of +$0.24. Nine hours later the same bet re-priced
    itself to $3.76 as the window moved. The UP-only record read -$1.35 in
    the morning and -$3.62 by midday with no bet having lost: the history
    was quietly rewriting itself, always in the flattering direction,
    because a missing fill can only make a bet look cheaper.

    So: page through with the cursor, and then REFUSE any fill set whose
    contract count does not match what the sheet says was bought. A partial
    read is worse than no read, because the fallback is merely pessimistic
    while a partial sum is wrong and believable.
    """
    by = collections.defaultdict(lambda: dict(cost=0.0, n=0.0, px=[], qty=[]))
    fills, cur, pages = [], None, 0
    while pages < 40:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = get("/portfolio/fills", **p)
        got = (d or {}).get("fills") or []
        fills += got
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not got:
            break
        time.sleep(0.15)
    for f in fills:
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
        # QUANTITY, SO THE AVERAGE CAN BE WEIGHTED BY IT. The reported fill
        # price was sum(px)/len(px) -- the mean of the PRICES, ignoring how
        # many contracts went through at each. An order filling 9 contracts
        # at 90c and 1 at 60c was shown as 75c when it really paid 87c. The
        # cost figure was always right (it is cnt*px summed), so P&L was
        # never affected; only the displayed fill, which is the column the
        # whole slippage analysis reads.
        by[oid]["qty"].append(cnt)
    return by


def stake_of(b):
    """Which stake size this bet was placed at -- $4, $7, whatever comes next.

    NOTHING RECORDS IT. The stake lives in the workflow as FAVLIVE_STAKE, the
    sheet stores only contracts and a price, and nobody thought to write the
    intent down because for the first 73 bets there was only one answer. On
    2026-10-10 it changed from $4 to $7, and a record that cannot tell the two
    apart silently averages them -- the headline return would blend a $4 era
    and a $7 era into one rate belonging to neither.

    So it is recovered by inverting the arithmetic that produced it.

    THE FIRST VERSION INVERTED THE WRONG FORMULA and fragmented the record
    into five stake buckets where there were three -- a $6 and a $9 that
    never existed. It sized from the FILL, and the fill is not what the order
    was sized on; it is not even known until after the order is sent. Two
    failures followed from that one mistake:

      A fill well below the quote understated the stake. An order sized at
      90.7c filled at 79.0c, and 7 contracts times the fill reads as $6.

      Even an exact fill understated it, because live.py sizes on the LIMIT
      -- ceil(quote + one cent of slip buffer) -- not on the quote. Ten
      contracts at a 90c quote is $9.00 against the quote and $9.10 against
      the limit, so a $10 bet read as $9.

    So invert what the order was actually built from: contracts =
    floor(stake / limit), hence stake = ceil(contracts * limit). Checked
    against all 108 live bets -- three buckets, and floor(stake/limit)
    reproduces the recorded contract count on every row.

    It remains an inference, not a record. If a stake is ever chosen close
    enough to an existing one that two sizes could round together, write the
    stake into the sheet instead of extending this.
    """
    entry = b.get("entry") or 0
    n = int(b.get("contracts") or 0)
    if not entry or n <= 0:
        return None
    # The limit live.py actually sends, taken from live.py rather than
    # restated here: a second copy of a constant is a second place for it to
    # go stale, and this whole function exists because an assumption about
    # how the order was built turned out to be wrong.
    limit_c = min(99.0, math.ceil(entry + live.SLIP_BUFFER_CENTS))
    return int(math.ceil(n * limit_c / 100.0 - 1e-9))


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
            side=str(b.get("side") or "").upper(),
            result=s["result"],
            rev=round(b.get("rev_share", s["rev"]), 2),
            pnl=round(b.get("rev_share", s["rev"]) - b["cost"], 2),
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


SCORECARD_TAB = "Scorecard"
# SIDE IS A COLUMN BECAUSE A DASHBOARD CANNOT FILTER ON WHAT IT CANNOT SEE.
# The scorecard carried no side at all, so the only apparent way to keep the
# DOWN and NO bets out of a chart was to delete their rows from the sheet --
# which would have destroyed the evidence that switched that side off, and
# the deletion would have been silent and permanent. One column is the whole
# fix: filter Side = UP in the dashboard and the rows stay on the record.
SCORECARD_COLS = ["Date CT", "Time CT", "Timestamp UTC", "Series", "Ticker",
                  "Side",
                  "Stake $", "Contracts", "Quote c", "Fill c", "Cost $",
                  "Result", "Won", "Revenue $", "P&L $", "Running P&L $",
                  "Break-even %"]


FILLRATE_TAB = "Fill Rate"

FILLRATE_COLS = ["Date CT", "Signals", "Filled", "Missed", "Fill rate %",
                 "Missed: book moved", "Missed: drop guard",
                 "Missed: other", "Missed stake $"]


def write_fillrate():
    """One row per CT day: how many signals became bets, and how many did not.

    WHY THIS TAB EXISTS. The Scorecard holds bets that FILLED, so a day can
    read 100% and say nothing about the two signals that never became bets.
    On 2026-10-19 two orders went out at 06:36 UTC, were accepted, and filled
    nothing because the ask had moved up past the limit -- invisible in every
    chart on the dashboard.

    That cohort is not a rounding error. Orders missed because the ask ran
    away settled YES 98.4% of the time across 62 of them, which is better
    than the bets that did fill. A missed bet is a real cost and the
    dashboard could not show it.

    GROUPED BY CT DATE, deliberately, because that is what the rest of the
    dashboard groups by. Timestamp UTC and Date CT disagree either side of
    midnight and a page using both describes one bet under two dates.

    Reads the Live Bets tab, which holds every ATTEMPT. Writes its own tab.
    """
    import live
    try:
        sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
        rows = sh.worksheet(live.LIVE_SHEET).get_all_values()
    except Exception as e:
        print(f"\n  could not read '{live.LIVE_SHEET}' for fill rate: "
              f"{str(e)[:90]}")
        return
    if len(rows) < 2:
        return
    h = {k: i for i, k in enumerate(rows[0])}

    def c(r, k):
        i = h.get(k)
        return r[i] if i is not None and i < len(r) else ""

    day = collections.defaultdict(lambda: dict(
        sig=0, fill=0, moved=0, guard=0, other=0, lost=0.0))
    for r in rows[1:]:
        if not r or not r[0] or norm_ts(r[0]) < norm_ts(START):
            continue
        st = str(c(r, "Status")).strip().upper()
        if st not in ("PLACED", "SKIPPED"):
            continue
        d = day[ct_parts(r[0])[0]]
        d["sig"] += 1
        if st == "PLACED":
            d["fill"] += 1
            continue
        det = str(c(r, "Detail")).lower()
        if "filled nothing" in det:
            d["moved"] += 1
        elif "ask fell" in det or "bid ran up" in det:
            d["guard"] += 1
        else:
            d["other"] += 1
        # What the skipped bet would have staked, at the limit price.
        lim = _f(c(r, "Limit ¢"))
        n = _f(c(r, "Contracts")) or 0
        if lim and n:
            d["lost"] += n * lim / 100.0

    out = []
    for k in sorted(day, reverse=True):
        v = day[k]
        miss = v["sig"] - v["fill"]
        out.append([k, v["sig"], v["fill"], miss,
                    round(100.0 * v["fill"] / v["sig"], 1) if v["sig"] else "",
                    v["moved"], v["guard"], v["other"], round(v["lost"], 2)])
    try:
        try:
            ws = sh.worksheet(FILLRATE_TAB)
        except Exception:
            ws = sh.add_worksheet(title=FILLRATE_TAB, rows=400,
                                  cols=len(FILLRATE_COLS))
        ws.clear()
        ws.update(values=[FILLRATE_COLS] + out, range_name="A1")
        tot_s = sum(day[k]["sig"] for k in day)
        tot_f = sum(day[k]["fill"] for k in day)
        print(f"\n  wrote {len(out)} day(s) to '{FILLRATE_TAB}' -- "
              f"{tot_f} of {tot_s} signals filled "
              f"({100.0*tot_f/tot_s:.1f}%)" if tot_s else "")
    except Exception as e:
        print(f"\n  could not write '{FILLRATE_TAB}': {str(e)[:120]}")


def write_scorecard(settled, setts):
    """One reconciled row per settled bet, for a dashboard to read.

    WHY NOT JUST POINT A DASHBOARD AT THE LIVE BETS TAB. Its `Cost $` column
    is contracts times the LIMIT price, not what was paid -- the limit is the
    quote plus a one-cent slip buffer, and IOC orders usually execute better
    than that. That column is why an early version of this file reported
    +$4.08 while Kalshi reported +$4.83. A dashboard built on it would be
    quietly wrong forever, and wrong in the flattering direction.

    So this writes the figures that were reconciled against the fills and the
    settlements: the executed price, the real cost including fee, the
    exchange's own result.

    IT OVERWRITES IN PLACE RATHER THAN APPENDING. The workbook is a third of
    the way through a hard 10-million-cell limit and has already had to be
    purged once; an appending log would grow without bound for no benefit,
    since every row is rewritten from the same source each run anyway. The
    tab therefore stays the size of the record, a few thousand cells.

    Times are CENTRAL as well as UTC. UTC is what the exchange records and
    what every calculation here uses; Central is the day the operator
    actually lived through, and a dashboard grouped by UTC date puts an
    evening's bets on tomorrow.
    """
    rows, run = [], 0.0
    for b in sorted(settled, key=lambda x: x["ts"]):
        s = setts[b["ticker"]]
        rv = b.get("rev_share", s["rev"]) or 0.0
        pnl = rv - (b["cost"] or 0.0)
        run += pnl
        ct = ct_parts(b["ts"])
        fill = 100 * b["fillpx"] if b.get("fillpx") else None
        rows.append([
            ct[0], ct[1], b["ts"],
            b["ticker"].split("-")[0].replace("KX", "").replace("15M", ""),
            b["ticker"],
            str(b.get("side") or "").upper(),
            stake_of(b) or "", int(b["contracts"] or 0),
            round(b["entry"] or 0, 1),
            round(fill, 1) if fill is not None else "",
            round(b["cost"] or 0, 2),
            s["result"], 1 if rv > 0 else 0,
            round(rv, 2), round(pnl, 2), round(run, 2),
            # The bar this bet had to clear: the price actually paid, as a
            # percentage. A win rate without it beside it says nothing.
            round(100 * (b["cost"] or 0) / max(1, int(b["contracts"] or 1)), 1),
        ])
    rows.reverse()                       # newest first, like the scorecard
    try:
        sh = P._get_client().open_by_key(P.SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SCORECARD_TAB)
        except Exception:
            ws = sh.add_worksheet(title=SCORECARD_TAB,
                                  rows=max(100, len(rows) + 10),
                                  cols=len(SCORECARD_COLS))
        ws.clear()
        ws.update(values=[SCORECARD_COLS] + rows, range_name="A1")
        print(f"\n  wrote {len(rows)} rows to the '{SCORECARD_TAB}' tab")
    except Exception as e:
        # A dashboard feed failing must not take the report down with it.
        print(f"\n  could not write '{SCORECARD_TAB}': {str(e)[:120]}")


def ct_parts(ts):
    """('2026-10-11', '4:21pm') in Central, from a UTC 'Y-m-d H:M:S'.

    Central, not a fixed offset: 1 November is CST and 31 October is CDT, and
    a hard-coded -5 would be right today and an hour wrong in three weeks.
    """
    try:
        t = time.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
        epoch = calendar.timegm(t)
    except Exception:
        return (str(ts)[:10], str(ts)[11:16])
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        d = _dt.datetime.fromtimestamp(epoch, ZoneInfo("America/Chicago"))
        return (d.strftime("%Y-%m-%d"), d.strftime("%-I:%M%p").lower())
    except Exception:
        return (str(ts)[:10], str(ts)[11:16])


def _ingest_settlements(rows, setts):
    """Fold one page of settlements into the ticker -> {result, rev} map."""
    for s in rows:
        tk = str(s.get("ticker") or "")
        rev = _f(s.get("revenue_dollars"))
        if rev is None:
            # UNITS BY FIELD NAME, NOT BY SIZE. A bare `revenue` is cents.
            rev = _f(s.get("revenue"))
            if rev is not None:
                rev = rev / 100.0
        setts[tk] = dict(result=str(s.get("market_result")
                                    or s.get("result") or "").lower(),
                         rev=rev or 0.0)


def main():
    bets = rows_since(START)
    print("=" * 94)
    print(f"LIVE RECORD since {START} UTC -- narrow slice, $4 a bet")
    print("=" * 94)
    if not bets:
        print("  No filled bets recorded yet.")
        return 0

    # Settlements, keyed by ticker, for what each market actually returned.
    # EVERY SETTLEMENT, NOT THE LAST 200. This read one page with no cursor,
    # so the "all time" record was really "the most recent 200 settlements"
    # and the window slid: on 2026-10-19 eleven new bets pushed eleven
    # winners from 9-10 October out of the back, and the headline fell from
    # +$12.25 to +$7.79 with no bet having lost. The count sat frozen at 200
    # the whole time, which is what made it look like a stable total.
    #
    # The drift has a direction, which is what makes it worse than noise:
    # the oldest bets are the early $4 era, the best stretch in the record,
    # so the number was set to decline on winning days indefinitely.
    #
    # Same mistake as the fills pagination bug a week ago. One page is not a
    # history, and a fixed-size window that always looks full is the hardest
    # kind to notice.
    setts = {}
    cur, pages, got_any = None, 0, 0
    while pages < 60:
        p = dict(limit=200)
        if cur:
            p["cursor"] = cur
        d = get("/portfolio/settlements", **p)
        rows = (d or {}).get("settlements") or []
        got_any += len(rows)
        _ingest_settlements(rows, setts)
        cur = (d or {}).get("cursor")
        pages += 1
        if not cur or not rows:
            break
        time.sleep(0.15)
    print(f"  read {got_any} settlement(s) across {pages} page(s)")
    # ONE SETTLEMENT, SHARED BY EVERY BET ON THAT MARKET.
    #
    # A settlement pays the whole POSITION, but this dict is keyed by ticker
    # and every bet on that ticker was reading the full figure. On 2026-10-13
    # two orders landed on one DOGE market -- 20 contracts, $19.20, paying
    # $20 -- and both rows claimed the $20, so the record showed +$10.45 and
    # +$10.34 on bets that between them made 80 cents. The headline read
    # +$12.93 when the truth was still negative, and the dashboard reported
    # a profit that did not exist.
    #
    # The revenue is split in proportion to contracts, which is exactly how
    # the payout was earned. Equal splitting would be wrong the moment two
    # bets on one market are different sizes.
    by_ticker = {}
    for b in bets:
        by_ticker.setdefault(b["ticker"], []).append(b)
    share = {}
    for tk, group in by_ticker.items():
        tot = sum(int(x["contracts"] or 0) for x in group) or 1
        for x in group:
            share[id(x)] = (int(x["contracts"] or 0) / tot) if len(group) > 1 else 1.0
        if len(group) > 1 and tk in setts:
            print(f"  NOTE: {len(group)} bets on {tk} -- one settlement of "
                  f"${setts[tk]['rev']:.2f} split across them by contract "
                  "count. Two orders reached the same market, which should "
                  "not happen and is being fixed separately.")

    def rev_for(b):
        """This bet's share of its market's settlement."""
        s = setts.get(b["ticker"])
        return (s["rev"] * share.get(id(b), 1.0)) if s else 0.0

    # ATTACHED TO THE BET, so every consumer sees the same number. The JSON
    # payload and the Scorecard tab each recomputed P&L from setts[ticker]
    # independently, which is how one fix in main() would have left two
    # other views still double-counting.
    for b in bets:
        b["rev_share"] = rev_for(b)

    settled = [b for b in bets if b["ticker"] in setts]
    pending = [b for b in bets if b["ticker"] not in setts]
    won = [b for b in settled if rev_for(b) > 0]
    lost = [b for b in settled if rev_for(b) <= 0]

    fills = actual_costs()
    est = partial = 0
    for b in bets:
        f = fills.get(b["oid"])
        want = int(b["contracts"] or 0)
        # THE COUNT MUST MATCH, or the fills seen are not the whole order.
        # A partial sum reads as a cheaper bet and a bigger profit, which is
        # how an 8 Oct bet showed +$3.06 on a cost of $0.94 for four
        # contracts. Pessimistic and right beats flattering and wrong.
        if f and f["cost"] and want and abs(f["n"] - want) < 0.01:
            b["cost"] = f["cost"]
            qsum = sum(f["qty"]) or 0.0
            b["fillpx"] = ((sum(p * q for p, q in zip(f["px"], f["qty"])) / qsum)
                           if qsum else (sum(f["px"]) / len(f["px"])))
        elif f and f["cost"] and want:
            b["cost"] = b["limit_cost"] or 0.0
            b["fillpx"] = None
            est += 1
            partial += 1
        else:
            # No matching fill readable: fall back to the limit-priced figure
            # and SAY SO, rather than let a pessimistic number pass as exact.
            b["cost"] = b["limit_cost"] or 0.0
            b["fillpx"] = None
            est += 1

    if partial:
        print(f"  NOTE: {partial} bet(s) had an incomplete fill set and were "
              "costed at the limit instead. The fills endpoint did not return "
              "every piece of those orders.")

    staked = sum(b["cost"] or 0 for b in settled)
    returned = sum(rev_for(b) for b in settled)
    net = returned - staked

    print(f"  bets placed       {len(bets)}")
    print(f"  settled           {len(settled)}"
          + (f"   ({len(pending)} still open)" if pending else ""))
    if not settled:
        print("  Nothing has settled yet, so there is no result to read.")
        return 0

    pxs = [b["entry"] / 100.0 for b in settled if b["entry"]]
    avg = sum(pxs) / len(pxs) if pxs else 0.93

    # BREAK-EVEN IS WHAT WAS PAID, NOT WHAT WAS QUOTED.
    #
    # This used to be avg + 0.07*avg*(1-avg) -- the QUOTE plus a modelled
    # fee. The quote is the price the order was sized against; it is not the
    # price that left the account. IOC orders on this book fill better than
    # the limit more often than not: across 118 live bets the quotes average
    # 91.6c and the fills 90.8c. Scoring against a price nobody paid put the
    # bar 1.4 points too high, and every "break-even sits inside the
    # interval" verdict in this project inherited that.
    #
    # The honest bar is the one the money defines: you need to win often
    # enough that the contracts you hold cover what they cost, so
    #
    #     break-even = total cost / total contracts
    #
    # Cost here already includes the fee actually charged, so nothing is
    # modelled. It is the RATIO OF THE SUMS, not the average of each bet's
    # ratio -- those differ whenever bets are different sizes, and this
    # record now spans $4, $7 and $10.
    #
    # NOTE THE DIRECTION: this correction LOWERS the bar and makes the live
    # record look better. That is the direction a mistake is hardest to
    # notice, which is why the reasoning is spelled out rather than the
    # number simply changed.
    tot_cost = sum(b["cost"] or 0.0 for b in settled)
    tot_ct = sum(int(b["contracts"] or 0) for b in settled)
    need = (tot_cost / tot_ct) if tot_ct else avg + 0.07 * avg * (1 - avg)
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
    write_scorecard(settled, setts)
    write_fillrate()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the dashboard's data file from the Predictions tab.

Writes dashboard/equity_data.js -- the same payload that has been assembled by
hand from each exported workbook, so the published chart can be refreshed
without anyone downloading a copy.

Read-only against the sheet. It imports predictor.py for the credentials and
column layout but calls nothing that writes, so a failure here cannot affect
collection. It is also a SEPARATE workflow from the predictor for the same
reason: a bug in the dashboard must never stop the 15-minute run.

Usage:  python dashboard_data.py [output_path]
"""

import json, math, statistics, sys
from collections import defaultdict

import predictor as P

OUT_DEFAULT = "dashboard/equity_data.js"

# The champion went live here; rows before it have order flow logged but no
# call, and they are the rows that chose the threshold, so they cannot grade it.
FROZEN = P.CVD_FROZEN


def _fee(contracts, p):
    """Kalshi's fee: 7% x contracts x P x (1-P), rounded UP to the cent.

    The round() guard is not cosmetic. When the exact fee lands on a cent
    boundary -- 25 contracts at 40c gives exactly 42c -- binary floating point
    stores it as 42.00000000000001, and ceil then charges 43c. That penny was
    being added to a fair fraction of the history.
    """
    return math.ceil(round(0.07 * contracts * p * (1 - p) * 100, 9)) / 100


def _pnl(entry_cents, won):
    """Net on a $10 bet at `entry_cents`, Kalshi fee rounded up to the cent."""
    p = entry_cents / 100.0
    contracts = 10.0 / p
    return (contracts if won else 0.0) - 10.0 - _fee(contracts, p)


def build(rows):
    idx = {h: i for i, h in enumerate(P.ALL_HEADERS)}

    def cell(r, name):
        i = idx.get(name, -1)
        return r[i] if 0 <= i < len(r) else ""

    def num(r, name):
        try:
            return float(cell(r, name))
        except (TypeError, ValueError):
            return None

    live = [c for c in P.CHALLENGERS if not c.get("retired")]

    seen, sig = set(), []
    for r in rows[1:]:
        if cell(r, "Model Version") != P.MODEL_VERSION:
            continue
        side = cell(r, "CVD Call")
        if side not in ("UP", "DOWN") or cell(r, "CVD Correct?") not in ("Yes", "No"):
            continue
        ts = cell(r, "Timestamp")
        if not ts or ts < FROZEN:
            continue
        key = (ts, cell(r, "Symbol"))
        if key in seen:                       # legacy rows contain a few dupes
            continue
        seen.add(key)
        entry = num(r, "K Up%") if side == "UP" else num(r, "K Down%")
        if not entry or entry <= 0:
            continue
        mask = 0
        for bit, ch in enumerate(live):
            if cell(r, f"{ch['name']} Call") in ("UP", "DOWN"):
                mask |= (1 << bit)
        sig.append((ts, cell(r, "Symbol").replace("USDT", ""), side, entry,
                    cell(r, "CVD Correct?") == "Yes", mask))

    if not sig:
        raise SystemExit("FAILED: no graded champion signals found.")

    # Why dry-run orders come back "no market resolved". The dry run can only
    # say the ticker was missing; it cannot say whether Kalshi genuinely had no
    # market for that window or whether we dropped one we had. The Predictions
    # tab settles it, because it logs the ticker independently: count champion
    # fires since the dry run started whose late ticker is blank. If that count
    # matches the failures, the market was never there and nothing is lost; if
    # it is far lower, the loss is ours and it is a bug to fix before real
    # money depends on it.
    DRY_FROM = "2026-09-16 23:15"
    fired = noticker = 0
    nt_when, nt_coins = [], {}
    for r in rows[1:]:
        if cell(r, "Model Version") != P.MODEL_VERSION:
            continue
        if cell(r, "CVD Call") not in ("UP", "DOWN"):
            continue
        ts = cell(r, "Timestamp")
        if not ts or ts < DRY_FROM:
            continue
        fired += 1
        if not cell(r, "K Late Ticker"):
            noticker += 1
            # WHEN they happened, not just how many. The count alone could not
            # tell a steady rate from a burst that has already stopped, and
            # those mean different things: a rate is structural, a burst is an
            # episode. Also which coins, since one tape misbehaving is a very
            # different problem from all five.
            nt_when.append(ts)
            nt_coins[cell(r, "Symbol").replace("USDT", "")] = \
                nt_coins.get(cell(r, "Symbol").replace("USDT", ""), 0) + 1

    sig.sort(key=lambda x: (x[0], x[1]))
    T, S, Dd, E, W, N, Q, R = [], [], [], [], [], [], [], []
    eq = 0.0
    for ts, sym, side, entry, won, mask in sig:
        # Accumulate the ROUNDED per-bet net, not the raw one. The page recomputes
        # the running total from these same rounded values when it filters, so a
        # cumulative built from unrounded nets disagrees with its own day table --
        # 16 cents over 1,030 bets, and two numbers for one thing is a bug.
        net = round(_pnl(entry, won), 2)
        eq = round(eq + net, 2)
        T.append(ts[:16]); S.append(sym); Dd.append(side)
        E.append(round(entry, 1)); W.append(1 if won else 0)
        N.append(net); Q.append(eq); R.append(mask)

    # Clustered spread: five coins bet in one window are one market event, so a
    # per-bet standard deviation understates how far a worthless rule can drift.
    groups = defaultdict(list)
    for k, ts in enumerate(T):
        groups[ts].append(N[k])
    cl = [(sum(v), len(v)) for v in groups.values()]
    n_bets = sum(c[1] for c in cl)
    mean = sum(c[0] for c in cl) / n_bets
    if len(cl) > 1:
        var = sum(c[1] * ((c[0] / c[1]) - mean) ** 2 for c in cl) / (len(cl) - 1)
        sdc = (var / len(cl)) ** 0.5 * (n_bets ** 0.5)
    else:
        sdc = statistics.stdev(N) if len(N) > 1 else 0.0

    return {
        "t": T, "s": S, "d": Dd, "e": E, "w": W, "n": N, "q": Q, "r": R,
        "rules": [{"n": c["name"], "f": c["frozen"][:16], "why": _short(c)}
                  for c in live],
        "sd": round(statistics.stdev(N), 4) if len(N) > 1 else 0.0,
        "sdc": round(sdc, 4),
        "win": len(cl),
        "fired_since_dry": fired,
        "no_late_ticker": noticker,
        "no_ticker_first": nt_when[0] if nt_when else "",
        "no_ticker_last": nt_when[-1] if nt_when else "",
        "no_ticker_coins": dict(sorted(nt_coins.items(), key=lambda kv: -kv[1])),
        "built": P.datetime.now(P.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# Short captions for the dashboard's rule filter. predictor.py's `why` is prose
# meant for the report; these are the rule in a few words. Kept here rather than
# added to CHALLENGERS so the collection-critical file stays untouched.
SHORT = {
    "C1": "Kalshi spread <= 1c",
    "C3": "|CVD| >= 0.50",
    "C4": "market-order share >= 0.15",
    "C5": "entry < 40c",
    "C6": "entry < 60c",
}


def _short(ch):
    """A few words naming the rule, for the filter caption."""
    if ch["name"] in SHORT:
        return SHORT[ch["name"]]
    why = ch.get("why", "")
    return (why.split(".")[0].strip() or ch["name"])[:40]


def build_dry(rows):
    """Summarise the Dry Run tab: what an order would actually have cost.

    The bets on the chart are priced at the mid quote captured when the signal
    fired. A real order crosses the spread, so the honest question is how far
    the fill sits from that mid. This reduces the tab to the few numbers that
    answer it; the per-order detail stays in the sheet.
    """
    if not rows or len(rows) < 2:
        return None
    idx = {h: i for i, h in enumerate(rows[0])}

    def cell(r, name):
        i = idx.get(name, -1)
        return r[i] if 0 <= i < len(r) else ""

    slips, fills, partial, failed, by_coin = [], 0, 0, 0, {}
    why = {}
    for r in rows[1:]:
        state = cell(r, "Fillable?")
        if state == "YES":
            fills += 1
        elif state == "PARTIAL":
            partial += 1
            why[_why(cell(r, "Note"))] = why.get(_why(cell(r, "Note")), 0) + 1
        else:
            failed += 1
            # Counting failures without their reasons said one in six orders
            # could not be priced and nothing about why -- which is the half of
            # the finding that would let us fix it. Bucket the note instead.
            k = _why(cell(r, "Note"))
            why[k] = why.get(k, 0) + 1
            continue
        try:
            s = float(cell(r, "Slippage ¢"))
        except (TypeError, ValueError):
            continue
        slips.append(s)
        by_coin.setdefault(cell(r, "Symbol").replace("USDT", ""), []).append(s)

    if not slips:
        return {"n": 0, "fills": fills, "partial": partial, "failed": failed,
                "why": why}

    slips.sort()
    mid = slips[len(slips) // 2]
    # Cost of slippage as a share of the stake: paying x cents more per
    # contract on a fixed $10 stake costs roughly stake * x / entry.
    return {
        "n": len(slips),
        "fills": fills, "partial": partial, "failed": failed,
        "mean": round(sum(slips) / len(slips), 3),
        "median": round(mid, 3),
        "p90": round(slips[min(len(slips) - 1, int(len(slips) * 0.9))], 3),
        "worst": round(slips[-1], 3),
        "coins": {k: round(sum(v) / len(v), 3) for k, v in sorted(by_coin.items())},
        "why": dict(sorted(why.items(), key=lambda kv: -kv[1])),
    }


# Free-text notes bucketed into the few causes that need different fixes. The
# raw note stays in the sheet; this is only what the dashboard needs to show.
def _why(note):
    n = (note or "").strip().lower()
    if not n:
        return "unexplained"
    if "no kalshi market" in n or "no ticker" in n:
        return "no market that window"
    if "ticker field was empty" in n:
        return "market found, ticker missing (bug)"
    if "no resting size" in n:
        return "empty book"
    if "http 404" in n:
        return "market not found (404)"
    if "http 4" in n or "http 5" in n or "book unavailable" in n:
        return "book fetch failed"
    if "only $" in n:
        return "not enough depth for $10"
    # These notes quote the price that triggered them, so the raw text makes a
    # separate bucket per cent -- "entry 51c...", "entry 52c...", thirteen
    # categories describing four reasons. Group by the RULE, not the number.
    if "cut" in n and ("entry" in n or "re-quoted" in n):
        return "too expensive (50¢ cut)"
    if "floor" in n:
        return "too cheap (20¢ floor)"
    if "never listed" in n or "not listed" in n:
        return "market never listed in time"
    if "filled nothing" in n or "partial" in n:
        return "order filled nothing"
    if "exchange index" in n:
        return "could not read the exchange index"
    if "down needs" in n or "ask-side mapping" in n:
        return "DOWN betting was switched off"
    return n[:40]


def _read_tab(client, title):
    """One tab's values, or None if it does not exist yet."""
    try:
        sh = client.open_by_key(P.SPREADSHEET_ID)
        return sh.worksheet(title).get_all_values()
    except Exception:
        return None


def _read_dry(client):
    """Dry Run tab if it exists. Absent before step 2 starts logging."""
    return _read_tab(client, "Dry Run")


def build_live(live_rows, pred_rows):
    """Real money: what was actually placed, and how it settled.

    The Live Bets tab records placements, not outcomes -- Kalshi settles the
    market, not us. So each placed order is joined back to the Predictions tab
    on (timestamp, symbol) for the result, and the P&L is computed from the
    contracts and the price actually paid. Unsettled bets are counted
    separately rather than assumed to win or lose.
    """
    if not live_rows or len(live_rows) < 2:
        return None
    li = {h: i for i, h in enumerate(live_rows[0])}

    def lc(r, k):
        i = li.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    # Outcome lookup from the collection tab, which grades every window.
    pi = {h: i for i, h in enumerate(P.ALL_HEADERS)}

    def pc(r, k):
        i = pi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    outcome = {}
    for r in pred_rows[1:]:
        res = pc(r, "CVD Correct?")
        if res in ("Yes", "No"):
            outcome[(pc(r, "Timestamp"), pc(r, "Symbol"))] = (res == "Yes")

    placed = skipped = failed = 0
    settled = wins = 0
    staked = pnl = 0.0
    open_stake = 0.0
    by_coin = {}
    skip_why = {}
    fail_why = {}
    # Every placed order, in order, so the real-money view can show the account
    # bet by bet rather than only in aggregate. Seven settled bets is a number
    # you read one row at a time; a summary tile hides which one went wrong.
    bets = []
    first = last = ""
    for r in live_rows[1:]:
        st = lc(r, "Status")
        if st == "SKIPPED":
            skipped += 1
            k = _why(lc(r, "Detail"))
            skip_why[k] = skip_why.get(k, 0) + 1
            continue
        if st == "FAILED":
            failed += 1
            k = _why(lc(r, "Detail"))
            fail_why[k] = fail_why.get(k, 0) + 1
            continue
        if st != "PLACED":
            continue

        placed += 1
        ts, sym = lc(r, "Timestamp"), lc(r, "Symbol")
        first = first or ts
        last = ts
        try:
            n = float(lc(r, "Contracts") or 0)
            cost = float(lc(r, "Cost $") or 0)
            limit_c = float(lc(r, "Limit ¢") or 0)
        except ValueError:
            continue
        staked += cost

        c = sym.replace("USDT", "")
        row = {"t": ts, "s": c, "d": lc(r, "Side"), "n": n,
               "cost": round(cost, 2), "e": round(limit_c, 1)}

        won = outcome.get((ts, sym))
        if won is None:
            open_stake += cost          # still running; no result yet
            row["open"] = 1
            bets.append(row)
            continue
        settled += 1
        wins += 1 if won else 0
        # Kalshi's fee is 7% of stake x (1 - price), charged at trade time.
        net = (n if won else 0.0) - cost - _fee(n, limit_c / 100.0)
        pnl += net
        row["w"] = 1 if won else 0
        row["net"] = round(net, 2)
        bets.append(row)
        b = by_coin.setdefault(c, {"n": 0, "w": 0, "net": 0.0})
        b["n"] += 1; b["w"] += 1 if won else 0; b["net"] += net

    return {
        "placed": placed, "skipped": skipped, "failed": failed,
        "settled": settled, "wins": wins,
        "staked": round(staked, 2), "pnl": round(pnl, 2),
        "open_stake": round(open_stake, 2),
        "first": first, "last": last,
        "coins": {k: {"n": v["n"], "w": v["w"], "net": round(v["net"], 2)}
                  for k, v in sorted(by_coin.items())},
        "skip_why": dict(sorted(skip_why.items(), key=lambda kv: -kv[1])),
        "fail_why": dict(sorted(fail_why.items(), key=lambda kv: -kv[1])),
        "bets": bets,
    }


def build_decay(decay_rows, pred_rows):
    """Re-price the strategy at the entry a live order could actually get.

    Every number on this dashboard is priced at the quote taken ~35 seconds
    into the window. A live order cannot reach Kalshi's order host until three
    to four minutes in, by which time the price has absorbed part of the move
    the signal is predicting. Those are two different bets and only one of them
    has ever been measured.

    The Entry Decay tab records both quotes for the same signal. Joining them
    to the graded outcome gives the same bets, same wins, two entry prices --
    so the difference in P&L is the cost of the delay and nothing else. That
    paired design matters: it is not two samples being compared, it is one
    sample priced twice, which removes luck from the comparison entirely.

    Reported at the $10 flat stake the rest of the dashboard uses.
    """
    if not decay_rows or len(decay_rows) < 2:
        return None
    di = {h: i for i, h in enumerate(decay_rows[0])}

    def dc(r, k):
        i = di.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    pi = {h: i for i, h in enumerate(P.ALL_HEADERS)}
    outcome = {}
    for r in pred_rows[1:]:
        res = r[pi["CVD Correct?"]] if pi.get("CVD Correct?", -1) < len(r) else ""
        if res in ("Yes", "No"):
            outcome[(r[pi["Timestamp"]], r[pi["Symbol"]])] = (res == "Yes")

    logged = quoted = 0
    drifts = []
    # Paired P&L, and the same pair grouped by window so the CI can account
    # for five coins in one window being one market event, not five.
    by_win = defaultdict(lambda: [0.0, 0.0, 0])
    n_pairs = wins = 0
    early_sum = late_sum = 0.0
    still_eligible = eligible = 0
    for r in decay_rows[1:]:
        logged += 1
        ts, sym = dc(r, "Timestamp"), dc(r, "Symbol")
        try:
            early = float(dc(r, "Entry @35s ¢"))
            late = float(dc(r, "Entry @4min ¢"))
        except ValueError:
            continue                      # the later quote never resolved
        if not (0 < early < 100 and 0 < late < 100):
            continue
        quoted += 1
        drifts.append(late - early)
        # Would the live rule still have taken this bet at the later price?
        if early < 50.0:
            eligible += 1
            if late < 50.0:
                still_eligible += 1

        won = outcome.get((ts, sym))
        if won is None:
            continue                      # window not graded yet
        n_pairs += 1
        wins += 1 if won else 0
        e, l = _pnl(early, won), _pnl(late, won)
        early_sum += e
        late_sum += l
        w = by_win[ts]
        w[0] += e; w[1] += l; w[2] += 1

    if not quoted:
        return {"logged": logged, "quoted": 0, "pairs": 0}

    # Clustered CI on the paired per-bet difference: one observation per
    # window, so a bad minute cannot pose as five independent data points.
    diff_ci = None
    if len(by_win) > 1:
        per_win = [(w[1] - w[0]) / w[2] for w in by_win.values()]
        m = statistics.mean(per_win)
        se = statistics.stdev(per_win) / math.sqrt(len(per_win))
        diff_ci = [round(m - 1.96 * se, 2), round(m + 1.96 * se, 2)]

    return {
        "logged": logged,
        "quoted": quoted,
        "pairs": n_pairs,
        "windows": len(by_win),
        "wins": wins,
        "drift_mean": round(statistics.mean(drifts), 2),
        "drift_med": round(statistics.median(drifts), 2),
        "drift_worse": sum(1 for d in drifts if d > 0),
        "eligible": eligible,
        "still_eligible": still_eligible,
        "ev_early": round(early_sum / n_pairs, 2) if n_pairs else None,
        "ev_late": round(late_sum / n_pairs, 2) if n_pairs else None,
        "pnl_early": round(early_sum, 2),
        "pnl_late": round(late_sum, 2),
        "diff_ci": diff_ci,
    }


def build_cashout(mid_rows, pred_rows):
    """Would selling early have beaten holding to settlement?

    Same bets, same outcomes, three exit choices -- hold, sell at +5 minutes,
    sell at +10 minutes -- so the comparison is paired and luck cancels.

    Both fees are charged. Entering costs a fee and so does selling, and a
    cash-out that ignored the second one would look better than it is by
    roughly the fee on every single trade. That is the whole question here, so
    it has to be right.

    Reported at the $10 flat stake the rest of the page uses.
    """
    if not mid_rows or len(mid_rows) < 2:
        return None
    mi = {h: i for i, h in enumerate(mid_rows[0])}

    def mc(r, k):
        i = mi.get(k, -1)
        return r[i] if 0 <= i < len(r) else ""

    pi = {h: i for i, h in enumerate(P.ALL_HEADERS)}
    outcome = {}
    for r in pred_rows[1:]:
        res = r[pi["CVD Correct?"]] if pi.get("CVD Correct?", -1) < len(r) else ""
        if res in ("Yes", "No"):
            outcome[(r[pi["Timestamp"]], r[pi["Symbol"]])] = (res == "Yes")

    logged = 0
    n = {"hold": 0, "m5": 0, "m10": 0}
    tot = {"hold": 0.0, "m5": 0.0, "m10": 0.0}
    by_win = defaultdict(lambda: {"hold": [0.0, 0], "m5": [0.0, 0], "m10": [0.0, 0]})
    buckets = defaultdict(lambda: {"n": 0, "hold": 0.0, "m5": 0.0, "m10": 0.0})
    spreads = []

    for r in mid_rows[1:]:
        logged += 1
        ts, sym = mc(r, "Timestamp"), mc(r, "Symbol")
        try:
            entry = float(mc(r, "Entry ¢"))
        except ValueError:
            continue
        if not (0 < entry < 100):
            continue
        won = outcome.get((ts, sym))
        if won is None:
            continue                       # not graded yet

        p = entry / 100.0
        contracts = 10.0 / p
        entry_fee = _fee(contracts, p)
        hold = (contracts if won else 0.0) - 10.0 - entry_fee
        n["hold"] += 1
        tot["hold"] += hold
        w = by_win[ts]
        w["hold"][0] += hold; w["hold"][1] += 1

        b = ("<40c" if entry < 40 else "40-50c" if entry < 50
             else "50-60c" if entry < 60 else ">=60c")
        buckets[b]["n"] += 1
        buckets[b]["hold"] += hold

        for key, col, scol in (("m5", "Exit @5m ¢", "Spread @5m"),
                               ("m10", "Exit @10m ¢", "Spread @10m")):
            try:
                ex = float(mc(r, col))
            except ValueError:
                continue
            if not (0 < ex < 100):
                continue
            # Sell the contracts back at the bid, and pay the fee again.
            net = contracts * (ex / 100.0) - 10.0 - entry_fee - _fee(contracts, ex / 100.0)
            n[key] += 1
            tot[key] += net
            w[key][0] += net; w[key][1] += 1
            buckets[b][key] += net
            try:
                spreads.append(float(mc(r, scol)))
            except ValueError:
                pass

    if not n["hold"]:
        return {"logged": logged, "graded": 0}

    def ev(k):
        return round(tot[k] / n[k], 2) if n[k] else None

    # Paired, clustered by window: five coins in one window are one event.
    def ci(k):
        pairs = [(v[k][0] / v[k][1] - v["hold"][0] / v["hold"][1])
                 for v in by_win.values() if v[k][1] and v["hold"][1]]
        if len(pairs) < 2:
            return None
        m = statistics.mean(pairs)
        se = statistics.stdev(pairs) / math.sqrt(len(pairs))
        return [round(m - 1.96 * se, 2), round(m + 1.96 * se, 2)]

    return {
        "logged": logged,
        "graded": n["hold"],
        "windows": len(by_win),
        "n5": n["m5"], "n10": n["m10"],
        "ev_hold": ev("hold"), "ev_5": ev("m5"), "ev_10": ev("m10"),
        "ci_5": ci("m5"), "ci_10": ci("m10"),
        "spread_med": round(statistics.median(spreads), 1) if spreads else None,
        "buckets": {k: {"n": v["n"], "hold": round(v["hold"], 2),
                        "m5": round(v["m5"], 2), "m10": round(v["m10"], 2)}
                    for k, v in sorted(buckets.items())},
    }


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else OUT_DEFAULT
    client = P._get_client()
    ws = P.open_pred_sheet(client)
    rows = ws.get_all_values()
    data = build(rows)
    # Never let the dry-run summary break the chart it rides along with.
    try:
        data["dry"] = build_dry(_read_dry(client))
    except Exception as e:
        print(f"  (dry-run summary skipped: {e})")
        data["dry"] = None
    # Same rule for the live summary: a problem here must leave the chart
    # standing rather than take the whole rebuild down with it.
    try:
        data["live"] = build_live(_read_tab(client, "Live Bets"), rows)
        if data["live"]:
            L = data["live"]
            print(f"  Live Bets: {L['placed']} placed, {L['settled']} settled, "
                  f"net ${L['pnl']:+.2f} on ${L['staked']:.2f} staked")
    except Exception as e:
        print(f"  (live summary skipped: {e})")
        data["live"] = None
    # And the entry-delay measurement. Same rule again -- this is the newest
    # of the three and the least proven, so it gets the least trust.
    try:
        data["decay"] = build_decay(_read_tab(client, "Entry Decay"), rows)
        if data["decay"]:
            E = data["decay"]
            print(f"  Entry Decay: {E['logged']} logged, {E['quoted']} quoted, "
                  f"{E.get('pairs', 0)} graded"
                  + (f", mean drift {E['drift_mean']:+.2f}c" if E['quoted'] else ""))
        else:
            print("  Entry Decay: tab empty or missing -- nothing measured yet")
    except Exception as e:
        print(f"  (decay summary skipped: {e})")
        data["decay"] = None
    # Cash-out: is Kalshi's early exit worth taking?
    try:
        data["cashout"] = build_cashout(_read_tab(client, "Mid Window"), rows)
        if data["cashout"]:
            C = data["cashout"]
            print(f"  Cash-out: {C['logged']} logged, {C.get('graded', 0)} graded"
                  + (f", hold {C['ev_hold']:+.2f} vs +5m {C['ev_5']:+.2f}"
                     if C.get('ev_5') is not None else ""))
        else:
            print("  Cash-out: tab empty or missing -- nothing sampled yet")
    except Exception as e:
        print(f"  (cash-out summary skipped: {e})")
        data["cashout"] = None
    import pathlib
    p = pathlib.Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("const D=" + json.dumps(data, separators=(",", ":")) + ";")
    print(f"  {out}: {len(data['t'])} bets, {data['win']} windows, "
          f"net ${data['q'][-1]:+.2f}, {len(data['rules'])} challengers")


if __name__ == "__main__":
    main()
